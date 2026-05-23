"""Pyright sidecar for code_intel v2 (2026-05-22).

Subprocess wrapper around the upstream ``pyright`` CLI. Surfaces
type-resolved diagnostics that the pure-Python AST indexer (``v1``)
can't compute — type-mismatched argument calls, unresolved-name
lookups, return-type violations, etc.

Failure-isolated end-to-end:

  * pyright binary not on PATH → :func:`is_available` returns False,
    :func:`check_paths` returns an empty :class:`PyrightReport` with
    ``available=False``.
  * Wallclock timeout exceeded → empty report with ``timed_out=True``.
  * Non-JSON stdout (pyright crashed) → empty report with the raw
    stderr packed into ``error``.
  * Master switch ``pyright_sidecar_enabled`` OFF → :func:`check_paths`
    short-circuits to an empty report with ``disabled=True``.

The sidecar never raises out to the caller — composing with the
iterate_until_green loop and coding-session submit hooks means a
broken pyright install can never block agent progress.

The sidecar does NOT modify the v1 AST indexer or its data model.
Callers that want both walk both — typical composition is "AST for
where-is-X-defined" + "pyright for is-X-type-correct."
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30.0

# Pyright JSON severity strings map to our three buckets:
_SEVERITY_MAP = {
    "error": "error",
    "warning": "warning",
    "information": "info",
    # Pyright also emits "hint" for inlay-hint-style suggestions; we
    # bucket those as info.
    "hint": "info",
}


@dataclass
class PyrightDiagnostic:
    file: str
    line: int  # 1-based
    column: int  # 1-based
    severity: str  # one of "error", "warning", "info"
    rule: str  # pyright rule name e.g. "reportGeneralTypeIssues"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PyrightReport:
    """Top-level result. ``has_errors`` is the load-bearing operator
    boolean — when True, the caller has a real type problem to fix.
    """

    diagnostics: list[PyrightDiagnostic] = field(default_factory=list)
    paths_checked: list[str] = field(default_factory=list)
    available: bool = True
    disabled: bool = False
    timed_out: bool = False
    duration_s: float = 0.0
    error: str = ""  # populated on subprocess failure modes
    # Phase 3 v2 follow-up (2026-05-22) — when the sidecar walked up
    # from the checked file and found a pyrightconfig.json or a
    # pyproject.toml [tool.pyright] block, this records the root
    # directory it used as cwd. Empty string when no config was
    # discovered (pyright ran with defaults).
    config_root: str = ""

    @property
    def errors(self) -> list[PyrightDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[PyrightDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["has_errors"] = self.has_errors
        d["error_count"] = len(self.errors)
        d["warning_count"] = len(self.warnings)
        return d


# Maximum directory levels to walk up looking for a pyright config.
# 8 covers nested monorepos comfortably; prevents accidentally walking
# all the way to the filesystem root on a misconfigured path.
_MAX_CONFIG_WALKUP = 8


def _discover_project_config(start_path: Path) -> Optional[Path]:
    """Walk up from ``start_path`` looking for a pyright config file.

    Recognized configs:
      * ``pyrightconfig.json``
      * ``pyproject.toml`` with a ``[tool.pyright]`` section

    Returns the directory containing the first match, or None when no
    config is found within ``_MAX_CONFIG_WALKUP`` levels of the
    starting path. The returned directory is the project root pyright
    expects to be invoked from.

    Failure-isolated: a sick filesystem / unreadable pyproject.toml
    returns None and the caller falls back to whatever cwd it had.
    """
    try:
        # Start from the path itself if it's a directory; otherwise
        # from its parent. Resolve symlinks so the walk terminates.
        cur = start_path.resolve()
        if cur.is_file():
            cur = cur.parent
    except Exception:
        return None

    for _ in range(_MAX_CONFIG_WALKUP):
        try:
            if (cur / "pyrightconfig.json").is_file():
                return cur
            pyproject = cur / "pyproject.toml"
            if pyproject.is_file():
                try:
                    contents = pyproject.read_text(encoding="utf-8")
                except Exception:
                    contents = ""
                # Lightweight scan — pyright itself does a proper TOML
                # parse, but we just need to know whether the project
                # has opted into pyright config here.
                if "[tool.pyright]" in contents or "[tool.pyright." in contents:
                    return cur
        except Exception:
            return None
        parent = cur.parent
        if parent == cur:
            # Reached filesystem root
            return None
        cur = parent
    return None


def is_available() -> bool:
    """True iff a ``pyright`` binary is on PATH.

    Cheap — only does a PATH lookup, doesn't invoke the binary. The
    actual binary is exercised only inside :func:`check_paths`.
    """
    return shutil.which("pyright") is not None


def _master_switch_on() -> bool:
    try:
        from app import runtime_settings
        return runtime_settings.get_pyright_sidecar_enabled()
    except Exception:
        # Failure-isolated: a sick runtime_settings defaults the switch
        # OFF so we never accidentally start spawning subprocesses.
        return False


def _parse_pyright_json(raw: str) -> tuple[list[PyrightDiagnostic], str]:
    """Parse pyright's --outputjson stdout. Returns (diagnostics, error).

    ``error`` is non-empty when the JSON wasn't well-formed or didn't
    match the expected shape — callers surface this to the operator
    instead of silently swallowing.
    """
    try:
        data = json.loads(raw)
    except Exception as exc:
        return [], f"pyright json parse: {exc}"

    diagnostics: list[PyrightDiagnostic] = []
    raw_diags = data.get("generalDiagnostics") or []
    if not isinstance(raw_diags, list):
        return [], "pyright json: generalDiagnostics not a list"

    for item in raw_diags:
        if not isinstance(item, dict):
            continue
        file_ = str(item.get("file", ""))
        # pyright uses 0-based line/column in the range object
        rng = item.get("range") or {}
        start = rng.get("start") or {}
        try:
            line = int(start.get("line", 0)) + 1
            col = int(start.get("character", 0)) + 1
        except (TypeError, ValueError):
            line = col = 1
        raw_sev = str(item.get("severity", "")).lower()
        severity = _SEVERITY_MAP.get(raw_sev, "info")
        rule = str(item.get("rule", "") or "")
        message = str(item.get("message", "") or "")
        diagnostics.append(
            PyrightDiagnostic(
                file=file_,
                line=line,
                column=col,
                severity=severity,
                rule=rule,
                message=message,
            )
        )
    return diagnostics, ""


def check_paths(
    paths: list[Path],
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    cwd: Optional[Path] = None,
) -> PyrightReport:
    """Run pyright over the given paths and return a structured report.

    ``paths`` may be files or directories. Each is passed verbatim to
    pyright; the CLI handles globbing + directory traversal.

    ``timeout_s`` is a wallclock cap — exceeded → empty report with
    ``timed_out=True``.

    ``cwd`` lets the caller pin the working directory (e.g. a coding-
    session worktree). When ``None``, pyright inherits the gateway's
    cwd, which is rarely what you want — pin it.
    """
    if not _master_switch_on():
        return PyrightReport(
            paths_checked=[str(p) for p in paths],
            disabled=True,
            available=is_available(),
        )

    if not is_available():
        return PyrightReport(
            paths_checked=[str(p) for p in paths],
            available=False,
        )

    if not paths:
        return PyrightReport(available=True)

    # Phase 3 v2 follow-up (2026-05-22) — pyrightconfig.json discovery.
    # When the caller pinned cwd we honor it; when cwd is None we walk
    # up from the first path looking for a project config. The result
    # is recorded on the report so operators can debug "why did pyright
    # use these rules?" via /cp/changes drawer.
    config_root: str = ""
    effective_cwd: Optional[Path] = cwd
    if effective_cwd is None and paths:
        discovered = _discover_project_config(paths[0])
        if discovered is not None:
            effective_cwd = discovered
            config_root = str(discovered)
    elif effective_cwd is not None:
        # Even when caller pinned cwd, surface the config root if one
        # exists at or above that directory — useful debugging context.
        try:
            discovered = _discover_project_config(effective_cwd)
            if discovered is not None:
                config_root = str(discovered)
        except Exception:
            pass

    argv = ["pyright", "--outputjson", *(str(p) for p in paths)]

    import time
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(effective_cwd) if effective_cwd is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return PyrightReport(
            paths_checked=[str(p) for p in paths],
            available=True,
            timed_out=True,
            duration_s=timeout_s,
        )
    except FileNotFoundError:
        # PATH lookup raced — pyright disappeared between is_available()
        # and the actual spawn. Treat as unavailable.
        return PyrightReport(
            paths_checked=[str(p) for p in paths],
            available=False,
        )
    except Exception as exc:
        logger.debug(
            "pyright_sidecar: subprocess failed", exc_info=True,
        )
        return PyrightReport(
            paths_checked=[str(p) for p in paths],
            available=True,
            error=f"subprocess: {exc}",
            duration_s=time.monotonic() - started,
        )

    duration = time.monotonic() - started
    diagnostics, parse_err = _parse_pyright_json(proc.stdout)
    err = parse_err
    if parse_err and proc.stderr:
        # Surface stderr too — when pyright crashes it usually puts
        # the real reason there.
        err = f"{parse_err} (stderr: {proc.stderr.strip()[:500]})"

    return PyrightReport(
        diagnostics=diagnostics,
        paths_checked=[str(p) for p in paths],
        available=True,
        duration_s=duration,
        error=err,
        config_root=config_root,
    )


def check_file(path: Path, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> PyrightReport:
    """Convenience wrapper for the single-file case.

    Passes ``cwd=None`` so the project-config discovery in
    :func:`check_paths` runs from the file's path. When a
    ``pyrightconfig.json`` or ``pyproject.toml [tool.pyright]`` lives
    above the file, pyright runs from the project root with the
    operator's rules in effect.
    """
    return check_paths([path], timeout_s=timeout_s, cwd=None)

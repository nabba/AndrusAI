"""D#a — Curated writer for ``pyproject.toml`` dependency pins.

Sibling to :mod:`requirements_writer` (P0#1a) — same safety envelope,
different target format. When :mod:`package_manager.detect_manager`
reports ``UV``/``POETRY``/``PDM``, the apply_hook dispatches here
instead of the requirements writer.

The writer touches exactly one of three declared dependency tables:

  * **PEP 621**: ``[project.dependencies]`` (uv's canonical, also
    accepted by pdm + poetry-2.0+). Array of PEP 508 strings.
  * **Poetry**: ``[tool.poetry.dependencies]``. Table with
    ``package = "version"`` or ``package = {version = "X", ...}``.
  * **PDM**: ``[tool.pdm.dependencies]``. Same shape as PEP 621.

Lock files are NOT touched by this writer — they're derived
artefacts that go stale the moment the dep spec changes. The
apply_hook surfaces a Signal alert pointing the operator at
``uv sync`` / ``poetry lock`` / ``pdm lock`` to regenerate the lock.

Safety envelope mirrors requirements_writer:

  * Caller in :data:`_ALLOWED_REQUESTORS`.
  * Package + version regex-validated.
  * AT MOST one matching dep entry in the file.
  * Single-line diff invariant (multi-line table entries get the
    version field bumped; other fields preserved).
  * Master switch :func:`runtime_settings.get_upgrade_lifecycle_pyproject_writer_enabled`
    must be True. Default OFF.

Stdlib-only: uses Python 3.11+ ``tomllib`` for read, raw
text-edit for write (preserves comments + formatting that a
tomllib-roundtrip would lose).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


_ALLOWED_REQUESTORS = frozenset({
    "dependency_radar",
    "upgrade_lifecycle",
    "ecosystem_snapshot",
})

_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9.\-]*)?$")


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    diff_lines: tuple[str, ...] = ()
    pyproject_path: str = ""
    table_section: str = ""    # "project.dependencies" | "tool.poetry.dependencies" | "tool.pdm.dependencies"
    lockfile_hint: str = ""    # e.g. "run `uv sync` to regenerate uv.lock"


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_pyproject_writer_enabled,
        )
        return get_upgrade_lifecycle_pyproject_writer_enabled()
    except Exception:
        return False


def _pyproject_path() -> Path:
    override = os.getenv("PYPROJECT_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "pyproject.toml"


def _normalize_name(name: str) -> str:
    return name.lower().replace("_", "-")


# ── PEP 508-ish requirement parser (narrow) ──────────────────────────────


# Captures: package extras specifier version markers
# Examples that should match:
#   "starlette==0.52.1"
#   "starlette>=0.52.1, <1.0"
#   "starlette[full]==0.52.1"
#   "starlette==0.52.1 ; python_version >= '3.11'"
_PEP508_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)"
    r"(?P<extras>\[[^\]]*\])?"
    r"\s*(?P<specifier>(?:[<>=!~]=?|==)\s*[A-Za-z0-9_.*+\-]+(?:\s*,\s*(?:[<>=!~]=?|==)\s*[A-Za-z0-9_.*+\-]+)*)?"
    r"(?P<marker>\s*;\s*.+)?$",
)


def _swap_pep508_version(req_str: str, *, new_version: str) -> Optional[str]:
    """Replace the version specifier in a PEP 508 string with ``==new_version``.

    Preserves extras + markers. Returns None if the string doesn't
    look like a PEP 508 requirement.
    """
    s = req_str.strip()
    m = _PEP508_RE.match(s)
    if not m:
        return None
    name = m.group("name")
    extras = m.group("extras") or ""
    marker = m.group("marker") or ""
    return f"{name}{extras}=={new_version}{marker}"


# ── Section matchers ─────────────────────────────────────────────────────


# Lines we're willing to bump. Each tuple is (section_header, format).
# ``format`` is "pep621_array" (project.dependencies) or
# "poetry_table" (tool.poetry.dependencies).
_SECTIONS = (
    ("[project]", "pep621_array"),
    ("[tool.poetry.dependencies]", "poetry_table"),
    ("[tool.pdm.dependencies]", "pep621_array"),   # PDM emits PEP 621 arrays
)


# ── Public API ───────────────────────────────────────────────────────────


def apply_bump(
    *,
    package: str,
    to_version: str,
    requestor: str,
    reason: str,
    pyproject_path: Optional[Path] = None,
    lockfile_hint: Optional[str] = None,
) -> WriteResult:
    """Bump *package* to *to_version* in pyproject.toml.

    Resolution order — checks each candidate section in turn, bumps
    the first match. Refuses if multiple sections mention the package
    (ambiguous) or none do (caller should fall back to appending —
    but this writer doesn't auto-append; the operator's CR is the
    place to add a brand new dep).
    """
    if not _enabled():
        return WriteResult(ok=False, reason="master_switch_off")

    if requestor not in _ALLOWED_REQUESTORS:
        return WriteResult(
            ok=False, reason=f"requestor_not_allowed:{requestor}",
        )

    if not _PACKAGE_NAME_RE.match(package or ""):
        return WriteResult(ok=False, reason="malformed_package_name")

    if not _VERSION_RE.match(to_version or ""):
        return WriteResult(ok=False, reason="malformed_version")

    path = pyproject_path or _pyproject_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return WriteResult(ok=False, reason=f"read_failed:{exc}")

    lines = text.splitlines(keepends=True)

    # Find every section, then try bumping per format.
    norm_target = _normalize_name(package)
    section_results: list[tuple[str, list[str], int]] = []   # (section, new_lines, line_idx)

    for section_header, fmt in _SECTIONS:
        section_idx = _find_section_start(lines, section_header)
        if section_idx is None:
            continue
        section_end = _find_section_end(lines, section_idx + 1)
        if fmt == "pep621_array":
            result = _bump_pep621_array(
                lines, section_idx + 1, section_end,
                package=norm_target, to_version=to_version,
            )
        elif fmt == "poetry_table":
            result = _bump_poetry_table(
                lines, section_idx + 1, section_end,
                package=norm_target, to_version=to_version,
            )
        else:   # unreachable
            continue
        if result is not None:
            new_lines, line_idx = result
            section_results.append((section_header.strip("[]"), new_lines, line_idx))

    if not section_results:
        return WriteResult(
            ok=False, reason="package_not_found",
            pyproject_path=str(path),
        )

    if len(section_results) > 1:
        # Multiple sections declare the same package — refuse rather
        # than guess.
        sections = ", ".join(s for s, _, _ in section_results)
        return WriteResult(
            ok=False, reason=f"ambiguous_multiple_sections:{sections}",
            pyproject_path=str(path),
        )

    section_name, new_lines, _ = section_results[0]
    new_text = "".join(new_lines)

    # Diff for audit.
    diff_lines = _diff_lines(text.splitlines(), new_text.splitlines())

    # Refuse if too many lines mutated — same safety net as
    # requirements_writer.
    if len(diff_lines) > 2:
        return WriteResult(
            ok=False, reason=f"diff_size_unexpected:{len(diff_lines)}",
            diff_lines=diff_lines,
            pyproject_path=str(path),
        )

    try:
        tmp = path.with_suffix(".toml.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return WriteResult(
            ok=False, reason=f"write_failed:{exc}",
            pyproject_path=str(path),
        )

    final_lockfile_hint = lockfile_hint or _default_lockfile_hint(
        path, section_name, package=package,
    )
    _emit_audit(
        package=package, to_version=to_version,
        requestor=requestor, reason=reason,
        diff=diff_lines, section=section_name,
        pyproject_path=str(path),
    )
    return WriteResult(
        ok=True, reason="ok",
        diff_lines=diff_lines,
        pyproject_path=str(path),
        table_section=section_name,
        lockfile_hint=final_lockfile_hint,
    )


# ── Section finders ─────────────────────────────────────────────────────


def _find_section_start(lines: list[str], header: str) -> Optional[int]:
    """Find the index of *header* in *lines* (exact match after strip)."""
    for i, raw in enumerate(lines):
        if raw.strip() == header:
            return i
    return None


def _find_section_end(lines: list[str], start_idx: int) -> int:
    """Find the index of the next ``[section]`` line at or after start_idx.

    The end of section is exclusive — the returned index points at the
    NEXT section header (or len(lines) if no next section).
    """
    for i in range(start_idx, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            return i
    return len(lines)


# ── PEP 621 (project.dependencies) bumper ────────────────────────────────


# Match a dependency array line like:  "starlette==0.52.1",
# Accepts leading comma/whitespace and trailing comma + optional comment.
_PEP621_ENTRY_RE = re.compile(
    r"^(?P<lead>\s*)\"(?P<spec>[^\"]+)\"\s*(?P<tail>,?\s*(?:#.*)?)$",
)


def _bump_pep621_array(
    lines: list[str], section_start: int, section_end: int,
    *, package: str, to_version: str,
) -> Optional[tuple[list[str], int]]:
    """Look for the ``dependencies = [...]`` (or ``[project.dependencies]``
    direct table) entry matching *package*, replace its specifier.

    Returns ``(new_lines, line_idx)`` or None on no match.
    """
    # Special case: [project] section needs a `dependencies = [` line
    # to scope the search. [tool.pdm.dependencies] is itself the array.
    new_lines = list(lines)
    in_deps_array = False
    deps_start_line: Optional[int] = None

    # Detect whether we're inside [project] or [tool.pdm.dependencies]
    # by reading the section header line just before section_start.
    header_line = lines[section_start - 1].strip() if section_start > 0 else ""

    if header_line == "[project]":
        # Find ``dependencies = [`` inside [project]
        for i in range(section_start, section_end):
            stripped = lines[i].strip()
            if re.match(r"^dependencies\s*=\s*\[", stripped):
                in_deps_array = True
                deps_start_line = i
                # If the [ opens, the array continues until ]
                continue
            if in_deps_array:
                if stripped.startswith("]"):
                    break   # array closes
                m = _PEP621_ENTRY_RE.match(lines[i])
                if not m:
                    continue
                spec = m.group("spec")
                # Parse the name out of the spec
                pn = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", spec)
                if not pn or _normalize_name(pn.group(1)) != package:
                    continue
                new_spec = _swap_pep508_version(spec, new_version=to_version)
                if new_spec is None:
                    return None
                # tail's ``\s*`` may have absorbed the trailing newline;
                # rstrip + re-append one \n so the replacement matches
                # the original line's structure exactly.
                tail = m.group("tail").rstrip()
                new_lines[i] = f"{m.group('lead')}\"{new_spec}\"{tail}\n"
                return new_lines, i
        return None

    # [tool.pdm.dependencies] — same array shape, but section itself
    # is the array (no `dependencies = [` wrapper).
    for i in range(section_start, section_end):
        m = _PEP621_ENTRY_RE.match(lines[i])
        if not m:
            continue
        spec = m.group("spec")
        pn = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", spec)
        if not pn or _normalize_name(pn.group(1)) != package:
            continue
        new_spec = _swap_pep508_version(spec, new_version=to_version)
        if new_spec is None:
            return None
        new_lines[i] = f"{m.group('lead')}\"{new_spec}\"{m.group('tail')}\n"
        return new_lines, i
    return None


# ── Poetry table bumper ──────────────────────────────────────────────────


# Match a poetry-style dep line: ``starlette = "^0.52.1"`` or
# ``starlette = { version = "^0.52.1", extras = [...] }``.
_POETRY_SIMPLE_RE = re.compile(
    r"^(?P<lead>\s*)(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)\s*=\s*"
    r"\"(?P<spec>[^\"]+)\"\s*(?P<tail>#.*)?$",
)
_POETRY_INLINE_TABLE_VERSION_RE = re.compile(
    r"version\s*=\s*\"([^\"]+)\"",
)


def _bump_poetry_table(
    lines: list[str], section_start: int, section_end: int,
    *, package: str, to_version: str,
) -> Optional[tuple[list[str], int]]:
    """Bump ``[tool.poetry.dependencies]`` ``package = "version"`` entry."""
    new_lines = list(lines)
    for i in range(section_start, section_end):
        raw = lines[i]
        m = _POETRY_SIMPLE_RE.match(raw)
        if m and _normalize_name(m.group("name")) == package:
            tail = m.group("tail") or ""
            tail_sep = f"  {tail}" if tail else ""
            new_lines[i] = (
                f"{m.group('lead')}{m.group('name')} = \"{to_version}\"{tail_sep}\n"
            )
            return new_lines, i
        # Inline-table form: ``starlette = { version = "X", ... }``.
        inline = re.match(
            r"^(?P<lead>\s*)(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)\s*=\s*\{",
            raw,
        )
        if inline and _normalize_name(inline.group("name")) == package:
            sub = _POETRY_INLINE_TABLE_VERSION_RE.sub(
                f'version = "{to_version}"', raw, count=1,
            )
            if sub != raw:
                new_lines[i] = sub
                return new_lines, i
    return None


# ── Diff + lockfile hints ────────────────────────────────────────────────


def _diff_lines(old: list[str], new: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for line in old:
        if line not in new:
            out.append(f"-{line}")
    for line in new:
        if line not in old:
            out.append(f"+{line}")
    return tuple(out)


def _default_lockfile_hint(
    pyproject_path: Path, section: str, *, package: str = "",
) -> str:
    """Suggest the right lock-regeneration command based on which
    section the bump landed in.

    A7-P1: the previous hints used commands that didn't actually
    apply a version change (``poetry lock --no-update`` re-locks
    current versions; ``pdm lock --update-reuse`` preserves existing
    pins). The current hints use the per-manager UPGRADE commands
    that actually pick up the new constraint, scoped to the single
    bumped package so adjacent deps aren't gratuitously refreshed.
    """
    repo = pyproject_path.parent
    pkg_arg = f" {package}" if package else ""
    if (repo / "uv.lock").exists():
        if package:
            return f"Run `uv lock --upgrade-package {package}` to refresh uv.lock."
        return "Run `uv lock` to refresh uv.lock."
    if (repo / "poetry.lock").exists() or section.startswith("tool.poetry"):
        if package:
            return f"Run `poetry update {package}` to refresh poetry.lock."
        return "Run `poetry update` to refresh poetry.lock."
    if (repo / "pdm.lock").exists() or section.startswith("tool.pdm"):
        if package:
            return f"Run `pdm update{pkg_arg}` to refresh pdm.lock."
        return "Run `pdm update` to refresh pdm.lock."
    return "Regenerate the manager's lockfile before next deploy."


def _emit_audit(
    *,
    package: str, to_version: str,
    requestor: str, reason: str, diff: tuple[str, ...],
    section: str, pyproject_path: str,
) -> None:
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="ecosystem_snapshot",
            actor="upgrade_lifecycle.pyproject_writer",
            summary=f"pyproject bump: {package} -> {to_version} ({section})",
            detail={
                "subkind": "pyproject_bump",
                "package": package,
                "to_version": to_version,
                "section": section,
                "requestor": requestor,
                "reason_excerpt": reason[:200],
                "diff_line_count": len(diff),
                "pyproject_path": pyproject_path,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.debug(
            "pyproject_writer: ledger emit failed", exc_info=True,
        )

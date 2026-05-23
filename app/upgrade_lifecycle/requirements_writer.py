"""P0#1a — Curated single-line writer for ``requirements.txt``.

The change-request validator restricts mutation to
``_ALLOWED_ROOT_PREFIXES`` (``app/``, ``tests/``, ``docs/``, …) and
``requirements.txt`` deliberately sits outside that set — broad
validator-allowance would expose every CR-routed operation to a path
the operator might not want them touching.

This module is the **narrow, curated exception**: a single primitive
that knows how to bump exactly one ``name==version`` line at a time,
refuses anything outside that envelope, is invokable only by the
upgrade-lifecycle subsystem, and emits a continuity-ledger event on
success so the operator can audit every bump after the fact.

It is NOT a general-purpose file writer. Adding a new operation here
should require a deliberate design pass and a Tier-3 amendment if the
operation isn't already trivially safe.

Safety envelope:

  * Caller (``requestor``) must be in :data:`_ALLOWED_REQUESTORS`.
  * ``package`` must match :data:`_PACKAGE_NAME_RE` (PEP 503 names).
  * ``to_version`` must match :data:`_VERSION_RE` (semver-ish — PyPI
    accepts more shapes but the common case is what we want).
  * Diff must change AT MOST one line (the package's pin), or
    APPEND ONE line if the package isn't currently pinned.
  * Master switch :func:`runtime_settings.get_upgrade_lifecycle_requirements_writer_enabled`
    must be True. Default OFF until the operator opts in via
    ``/cp/settings``.
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
    "dependency_radar",        # PATCH/MINOR/CVE bumps
    "upgrade_lifecycle",        # U4 MAJOR auto-CR after operator approval
    "ecosystem_snapshot",       # U6 operator-accepted MAJOR
})

# PEP 503 normalized package names (lower, [a-z0-9_.-]).
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Version shape: digit-dot-digit at minimum, with optional pre/post/dev
# suffixes. Permissive enough to handle PyPI's variations without
# allowing arbitrary text.
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9.\-]*)?$")

# Match a requirements.txt pin line, capturing prefix + name + separator.
_PIN_LINE_RE = re.compile(
    r"^(?P<prefix>\s*)(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)"
    r"(?P<sep>\s*(?:==|>=|<=|~=|>|<|!=)\s*)"
    r"(?P<rest>.*)$",
)


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    diff_lines: tuple[str, ...] = ()
    requirements_path: str = ""


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_requirements_writer_enabled,
        )
        return get_upgrade_lifecycle_requirements_writer_enabled()
    except Exception:
        return False   # default OFF on lookup failure (conservative)


def _requirements_path() -> Path:
    """Resolve ``requirements.txt`` relative to the gateway repo root.

    Honors ``REQUIREMENTS_PATH`` env override for tests.
    """
    override = os.getenv("REQUIREMENTS_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "requirements.txt"


def _normalize_name(name: str) -> str:
    """PEP 503: lower + hyphenate underscores."""
    return name.lower().replace("_", "-")


def _diff_lines(old: list[str], new: list[str]) -> tuple[str, ...]:
    """Tiny unified-diff-ish — used for audit and refusal-on-multi-line."""
    out: list[str] = []
    for line in old:
        if line not in new:
            out.append(f"-{line}")
    for line in new:
        if line not in old:
            out.append(f"+{line}")
    return tuple(out)


# ── Public API ───────────────────────────────────────────────────────────


def apply_bump(
    *,
    package: str,
    to_version: str,
    requestor: str,
    reason: str,
) -> WriteResult:
    """Bump (or append) one package pin in requirements.txt.

    Returns :class:`WriteResult` with ``ok=True`` on success, ``ok=False``
    + a machine-readable reason otherwise. Never raises. Emits the
    continuity-ledger event ``ecosystem_snapshot`` with
    ``subkind="requirements_bump"`` on success.
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

    path = _requirements_path()
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = ""
    except OSError as exc:
        return WriteResult(ok=False, reason=f"read_failed:{exc}")

    norm_target = _normalize_name(package)
    old_lines = text.splitlines()
    new_lines: list[str] = []
    found = False
    multi_match_refused = False
    for raw in old_lines:
        m = _PIN_LINE_RE.match(raw)
        if m and _normalize_name(m.group("name")) == norm_target:
            if found:
                # Two lines for the same package — refuse rather than
                # guess which one to bump.
                multi_match_refused = True
                new_lines.append(raw)
                continue
            new_lines.append(
                f"{m.group('prefix')}{m.group('name')}=={to_version}"
            )
            found = True
        else:
            new_lines.append(raw)

    if multi_match_refused:
        return WriteResult(
            ok=False, reason="multiple_pins_for_package",
        )

    if not found:
        new_lines.append(f"{package}=={to_version}")

    diff = _diff_lines(old_lines, new_lines)

    # Refuse if more than two diff lines mutated (1 add + 1 remove
    # for the bump case, OR 1 add for the append case). Anything
    # else means the regex matched non-pin syntax in a way we
    # didn't anticipate.
    if len(diff) > 2:
        return WriteResult(
            ok=False, reason=f"diff_size_unexpected:{len(diff)}",
            diff_lines=diff,
        )

    # Persist atomically.
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    try:
        tmp = path.with_suffix(".txt.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return WriteResult(ok=False, reason=f"write_failed:{exc}")

    _emit_audit(
        package=package, to_version=to_version,
        requestor=requestor, reason=reason, diff=diff,
    )

    return WriteResult(
        ok=True, reason="ok", diff_lines=diff,
        requirements_path=str(path),
    )


def _emit_audit(
    *,
    package: str, to_version: str,
    requestor: str, reason: str, diff: tuple[str, ...],
) -> None:
    """Best-effort identity-ledger emission.

    Failure-isolated so a ledger problem never blocks the write.
    """
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="ecosystem_snapshot",
            actor="upgrade_lifecycle.requirements_writer",
            summary=f"requirements bump: {package} -> {to_version} ({requestor})",
            detail={
                "subkind": "requirements_bump",
                "package": package,
                "to_version": to_version,
                "requestor": requestor,
                "reason_excerpt": reason[:200],
                "diff_line_count": len(diff),
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.debug("requirements_writer: ledger emit failed", exc_info=True)

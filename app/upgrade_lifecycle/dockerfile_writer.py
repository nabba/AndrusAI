"""P0#4 — Curated single-line writer for ``FROM python:`` in Dockerfile.

The validator-bypass justification is the same as
:mod:`requirements_writer` (P0#1a): the operation is narrow enough
that a general validator would either be over-permissive or
under-permissive. Here we operate on exactly one line per file: the
``FROM python:X.Y[.Z][-variant][@sha256:...]`` base-image directive.

SHA pin policy
==============

The current Dockerfile pins the image by digest:

    FROM python:3.13-slim@sha256:d168...

A naive tag-only bump (``3.13-slim`` → ``3.14-slim``) leaves the
``@sha256`` pin in place, so Docker pulls the OLD image regardless of
the tag. To make the bump effective, the writer **strips the SHA
suffix** and inserts a ``# TODO`` comment naming the file path of the
decision-CR + the new tag. The operator is expected to re-pin to a
verified digest before the next deploy.

The trade-off: a deliberate, time-bounded security regression
(unpinned image during the operator's re-pin window) in exchange
for the bump actually applying. The alternative — refusing
SHA-pinned files — leaves Python EOL transitions impossible.

Safety envelope
===============

  * Caller in :data:`_ALLOWED_REQUESTORS`.
  * Target version matches :data:`_VERSION_RE`.
  * Exactly one ``FROM python:`` line in the file (multi-stage with
    multiple Python variants is refused — operator must hand-edit).
  * Master switch :func:`runtime_settings.get_upgrade_lifecycle_dockerfile_writer_enabled`
    (default OFF).
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
    "upgrade_lifecycle",
    "ecosystem_snapshot",
})

# Python version: 3 numeric segments at most, optional pre/post.
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[a-zA-Z0-9.-]*)?$")

# Match the ``FROM python:X.Y[.Z][-variant]`` line, with optional
# ``@sha256:...`` digest pin and optional trailing whitespace/AS-clause.
# Examples that should match:
#   FROM python:3.13-slim
#   FROM python:3.13-slim@sha256:d168...
#   FROM python:3.11.5
#   FROM python:3.13-slim as builder
_FROM_PYTHON_RE = re.compile(
    r"^(?P<prefix>FROM\s+python:)"
    r"(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)"
    r"(?P<variant>[-a-zA-Z0-9.]*)"
    r"(?P<sha>@sha256:[0-9a-f]{64})?"
    r"(?P<trail>(?:\s+(?:AS|as)\s+\S+)?\s*)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    old_version: str = ""
    new_version: str = ""
    sha_pin_dropped: bool = False
    dockerfile_path: str = ""
    diff_lines: tuple[str, ...] = ()


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_dockerfile_writer_enabled,
        )
        return get_upgrade_lifecycle_dockerfile_writer_enabled()
    except Exception:
        return False


def _dockerfile_path() -> Path:
    """Resolve the default Dockerfile (main, not sandbox).

    Honors ``DOCKERFILE_PATH`` env override for tests.
    """
    override = os.getenv("DOCKERFILE_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "Dockerfile"


def _diff_lines(old: list[str], new: list[str]) -> tuple[str, ...]:
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
    to_version: str,
    requestor: str,
    reason: str,
    from_version: Optional[str] = None,
    dockerfile_path: Optional[Path] = None,
) -> WriteResult:
    """Bump the ``FROM python:`` directive in *dockerfile_path*.

    Args:
        to_version: new ``X.Y[.Z]`` Python version (no variant suffix —
            preserved from the existing line).
        requestor: must be in :data:`_ALLOWED_REQUESTORS`.
        reason: free-form audit reason (passed to the ledger event).
        from_version: optional sanity check — if supplied, the writer
            refuses unless the existing Dockerfile line matches this
            version. Catches stale CRs that were filed against a
            different baseline.
        dockerfile_path: override for tests. Defaults to repo root
            ``Dockerfile``.

    Returns :class:`WriteResult` with ``ok=True`` on success. Never raises.
    """
    if not _enabled():
        return WriteResult(ok=False, reason="master_switch_off")

    if requestor not in _ALLOWED_REQUESTORS:
        return WriteResult(
            ok=False, reason=f"requestor_not_allowed:{requestor}",
        )

    if not _VERSION_RE.match(to_version or ""):
        return WriteResult(ok=False, reason="malformed_version")

    path = dockerfile_path or _dockerfile_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return WriteResult(ok=False, reason=f"read_failed:{exc}")

    matches = list(_FROM_PYTHON_RE.finditer(text))
    if not matches:
        return WriteResult(ok=False, reason="no_python_from_line")

    # D#b (PROGRAM §63.10) — multi-stage Dockerfiles are common (one
    # FROM for builder + one for runtime). When ALL FROM python:
    # lines share the same Python minor version, we bump them in
    # lockstep. When they DIFFER, we refuse (operator must specify
    # which one — or hand-edit).
    existing_versions = {m.group("version") for m in matches}
    if len(existing_versions) > 1:
        return WriteResult(
            ok=False,
            reason=(
                "multiple_python_from_lines_different_versions:"
                + ",".join(sorted(existing_versions))
            ),
        )

    existing_version = existing_versions.pop()
    sha_seen = any(m.group("sha") for m in matches)

    if from_version is not None and from_version != existing_version:
        return WriteResult(
            ok=False,
            reason=f"baseline_mismatch:expected={from_version},found={existing_version}",
            old_version=existing_version,
        )

    if existing_version == to_version:
        return WriteResult(
            ok=True, reason="already_at_version",
            old_version=existing_version, new_version=to_version,
            dockerfile_path=str(path),
        )

    # Build the new text by replacing each FROM line in reverse order
    # (so earlier-line edits don't shift later-line offsets).
    new_text = text
    for m in reversed(matches):
        variant = m.group("variant") or ""
        sha = m.group("sha") or ""
        trail = m.group("trail") or ""
        new_line = f"FROM python:{to_version}{variant}{trail}".rstrip()
        if sha:
            new_line += (
                f"\n# TODO P0#4: re-pin Dockerfile SHA digest. Previous "
                f"line carried '{sha}' which would have anchored to the "
                f"OLD {existing_version} image. Operator: pull the new "
                f"image, capture its digest with `docker inspect "
                f"--format='{{{{.RepoDigests}}}}'`, replace this comment "
                f"+ the line above with `FROM python:{to_version}{variant}@sha256:<digest>`."
            )
        new_text = new_text[: m.start()] + new_line + new_text[m.end():]

    sha = "set" if sha_seen else ""

    # Persist atomically.
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return WriteResult(ok=False, reason=f"write_failed:{exc}")

    diff = _diff_lines(text.splitlines(), new_text.splitlines())

    _emit_audit(
        from_version=existing_version,
        to_version=to_version,
        sha_pin_dropped=bool(sha),
        requestor=requestor,
        reason=reason,
        diff=diff,
        dockerfile_path=str(path),
    )

    return WriteResult(
        ok=True, reason="ok",
        old_version=existing_version, new_version=to_version,
        sha_pin_dropped=bool(sha),
        dockerfile_path=str(path), diff_lines=diff,
    )


def _emit_audit(
    *,
    from_version: str, to_version: str,
    sha_pin_dropped: bool,
    requestor: str, reason: str,
    diff: tuple[str, ...],
    dockerfile_path: str,
) -> None:
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="ecosystem_snapshot",
            actor="upgrade_lifecycle.dockerfile_writer",
            summary=f"python bump: {from_version} -> {to_version}",
            detail={
                "subkind": "python_version_bump",
                "from_version": from_version,
                "to_version": to_version,
                "sha_pin_dropped": sha_pin_dropped,
                "requestor": requestor,
                "reason_excerpt": reason[:200],
                "diff_line_count": len(diff),
                "dockerfile_path": dockerfile_path,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.debug("dockerfile_writer: ledger emit failed", exc_info=True)

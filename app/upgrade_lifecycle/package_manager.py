"""P2#b — Package-manager detection.

PROGRAM §63.9 (P2 hardening). pip won't be the only Python package
manager forever — uv, rye, poetry, pdm are all viable; the
ecosystem may rotate again over a decade. This module detects which
manager a repo uses by probing for canonical lock/config files, so
the trial runner picks the right install invocation.

It does NOT (yet) implement bump-line-writing for non-pip managers
— ``requirements_writer.py`` still targets only ``requirements.txt``.
When a non-pip manager is detected, the trial runner adapts but the
writer logs a structured warning so the operator knows what to wire
next.

Detection precedence (most specific first):

  1. ``uv.lock`` → uv
  2. ``poetry.lock`` → poetry
  3. ``pdm.lock`` → pdm
  4. ``pyproject.toml`` (with no lockfile) → pip via PEP 517
  5. ``requirements.txt`` → pip
  6. (none) → pip (assumption — most defensible default)

Composes with — does not replace — ``trial_runner._default_pip_install``.
The trial runner consults :func:`detect_manager` and dispatches to
the right :func:`install_command` for the detected manager.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PackageManager(str, Enum):
    PIP = "pip"
    UV = "uv"
    POETRY = "poetry"
    PDM = "pdm"


@dataclass(frozen=True)
class DetectionResult:
    manager: PackageManager
    evidence_path: Optional[str]      # e.g. "uv.lock", "pyproject.toml"
    confidence: str                    # "explicit_lock" | "config_only" | "default"


# ── Detection ────────────────────────────────────────────────────────────


_LOCK_FILE_TO_MANAGER: tuple[tuple[str, PackageManager], ...] = (
    ("uv.lock", PackageManager.UV),
    ("poetry.lock", PackageManager.POETRY),
    ("pdm.lock", PackageManager.PDM),
)


def detect_manager(repo_root: Path) -> DetectionResult:
    """Inspect *repo_root* and decide which package manager to use.

    Lock files beat config files (a lock implies the manager has
    actually generated state). Among lock files, the precedence is
    the order in ``_LOCK_FILE_TO_MANAGER`` — first match wins. In
    practice a repo with multiple lockfiles is in transition, so
    flagging the order explicitly is the safest read.

    Falls back to pip as the default — defensible because pip is
    the universal baseline + the writer already supports it.
    """
    # Lock files
    for filename, manager in _LOCK_FILE_TO_MANAGER:
        if (repo_root / filename).exists():
            return DetectionResult(
                manager=manager,
                evidence_path=filename,
                confidence="explicit_lock",
            )

    # Config-only (no lockfile)
    if (repo_root / "pyproject.toml").exists():
        # pyproject.toml without a lock could be pip-via-PEP-517 or
        # a fresh uv/poetry/pdm project before lock is run.
        # Inspect [build-system] / [tool.*] hints if needed; for v1,
        # default to pip — same as bare requirements.txt repos.
        text = ""
        try:
            text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            pass
        if "[tool.poetry]" in text:
            return DetectionResult(
                manager=PackageManager.POETRY,
                evidence_path="pyproject.toml",
                confidence="config_only",
            )
        if "[tool.pdm]" in text:
            return DetectionResult(
                manager=PackageManager.PDM,
                evidence_path="pyproject.toml",
                confidence="config_only",
            )
        if "[tool.uv]" in text:
            return DetectionResult(
                manager=PackageManager.UV,
                evidence_path="pyproject.toml",
                confidence="config_only",
            )

    if (repo_root / "requirements.txt").exists():
        return DetectionResult(
            manager=PackageManager.PIP,
            evidence_path="requirements.txt",
            confidence="explicit_lock",
        )

    return DetectionResult(
        manager=PackageManager.PIP,
        evidence_path=None,
        confidence="default",
    )


# ── Install command shapes ────────────────────────────────────────────────


def install_command(
    manager: PackageManager,
    *,
    venv_python: Path,
    package: str,
    version: str,
    requirements_file: Optional[Path] = None,
) -> list[str]:
    """Build the install argv for *manager*.

    All paths route through the trial venv's interpreter so isolation
    is preserved regardless of manager.
    """
    if manager == PackageManager.PIP:
        if requirements_file and requirements_file.exists():
            return [
                str(venv_python), "-m", "pip", "install", "--quiet",
                "-r", str(requirements_file),
            ]
        return [
            str(venv_python), "-m", "pip", "install", "--quiet",
            f"{package}=={version}",
        ]

    if manager == PackageManager.UV:
        # uv ships its own CLI; we still drive via the venv to keep
        # PATH isolated. ``uv pip install`` accepts requirements.txt
        # and resolves much faster than pip.
        if requirements_file and requirements_file.exists():
            return [
                str(venv_python), "-m", "uv", "pip", "install",
                "-r", str(requirements_file),
            ]
        return [
            str(venv_python), "-m", "uv", "pip", "install",
            f"{package}=={version}",
        ]

    if manager == PackageManager.POETRY:
        # Poetry is harder to run inside a generic venv because the
        # project's deps come from pyproject.toml. For trial purposes,
        # we drive via ``pip install --upgrade`` of just the bumped
        # package — Poetry-managed envs accept this even though
        # `poetry update` is the canonical path.
        return [
            str(venv_python), "-m", "pip", "install",
            f"{package}=={version}",
        ]

    if manager == PackageManager.PDM:
        # PDM has a similar shape; same fallback.
        return [
            str(venv_python), "-m", "pip", "install",
            f"{package}=={version}",
        ]

    # Defensive default — should be unreachable thanks to the enum.
    return [
        str(venv_python), "-m", "pip", "install",
        f"{package}=={version}",
    ]


# ── Writer applicability ─────────────────────────────────────────────────


def writer_can_handle(detection: DetectionResult) -> bool:
    """True iff :mod:`requirements_writer` can mutate the lock for *detection*.

    Today the writer only mutates ``requirements.txt`` — when the
    operator switches to uv/poetry/pdm the writer's a no-op for the
    bump and the operator needs to wire a manager-specific writer.
    This predicate lets the orchestrator surface the gap *clearly*
    instead of silently no-op'ing.
    """
    return detection.manager == PackageManager.PIP and (
        detection.evidence_path == "requirements.txt"
        or detection.evidence_path is None
    )

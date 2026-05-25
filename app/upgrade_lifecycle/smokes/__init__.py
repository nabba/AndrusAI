"""Smoke runners for the U3 trial harness (Gap 2).

A smoke runner is a callable ``(sandbox_path) -> dict`` that exercises
behaviour the regular pytest pass cannot reach — typically real-data
read paths the unit-test suite stubs out.

Why a generic hook rather than chromadb-specific code in
``trial_runner``? Three reasons:

  1. ``trial_runner`` stays generic — package-specific knowledge lives
     in a separate module, easy to add a postgres smoke, an
     ABI-compatibility smoke, etc. without touching the trial core.
  2. Smokes are *append-only signals* in the trial result; they never
     block the trial's overall status. The MAJOR auto-CR gate or a
     future review step can read ``smoke_results`` and decide what to
     do with a smoke failure.
  3. Discovery is curated, not implicit. ``runners_for(package)``
     returns the runners associated with a package — empty by default
     so adding a smoke is an explicit operator/dev action, not a
     surprise pulled in by import-graph heuristics.

Each runner contract:

    def run(sandbox: pathlib.Path) -> dict:
        return {
            "name": "<runner-id>",
            "status": "ok" | "fail" | "error",
            "details": "<short string for operator>",
            # ... runner-specific extras
        }

The trial harness wraps the call in try/except — a runner that raises
gets recorded as ``status="error"`` automatically, never crashes the
trial.
"""
from __future__ import annotations

from typing import Callable, Iterable
from pathlib import Path


SmokeRunner = Callable[[Path], dict]


# Per-package smoke-runner registry. Operator/devs append-only; entries
# are added when a smoke is genuinely useful for that package's class of
# failure modes. Empty default = explicit opt-in only.
_REGISTRY: dict[str, tuple[SmokeRunner, ...]] = {}


def register(package: str, runner: SmokeRunner) -> None:
    """Attach a smoke runner to a package. Idempotent on (package, runner).

    Designed to be called at module-import time from the smoke-runner
    file itself — e.g., ``smokes/chromadb.py`` registers the chromadb
    smoke for the ``chromadb`` package when imported. The registry
    survives across calls because the module is cached.
    """
    key = package.lower()
    current = _REGISTRY.get(key, ())
    if runner in current:
        return
    _REGISTRY[key] = current + (runner,)


def runners_for(package: str) -> tuple[SmokeRunner, ...]:
    """Return registered smoke runners for *package*. Empty tuple when
    no runners are registered — the trial harness then skips the
    smoke phase entirely."""
    return _REGISTRY.get(package.lower(), ())


def clear_for_test() -> None:
    """Reset the registry — test-only hook so each test starts clean."""
    _REGISTRY.clear()


__all__ = ["SmokeRunner", "register", "runners_for", "clear_for_test"]

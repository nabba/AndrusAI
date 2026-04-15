"""
app.consciousness.belief_store — shim module.

The implementation moved to app.subia.belief.store as part of the
unified consciousness program Phase 1 migration (see PROGRAM.md).

This shim aliases itself to the backing module in sys.modules so that
every existing importer (personality/validation, self_awareness/
inspect_tools, test_consciousness_indicators) sees identical behavior.
New code should import from app.subia.belief.store directly.

Shim retained for one release cycle; then removed.
"""

from __future__ import annotations

import sys as _sys

import app.subia.belief.store as _target


import warnings as _warnings
_warnings.warn(
    f"{__name__} is a Phase-1 shim; import from app.subia.* directly. "
    "This shim will be removed once all call sites migrate (see PROGRAM.md).",
    DeprecationWarning, stacklevel=2,
)
_sys.modules[__name__] = _target

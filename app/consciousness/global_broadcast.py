"""
app.consciousness.global_broadcast — shim module.

The implementation moved to app.subia.scene.broadcast as part of the unified
consciousness program Phase 1 migration (see PROGRAM.md).

This shim aliases itself to the backing module in sys.modules so
existing importers see identical behavior. New code should import
from app.subia.scene.broadcast directly. Shim retained for one release cycle.
"""

from __future__ import annotations

import sys as _sys

import app.subia.scene.broadcast as _target


import warnings as _warnings
_warnings.warn(
    f"{__name__} is a Phase-1 shim; import from app.subia.* directly. "
    "This shim will be removed once all call sites migrate (see PROGRAM.md).",
    DeprecationWarning, stacklevel=2,
)
_sys.modules[__name__] = _target

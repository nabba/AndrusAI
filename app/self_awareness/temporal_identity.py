"""
app.self_awareness.temporal_identity — shim module.

The implementation moved to app.subia.self.temporal_identity as part of the unified
consciousness program Phase 1 migration (see PROGRAM.md).

This shim aliases itself to the backing module in sys.modules so
existing importers see identical behavior. New code should import
from app.subia.self.temporal_identity directly. Shim retained for one release cycle.
"""

from __future__ import annotations

import sys as _sys

import app.subia.self.temporal_identity as _target


import warnings as _warnings
_warnings.warn(
    f"{__name__} is a Phase-1 shim; import from app.subia.* directly. "
    "This shim will be removed once all call sites migrate (see PROGRAM.md).",
    DeprecationWarning, stacklevel=2,
)
_sys.modules[__name__] = _target

"""
app.self_awareness.precision_weighting — shim module.

The implementation moved to app.subia.prediction.precision_weighting as part of the unified
consciousness program Phase 1 migration (see PROGRAM.md).

This shim aliases itself to the backing module in sys.modules so
existing importers see identical behavior. New code should import
from app.subia.prediction.precision_weighting directly. Shim retained for one release cycle.
"""

from __future__ import annotations

import sys as _sys

import app.subia.prediction.precision_weighting as _target

_sys.modules[__name__] = _target

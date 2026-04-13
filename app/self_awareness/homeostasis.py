"""
app.self_awareness.homeostasis — shim module.

The implementation moved to app.subia.homeostasis.state as part of the unified
consciousness program Phase 1 migration (see PROGRAM.md).

This shim aliases itself to the backing module in sys.modules so
existing importers see identical behavior. New code should import
from app.subia.homeostasis.state directly. Shim retained for one release cycle.
"""

from __future__ import annotations

import sys as _sys

import app.subia.homeostasis.state as _target

_sys.modules[__name__] = _target

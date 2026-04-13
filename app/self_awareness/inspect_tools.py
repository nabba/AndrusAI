"""
app.self_awareness.inspect_tools — shim module.

The implementation moved to app.subia.tsal.inspect_tools as part of
the Phase 13 Technical Self-Awareness Layer consolidation (see
PROGRAM.md). Six prior Phase 1 self_awareness shim modules use the
same sys.modules aliasing pattern.

This shim aliases itself to the backing module in sys.modules so
existing importers see identical behavior. New code should import
from app.subia.tsal.inspect_tools (or simply `from app.subia.tsal
import ...`) directly. Shim retained for one release cycle.
"""

from __future__ import annotations

import sys as _sys

import app.subia.tsal.inspect_tools as _target

_sys.modules[__name__] = _target

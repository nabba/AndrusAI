"""
app.consciousness.workspace_buffer — shim module.

The implementation moved to app.subia.scene.buffer as part of the
unified consciousness program Phase 1 migration (see PROGRAM.md).

This shim aliases itself to the backing module in sys.modules, so:
  - Attribute reads from app.consciousness.workspace_buffer return the
    backing module's attributes.
  - Attribute writes (e.g. `wb._scorer = None` in tests) rebind on the
    same underlying module object.
  - Mutable singletons (`_gates` dict) are the same object in both paths.

This preserves exact behavior for the six existing importers
(orchestrator, workspace API, four test files). New code should import
from app.subia.scene.buffer directly.

Migration tracked in PROGRAM.md Phase 1; shim retained for one release.
"""

from __future__ import annotations

import sys as _sys

import app.subia.scene.buffer as _target

# Replace this module with the target. Any subsequent access to
# app.consciousness.workspace_buffer.X — including attribute rebinds —
# operates on the one shared module object.
_sys.modules[__name__] = _target

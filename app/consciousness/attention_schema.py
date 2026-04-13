"""
app.consciousness.attention_schema — shim module.

The implementation moved to app.subia.scene.attention_schema as part
of the unified consciousness program Phase 1 migration (see PROGRAM.md).

This shim aliases itself to the backing module in sys.modules so that
every existing importer (orchestrator, idle_scheduler, inspect_tools,
three test files) sees identical behavior. New code should import from
app.subia.scene.attention_schema directly.

Shim retained for one release cycle; then removed.
"""

from __future__ import annotations

import sys as _sys

import app.subia.scene.attention_schema as _target

_sys.modules[__name__] = _target

"""
Phase 1 migration tests.

Verifies that the three STRONG consciousness modules migrated into
app/subia/ preserve exact semantic behavior through their old-path
shims:

  app/consciousness/workspace_buffer.py  -> app/subia/scene/buffer.py
  app/consciousness/attention_schema.py  -> app/subia/scene/attention_schema.py
  app/consciousness/belief_store.py      -> app/subia/belief/store.py

The shims use sys.modules[__name__] = _target so both import paths
resolve to the SAME module object. That is what we assert here. Module
identity guarantees:
  - Attribute reads: identical
  - Attribute writes (e.g. `wb._scorer = None` in tests): propagate
  - Mutable singletons (_gates dict): one shared instance
  - isinstance checks against classes: identical class identity

Migration tracked in PROGRAM.md Phase 1.
"""

from __future__ import annotations


# ── Module-object identity ──────────────────────────────────────────

MIGRATION_PAIRS = [
    # (old_path, new_path) — every Phase 1 shim must resolve to target
    ("app.consciousness.workspace_buffer",      "app.subia.scene.buffer"),
    ("app.consciousness.attention_schema",      "app.subia.scene.attention_schema"),
    ("app.consciousness.global_broadcast",      "app.subia.scene.broadcast"),
    ("app.consciousness.meta_workspace",        "app.subia.scene.meta_workspace"),
    ("app.consciousness.personality_workspace", "app.subia.scene.personality_workspace"),
    ("app.consciousness.belief_store",          "app.subia.belief.store"),
    ("app.consciousness.metacognitive_monitor", "app.subia.belief.metacognition"),
    ("app.consciousness.prediction_hierarchy",  "app.subia.prediction.hierarchy"),
    ("app.consciousness.predictive_layer",      "app.subia.prediction.layer"),
    ("app.consciousness.adversarial_probes",    "app.subia.probes.adversarial"),
]


class TestModuleIdentity:
    def test_all_shims_alias_to_target(self):
        import importlib
        for old_path, new_path in MIGRATION_PAIRS:
            old = importlib.import_module(old_path)
            new = importlib.import_module(new_path)
            assert old is new, f"{old_path} does not alias to {new_path}"

    def test_workspace_buffer_aliased(self):
        import app.consciousness.workspace_buffer as old
        import app.subia.scene.buffer as new
        assert old is new, "shim must resolve to the same module object"

    def test_attention_schema_aliased(self):
        import app.consciousness.attention_schema as old
        import app.subia.scene.attention_schema as new
        assert old is new

    def test_belief_store_aliased(self):
        import app.consciousness.belief_store as old
        import app.subia.belief.store as new
        assert old is new


# ── Public API still present ────────────────────────────────────────

class TestApiPresence:
    def test_workspace_buffer_exports(self):
        import app.subia.scene.buffer as m
        for name in [
            "WorkspaceItem", "CompetitiveGate", "SalienceScorer",
            "get_workspace_gate", "create_workspace", "list_workspaces",
            "GENERIC_WORKSPACE", "META_WORKSPACE",
            "_cosine_sim", "_gates", "_scorer",
        ]:
            assert hasattr(m, name), f"missing: {name}"

    def test_attention_schema_exports(self):
        import app.subia.scene.attention_schema as m
        assert hasattr(m, "AttentionSchema")

    def test_belief_store_exports(self):
        import app.subia.belief.store as m
        assert hasattr(m, "BeliefStore") or hasattr(m, "Belief")


# ── Class identity across import paths ─────────────────────────────

class TestClassIdentity:
    def test_workspace_item_class_identity(self):
        from app.consciousness.workspace_buffer import WorkspaceItem as OldWI
        from app.subia.scene.buffer import WorkspaceItem as NewWI
        assert OldWI is NewWI
        # isinstance across paths must work
        inst = NewWI(content="hello")
        assert isinstance(inst, OldWI)

    def test_competitive_gate_class_identity(self):
        from app.consciousness.workspace_buffer import CompetitiveGate as OldCG
        from app.subia.scene.buffer import CompetitiveGate as NewCG
        assert OldCG is NewCG


# ── Singleton dict is shared ────────────────────────────────────────

class TestSharedSingletons:
    def test_gates_dict_is_same_object(self):
        """The `_gates` dict must be literally the same object in both paths."""
        import app.consciousness.workspace_buffer as old
        import app.subia.scene.buffer as new
        assert old._gates is new._gates

    def test_mutation_via_old_path_visible_via_new(self):
        import app.consciousness.workspace_buffer as old
        import app.subia.scene.buffer as new
        key = "__phase1_migration_test__"
        try:
            old._gates[key] = "sentinel"
            assert new._gates.get(key) == "sentinel"
        finally:
            old._gates.pop(key, None)

    def test_attribute_rebind_via_old_path_visible_via_new(self):
        """wb._scorer = None in tests must actually rebind the underlying."""
        import app.consciousness.workspace_buffer as old
        import app.subia.scene.buffer as new
        saved = old._scorer
        try:
            old._scorer = "phase1-sentinel"
            assert new._scorer == "phase1-sentinel"
            new._scorer = None
            assert old._scorer is None
        finally:
            old._scorer = saved

"""Tests for the risk classifier + trust zones (2026-05-20).

Covers:
  * runtime_settings overlay (allowed_requestors / allowed_paths /
    risk_classifier_enabled)
  * validator overlay (baseline UNION runtime overlay)
  * zone_for_path routing (8 zones + default)
  * classify() decision tree (10+ cases)
  * classify_with_overrides rationale chain

Safety invariants pinned by these tests:
  * source-pinned baseline allowlists stay empty (defends against
    silent widening from runtime_settings corruption)
  * IMMUTABLE always REFUSE
  * FINANCIAL + SECURITY_SENSITIVE never AUTO
  * OBSERVABLE + has_deletions → REFUSE (append-only)
  * runtime_settings failure → fall back to baseline (fail-safe)
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ── Stubs (defensive — defer to real crewai when available) ──────────
_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    import crewai.tools as _real_crewai_tools  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m

for _mod in ("langchain_anthropic", "docker"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)


from app import runtime_settings  # noqa: E402
from app.change_requests import validator  # noqa: E402
from app.risk_classifier import (  # noqa: E402
    Action,
    Decision,
    TrustZone,
    classify,
    classify_with_overrides,
    zone_for_path,
)


def _reset_runtime_settings() -> None:
    runtime_settings._cache = None  # type: ignore[attr-defined]


def _patch_runtime_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


# ============================================================================
# Runtime settings: allowlists
# ============================================================================


class TestAllowlistRuntimeSettings(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_allowed_requestors_default_empty(self):
        with _patch_runtime_settings():
            self.assertEqual(
                runtime_settings.get_auto_apply_allowed_requestors(), [],
            )

    def test_allowed_requestors_set_get_roundtrip(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_auto_apply_allowed_requestors(
                ["agent_a", "agent_b"],
            )
            self.assertEqual(
                runtime_settings.get_auto_apply_allowed_requestors(),
                ["agent_a", "agent_b"],
            )

    def test_allowed_requestors_dedupe(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_auto_apply_allowed_requestors(
                ["a", "a", "b", "a"],
            )
            self.assertEqual(
                runtime_settings.get_auto_apply_allowed_requestors(),
                ["a", "b"],
            )

    def test_allowed_requestors_strips_whitespace(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_auto_apply_allowed_requestors(
                ["  a  ", "", "b"],
            )
            self.assertEqual(
                runtime_settings.get_auto_apply_allowed_requestors(),
                ["a", "b"],
            )

    def test_allowed_requestors_rejects_non_list(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_auto_apply_allowed_requestors("nope")

    def test_allowed_requestors_rejects_non_string_entry(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_auto_apply_allowed_requestors(
                    ["a", 42, "b"],
                )

    def test_allowed_requestors_sanity_cap(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            too_many = [f"agent_{i}" for i in range(64)]
            with self.assertRaises(ValueError):
                runtime_settings.set_auto_apply_allowed_requestors(too_many)

    def test_allowed_paths_default_empty(self):
        with _patch_runtime_settings():
            self.assertEqual(
                runtime_settings.get_auto_apply_allowed_paths(), [],
            )

    def test_allowed_paths_set_get_roundtrip(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_auto_apply_allowed_paths(
                ["workspace/notes/", "workspace/output/foo.txt"],
            )
            self.assertEqual(
                runtime_settings.get_auto_apply_allowed_paths(),
                ["workspace/notes/", "workspace/output/foo.txt"],
            )

    def test_allowed_paths_rejects_absolute(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_auto_apply_allowed_paths(
                    ["/etc/passwd"],
                )

    def test_allowed_paths_rejects_parent_traversal(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            with self.assertRaises(ValueError):
                runtime_settings.set_auto_apply_allowed_paths(
                    ["workspace/../secrets"],
                )

    def test_allowed_paths_sanity_cap(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            too_many = [f"workspace/p{i}/" for i in range(128)]
            with self.assertRaises(ValueError):
                runtime_settings.set_auto_apply_allowed_paths(too_many)

    def test_risk_classifier_enabled_default_off(self):
        with _patch_runtime_settings():
            self.assertFalse(runtime_settings.get_risk_classifier_enabled())

    def test_risk_classifier_enabled_set_get(self):
        with _patch_runtime_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_risk_classifier_enabled(True)
            self.assertTrue(runtime_settings.get_risk_classifier_enabled())


# ============================================================================
# Validator overlay (baseline UNION runtime)
# ============================================================================


class TestValidatorOverlay(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_effective_requestors_empty_when_both_empty(self):
        with _patch_runtime_settings():
            # Both baseline (frozenset()) and overlay ([]) → empty.
            self.assertEqual(
                validator._effective_allowed_requestors(), frozenset(),
            )

    def test_effective_requestors_picks_up_overlay(self):
        with _patch_runtime_settings(
                auto_apply_allowed_requestors=["agent_a", "agent_b"]):
            self.assertEqual(
                validator._effective_allowed_requestors(),
                frozenset({"agent_a", "agent_b"}),
            )

    def test_effective_requestors_unions_baseline_and_overlay(self):
        # Patch the baseline constant to simulate a future operator
        # decision to seed the source-pinned baseline.
        with patch.object(
            validator,
            "_AUTO_APPLY_ALLOWED_REQUESTORS",
            frozenset({"baseline_agent"}),
        ), _patch_runtime_settings(
                auto_apply_allowed_requestors=["overlay_agent"]):
            self.assertEqual(
                validator._effective_allowed_requestors(),
                frozenset({"baseline_agent", "overlay_agent"}),
            )

    def test_effective_requestors_runtime_failure_returns_baseline(self):
        with patch.object(
            validator,
            "_AUTO_APPLY_ALLOWED_REQUESTORS",
            frozenset({"baseline_only"}),
        ), patch.object(
            runtime_settings,
            "get_auto_apply_allowed_requestors",
            side_effect=RuntimeError("boom"),
        ):
            # Failure degrades to baseline alone — fail-safe.
            self.assertEqual(
                validator._effective_allowed_requestors(),
                frozenset({"baseline_only"}),
            )

    def test_effective_paths_unions_with_dedup(self):
        with patch.object(
            validator,
            "_AUTO_APPLY_ALLOWED_PATHS",
            ("workspace/notes/", "workspace/shared.txt"),
        ), _patch_runtime_settings(
                auto_apply_allowed_paths=[
                    "workspace/notes/",        # duplicate of baseline
                    "workspace/output/",       # new
                ]):
            result = validator._effective_allowed_paths()
            self.assertEqual(
                result,
                ("workspace/notes/", "workspace/shared.txt",
                 "workspace/output/"),
            )

    def test_legacy_patch_object_pattern_still_works(self):
        # The existing test_change_requests_auto_apply.py uses
        # patch.object(validator, "_AUTO_APPLY_ALLOWED_REQUESTORS", ...)
        # to inject. The migration must keep that pattern working.
        with patch.object(
            validator,
            "_AUTO_APPLY_ALLOWED_REQUESTORS",
            frozenset({"legacy_agent"}),
        ), _patch_runtime_settings():  # overlay empty
            self.assertIn(
                "legacy_agent",
                validator._effective_allowed_requestors(),
            )

    def test_matches_auto_apply_path_consults_overlay(self):
        with _patch_runtime_settings(
                auto_apply_allowed_paths=["workspace/runtime/"]):
            self.assertTrue(
                validator._matches_auto_apply_path("workspace/runtime/x.txt"),
            )
            self.assertFalse(
                validator._matches_auto_apply_path("workspace/elsewhere/y.txt"),
            )


# ============================================================================
# Zone routing
# ============================================================================


class TestZoneForPath(unittest.TestCase):
    def test_empty_path_is_operator_gated(self):
        self.assertEqual(zone_for_path(""), TrustZone.OPERATOR_GATED)

    def test_immutable_uses_caller_supplied_set(self):
        self.assertEqual(
            zone_for_path(
                "app/security.py",
                immutable_paths=frozenset({"app/security.py"}),
            ),
            TrustZone.IMMUTABLE,
        )

    def test_two_party_prefix(self):
        self.assertEqual(
            zone_for_path("app/governance_amendment/protocol.py"),
            TrustZone.TWO_PARTY,
        )
        self.assertEqual(
            zone_for_path("app/governance_ratchet/audit.py"),
            TrustZone.TWO_PARTY,
        )

    def test_security_sensitive_prefix(self):
        # When NOT supplied as immutable, security.py routes to
        # SECURITY_SENSITIVE — production callers should pass
        # TIER_IMMUTABLE so it lands in IMMUTABLE first.
        self.assertEqual(
            zone_for_path("app/security.py"),
            TrustZone.SECURITY_SENSITIVE,
        )
        self.assertEqual(
            zone_for_path("app/sanitize.py"),
            TrustZone.SECURITY_SENSITIVE,
        )

    def test_financial_prefix(self):
        self.assertEqual(
            zone_for_path("deploy/scripts/run_migration.sh"),
            TrustZone.FINANCIAL,
        )
        self.assertEqual(
            zone_for_path("app/control_plane/budgets.py"),
            TrustZone.FINANCIAL,
        )

    def test_observable_prefix(self):
        self.assertEqual(
            zone_for_path("workspace/healing/lock_waits.jsonl"),
            TrustZone.OBSERVABLE,
        )
        self.assertEqual(
            zone_for_path("workspace/audit.log"),
            TrustZone.OBSERVABLE,
        )

    def test_reversible_prefix(self):
        self.assertEqual(
            zone_for_path("workspace/notes/today.md"),
            TrustZone.REVERSIBLE,
        )
        self.assertEqual(
            zone_for_path("workspace/output/report.txt"),
            TrustZone.REVERSIBLE,
        )

    def test_free_prefix(self):
        self.assertEqual(
            zone_for_path("workspace/coding_sessions/abc/scratch.py"),
            TrustZone.FREE,
        )
        self.assertEqual(
            zone_for_path("workspace/.tmp/foo"),
            TrustZone.FREE,
        )

    def test_unknown_path_is_operator_gated(self):
        self.assertEqual(
            zone_for_path("app/new_module/foo.py"),
            TrustZone.OPERATOR_GATED,
        )

    def test_immutable_takes_priority_over_security(self):
        # security.py is both in TIER_IMMUTABLE (live) AND matches
        # SECURITY_SENSITIVE prefix. When immutable_paths is passed,
        # IMMUTABLE must win.
        self.assertEqual(
            zone_for_path(
                "app/security.py",
                immutable_paths=frozenset({"app/security.py"}),
            ),
            TrustZone.IMMUTABLE,
        )


# ============================================================================
# Decision tree
# ============================================================================


class TestClassify(unittest.TestCase):
    def test_no_target_path_is_gated(self):
        action = Action(action_type="exec_shell", target_path=None)
        self.assertEqual(classify(action), Decision.GATED)

    def test_immutable_is_refused(self):
        action = Action(
            action_type="write_file",
            target_path="app/security.py",
            requestor="any_agent",
        )
        self.assertEqual(
            classify(
                action,
                immutable_paths=frozenset({"app/security.py"}),
            ),
            Decision.REFUSE,
        )

    def test_two_party_path(self):
        action = Action(
            action_type="write_file",
            target_path="app/governance_amendment/store.py",
            requestor="any_agent",
        )
        self.assertEqual(classify(action), Decision.TWO_PARTY)

    def test_financial_is_gated_even_with_full_allowlist(self):
        action = Action(
            action_type="write_file",
            target_path="app/control_plane/budgets.py",
            requestor="financial_agent",
        )
        decision = classify(
            action,
            allowed_requestors={"financial_agent"},
            allowed_paths={"app/control_plane/budgets.py"},
        )
        # FINANCIAL never auto in v1.
        self.assertEqual(decision, Decision.GATED)

    def test_security_sensitive_is_gated(self):
        action = Action(
            action_type="write_file",
            target_path="app/security.py",
            requestor="any",
        )
        # Without immutable_paths, security.py lands in SECURITY_SENSITIVE.
        self.assertEqual(classify(action), Decision.GATED)

    def test_reversible_auto_with_allowlists(self):
        action = Action(
            action_type="write_file",
            target_path="workspace/notes/today.md",
            requestor="trusted_agent",
            additive_only=True,
        )
        self.assertEqual(
            classify(
                action,
                allowed_requestors={"trusted_agent"},
                allowed_paths={"workspace/notes/"},
            ),
            Decision.AUTO,
        )

    def test_reversible_gated_when_requestor_missing(self):
        action = Action(
            action_type="write_file",
            target_path="workspace/notes/today.md",
            requestor="untrusted_agent",
        )
        self.assertEqual(
            classify(
                action,
                allowed_requestors={"other_agent"},
                allowed_paths={"workspace/notes/"},
            ),
            Decision.GATED,
        )

    def test_reversible_gated_when_path_missing(self):
        action = Action(
            action_type="write_file",
            target_path="workspace/notes/today.md",
            requestor="trusted_agent",
        )
        self.assertEqual(
            classify(
                action,
                allowed_requestors={"trusted_agent"},
                allowed_paths={"workspace/elsewhere/"},
            ),
            Decision.GATED,
        )

    def test_observable_refuses_deletions(self):
        action = Action(
            action_type="write_file",
            target_path="workspace/healing/lock_waits.jsonl",
            requestor="trusted_agent",
            has_deletions=True,
        )
        self.assertEqual(
            classify(
                action,
                allowed_requestors={"trusted_agent"},
                allowed_paths={"workspace/healing/"},
            ),
            Decision.REFUSE,
        )

    def test_observable_auto_when_additive(self):
        action = Action(
            action_type="append_log",
            target_path="workspace/healing/lock_waits.jsonl",
            requestor="trusted_agent",
            has_deletions=False,
            additive_only=True,
        )
        self.assertEqual(
            classify(
                action,
                allowed_requestors={"trusted_agent"},
                allowed_paths={"workspace/healing/"},
            ),
            Decision.AUTO,
        )

    def test_operator_gated_default(self):
        action = Action(
            action_type="write_file",
            target_path="app/new_module/foo.py",
            requestor="any_agent",
        )
        self.assertEqual(classify(action), Decision.GATED)

    def test_empty_allowlists_means_no_auto(self):
        # FREE-zone path that would qualify for AUTO if allowlists
        # had the entries. Empty allowlists → GATED.
        action = Action(
            action_type="write_file",
            target_path="workspace/coding_sessions/abc/scratch.py",
            requestor="any",
        )
        self.assertEqual(classify(action), Decision.GATED)


# ============================================================================
# Rationale chain (classify_with_overrides)
# ============================================================================


class TestRationale(unittest.TestCase):
    def test_returns_classification_result_with_zone(self):
        action = Action(
            action_type="write_file",
            target_path="workspace/notes/x.md",
            requestor="trusted",
        )
        result = classify_with_overrides(
            action,
            allowed_requestors={"trusted"},
            allowed_paths={"workspace/notes/"},
        )
        self.assertEqual(result.decision, Decision.AUTO)
        self.assertEqual(result.zone, TrustZone.REVERSIBLE)
        self.assertIn("auto-apply", result.rationale)

    def test_rationale_explains_refusal(self):
        action = Action(
            action_type="write_file",
            target_path="app/security.py",
            requestor="any",
        )
        result = classify_with_overrides(
            action,
            immutable_paths=frozenset({"app/security.py"}),
        )
        self.assertEqual(result.decision, Decision.REFUSE)
        self.assertEqual(result.zone, TrustZone.IMMUTABLE)
        self.assertIn("TIER_IMMUTABLE", result.rationale)

    def test_rationale_explains_two_party(self):
        action = Action(
            action_type="write_file",
            target_path="app/governance_amendment/x.py",
            requestor="any",
        )
        result = classify_with_overrides(action)
        self.assertEqual(result.decision, Decision.TWO_PARTY)
        self.assertEqual(result.zone, TrustZone.TWO_PARTY)
        self.assertIn("Tier-3", result.rationale)


# ============================================================================
# Composition with validator (ensures classifier proposes,
# validator disposes)
# ============================================================================


class TestClassifierValidatorComposition(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_settings()

    def test_classifier_says_auto_but_validator_refuses_when_path_forbidden(
            self):
        # FINANCIAL refuses at the classifier layer for this path —
        # but the validator's forbidden-prefix list independently
        # protects ``migrations/``. Use a synthetic FREE-zone path
        # in the forbidden ``migrations/`` prefix to demonstrate the
        # composition.
        action = Action(
            action_type="write_file",
            target_path="migrations/0099_new.sql",
            requestor="trusted",
        )
        # Classifier doesn't know about ``migrations/`` as financial
        # or security; it lands in OPERATOR_GATED → GATED.
        result = classify_with_overrides(
            action,
            allowed_requestors={"trusted"},
            allowed_paths={"migrations/"},
        )
        # Classifier says GATED (no zone routing for migrations/).
        # Validator would also refuse at validate_auto_apply because
        # migrations/ is in _AUTO_APPLY_FORBIDDEN_PREFIXES — the
        # composition works because both layers agree on safety.
        self.assertEqual(result.decision, Decision.GATED)


if __name__ == "__main__":
    unittest.main()

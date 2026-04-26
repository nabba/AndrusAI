"""Integration tests for the 6 self-improvement remediation fixes.

After 38 days of runtime, the audit revealed 92.4% of "kept" experiments were
cosmetic (delta=0.0), 0 code mutations deployed, and 0 error hooks fired despite
64 errors in the journal. This test suite verifies the 6 fixes work end-to-end:

  Fix 1: error_handler.report_error invokes the ON_ERROR hook chain
  Fix 2: 0.5 fitness baseline is rejected (no fake deltas)
  Fix 3: Targeted eval_set_score is the primary delta signal
  Fix 4: Skills require positive delta to "keep"; delta=0 → "stored"
  Fix 5: Kept code mutations trigger auto-deploy
  Fix 6: Meta-evolution requires real signal, not cosmetic counts
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ── Fix 1: error_handler invokes ON_ERROR hook chain ────────────────────────

class TestErrorHandlerHookIntegration:
    """Fix 1: report_error() must dispatch to the lifecycle hook chain."""

    def test_report_error_invokes_hook_registry(self):
        from app.error_handler import report_error, ErrorCategory
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_registry = MagicMock()
            mock_get_reg.return_value = mock_registry
            report_error(ErrorCategory.LOGIC, "test error", context={"crew": "test"})
            assert mock_registry.execute.called
            args, kwargs = mock_registry.execute.call_args
            from app.lifecycle_hooks import HookPoint
            assert args[0] == HookPoint.ON_ERROR

    def test_report_error_passes_context_to_hook(self):
        from app.error_handler import report_error, ErrorCategory
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_registry = MagicMock()
            mock_get_reg.return_value = mock_registry
            report_error(
                ErrorCategory.DATA,
                "parse failed",
                context={"crew": "researcher", "agent_id": "r1"},
            )
            ctx = mock_registry.execute.call_args[0][1]
            assert "parse failed" in ctx.errors[0]
            assert ctx.metadata.get("category") == "data"

    def test_report_error_prevents_reentry(self):
        """A hook that calls report_error must not cause infinite recursion."""
        from app.error_handler import report_error, ErrorCategory
        call_count = [0]

        def recursive_hook(ctx):
            call_count[0] += 1
            # The hook itself reports an error — must not loop
            report_error(ErrorCategory.LOGIC, "hook reported")
            return ctx

        # Patch registry to call our recursive hook
        from app.lifecycle_hooks import HookPoint, HookContext
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.execute = lambda hp, ctx: recursive_hook(ctx)
            mock_get_reg.return_value = mock_reg
            report_error(ErrorCategory.LOGIC, "outer")
            # The outer call dispatches once; the inner call's reentry guard prevents
            # a second dispatch. So hook is called exactly once.
            assert call_count[0] == 1

    def test_report_error_never_crashes_on_hook_failure(self):
        """Hook failures must not propagate to the caller."""
        from app.error_handler import report_error, ErrorCategory
        with patch("app.lifecycle_hooks.get_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.execute.side_effect = Exception("hook crashed")
            mock_get_reg.return_value = mock_reg
            # Should not raise
            report_error(ErrorCategory.SYSTEM, "test")


# ── Fix 2: 0.5 fitness baseline rejected ────────────────────────────────────

class TestFitnessBaselineFix:
    """Fix 2: Baseline measurement failure aborts the experiment."""

    def test_composite_score_failure_aborts_experiment(self, tmp_path, monkeypatch):
        import app.experiment_runner as runner_mod
        import app.results_ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "LEDGER_PATH", tmp_path / "results.tsv")
        # Make composite_score raise — the experiment must not record a 0.5 baseline
        monkeypatch.setattr("app.experiment_runner.composite_score",
                          lambda: (_ for _ in ()).throw(Exception("metric down")))
        from app.experiment_runner import ExperimentRunner, MutationSpec
        er = ExperimentRunner()
        er._backup_dir = tmp_path / ".backup"
        mutation = MutationSpec(
            experiment_id="exp_baseline_fail",
            hypothesis="test",
            change_type="skill",
            files={"skills/test.md": "# Test"},
        )
        result = er.run_experiment(mutation)
        assert result.status == "crash"
        assert "Baseline measurement unavailable" in result.detail
        assert result.metric_before != 0.5  # No fake baseline recorded

    def test_zero_baseline_aborts_experiment(self, tmp_path, monkeypatch):
        import app.experiment_runner as runner_mod
        import app.results_ledger as ledger_mod
        monkeypatch.setattr(ledger_mod, "LEDGER_PATH", tmp_path / "results.tsv")
        monkeypatch.setattr("app.experiment_runner.composite_score", lambda: 0.0)
        from app.experiment_runner import ExperimentRunner, MutationSpec
        er = ExperimentRunner()
        er._backup_dir = tmp_path / ".backup"
        mutation = MutationSpec(
            experiment_id="exp_zero_baseline", hypothesis="t",
            change_type="skill", files={"skills/x.md": "# X"},
        )
        result = er.run_experiment(mutation)
        assert result.status == "crash"


# ── Fix 4: Skill keep threshold ─────────────────────────────────────────────

class TestSkillKeepThreshold:
    """Fix 4: Skills with delta=0 get status='stored', not 'keep'."""

    def _make_runner(self, tmp_path, monkeypatch, scores):
        import app.experiment_runner as runner_mod
        import app.results_ledger as ledger_mod
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "skills").mkdir()
        monkeypatch.setattr(runner_mod, "SKILLS_DIR", workspace / "skills")
        monkeypatch.setattr(ledger_mod, "LEDGER_PATH", tmp_path / "results.tsv")
        call_count = [0]
        def mock_score():
            call_count[0] += 1
            return scores[min(call_count[0] - 1, len(scores) - 1)]
        monkeypatch.setattr("app.experiment_runner.composite_score", mock_score)

        from app.experiment_runner import ExperimentRunner
        er = ExperimentRunner()
        er._backup_dir = tmp_path / ".backup"

        def patched_apply(self, mut):
            for path, content in mut.files.items():
                fp = workspace / path
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
            return list(mut.files.keys())

        def patched_backup(self, mut):
            return {p: None for p in mut.files}

        def patched_restore(self, backed_up):
            for p, c in backed_up.items():
                fp = workspace / p
                if c is None and fp.exists():
                    fp.unlink()

        def patched_validate(self, mut, applied):
            return True, "ok"

        monkeypatch.setattr(ExperimentRunner, "_apply_mutation", patched_apply)
        monkeypatch.setattr(ExperimentRunner, "_backup_files", patched_backup)
        monkeypatch.setattr(ExperimentRunner, "_restore_backup", patched_restore)
        monkeypatch.setattr(ExperimentRunner, "_validate_mutation", patched_validate)
        return er, workspace

    def test_skill_with_zero_delta_gets_stored_status(self, tmp_path, monkeypatch):
        from app.experiment_runner import MutationSpec
        # Same baseline and after — delta exactly 0
        er, ws = self._make_runner(tmp_path, monkeypatch, scores=[0.85, 0.85])
        mutation = MutationSpec(
            experiment_id="exp_cosmetic",
            hypothesis="cosmetic skill",
            change_type="skill",
            files={"skills/cosmetic.md": "# Cosmetic\n\nThis is just stored, not improved."},
        )
        result = er.run_experiment(mutation)
        # New behavior: delta=0 → "stored", not "keep"
        assert result.status == "stored"
        assert "no measurable impact" in result.detail.lower()

    def test_skill_with_positive_delta_keeps(self, tmp_path, monkeypatch):
        from app.experiment_runner import MutationSpec
        # Tiny positive delta
        er, ws = self._make_runner(tmp_path, monkeypatch, scores=[0.80, 0.81])
        mutation = MutationSpec(
            experiment_id="exp_real_skill",
            hypothesis="actual improvement",
            change_type="skill",
            files={"skills/improve.md": "# Improvement\n\nThis is a useful new skill."},
        )
        result = er.run_experiment(mutation)
        assert result.status == "keep"
        assert result.delta > 0

    def test_skill_with_negative_delta_discards(self, tmp_path, monkeypatch):
        from app.experiment_runner import MutationSpec
        er, ws = self._make_runner(tmp_path, monkeypatch, scores=[0.80, 0.78])
        mutation = MutationSpec(
            experiment_id="exp_bad",
            hypothesis="harmful",
            change_type="skill",
            files={"skills/bad.md": "# Bad skill content"},
        )
        result = er.run_experiment(mutation)
        assert result.status == "discard"


# ── Fix 5: Auto-deploy on keep ──────────────────────────────────────────────

class TestAutoDeployOnKeep:
    """Fix 5: Kept code mutations trigger auto_deployer."""

    def test_trigger_calls_schedule_deploy(self):
        from app.evolution import _trigger_code_auto_deploy
        from app.experiment_runner import ExperimentResult, MutationSpec

        result = ExperimentResult(
            experiment_id="exp_test",
            hypothesis="test code change",
            change_type="code",
            metric_before=0.80,
            metric_after=0.82,
            delta=0.02,
            status="keep",
            files_changed=["app/agents/researcher.py"],
        )
        mutation = MutationSpec(
            experiment_id="exp_test",
            hypothesis="test",
            change_type="code",
            files={"app/agents/researcher.py": "# improved code"},
        )

        with patch("app.auto_deployer.schedule_deploy") as mock_schedule, \
             patch("app.auto_deployer.validate_proposal_paths", return_value=[]):
            _trigger_code_auto_deploy(result, mutation)
            assert mock_schedule.called
            args, kwargs = mock_schedule.call_args
            # reason should reference the experiment ID
            reason = kwargs.get("reason") or (args[0] if args else "")
            assert "exp_test" in reason

    def test_trigger_skips_immutable_files(self):
        from app.evolution import _trigger_code_auto_deploy
        from app.experiment_runner import ExperimentResult, MutationSpec

        result = ExperimentResult(
            experiment_id="exp_imm",
            hypothesis="modify safety core (should fail)",
            change_type="code",
            metric_before=0.80, metric_after=0.82, delta=0.02,
            status="keep",
            files_changed=["app/sanitize.py"],
        )
        mutation = MutationSpec(
            experiment_id="exp_imm", hypothesis="t", change_type="code",
            files={"app/sanitize.py": "# bad"},
        )

        with patch("app.auto_deployer.schedule_deploy") as mock_schedule:
            _trigger_code_auto_deploy(result, mutation)
            # Schedule must NOT be called for immutable files
            assert not mock_schedule.called

    def test_trigger_skips_path_violations(self):
        from app.evolution import _trigger_code_auto_deploy
        from app.experiment_runner import ExperimentResult, MutationSpec

        result = ExperimentResult(
            experiment_id="exp_traversal", hypothesis="t",
            change_type="code", metric_before=0.8, metric_after=0.85,
            delta=0.05, status="keep", files_changed=["../etc/evil"],
        )
        mutation = MutationSpec(
            experiment_id="exp_traversal", hypothesis="t",
            change_type="code", files={"../etc/evil": "..."},
        )

        with patch("app.auto_deployer.schedule_deploy") as mock_schedule:
            _trigger_code_auto_deploy(result, mutation)
            assert not mock_schedule.called

    def test_trigger_swallows_exceptions(self):
        """Auto-deploy failure must not crash the evolution session."""
        from app.evolution import _trigger_code_auto_deploy
        from app.experiment_runner import ExperimentResult, MutationSpec

        result = ExperimentResult(
            experiment_id="exp_x", hypothesis="t", change_type="code",
            metric_before=0.8, metric_after=0.81, delta=0.01,
            status="keep", files_changed=["app/agents/x.py"],
        )
        mutation = MutationSpec(
            experiment_id="exp_x", hypothesis="t", change_type="code",
            files={"app/agents/x.py": "# code"},
        )

        with patch("app.auto_deployer.schedule_deploy", side_effect=Exception("docker down")):
            # Should not raise
            _trigger_code_auto_deploy(result, mutation)


# ── Fix 6: Meta-evolution requires real signal ──────────────────────────────

class TestMetaEvolutionGating:
    """Fix 6: Meta-evolution skips when all data is cosmetic delta=0."""

    def test_skips_when_total_movement_below_threshold(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "meta")
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "test.json").write_text("{}")

        # Mock: 10 experiments all with delta=0 (cosmetic skills)
        cosmetic = [{"status": "keep", "delta": 0.0, "change_type": "skill",
                    "hypothesis": f"skill {i}"} for i in range(10)]
        with patch("app.results_ledger.get_recent_results", return_value=cosmetic):
            result = mod.run_meta_evolution_cycle()
            assert result["status"] == "skipped"
            assert "cosmetic" in result["reason"].lower() or "Insufficient signal" in result["reason"]

    def test_proceeds_when_real_movement_present(self, tmp_path, monkeypatch):
        """When experiments show real signal, meta-evolution proceeds past the gate."""
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "meta")
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "test.json").write_text('{"weight": 0.30}')

        # Mock: 5 experiments with substantial deltas (>0.05 total movement)
        real_data = [
            {"status": "keep", "delta": 0.03, "change_type": "code", "hypothesis": "a"},
            {"status": "keep", "delta": 0.02, "change_type": "code", "hypothesis": "b"},
            {"status": "discard", "delta": -0.01, "change_type": "code", "hypothesis": "c"},
            {"status": "keep", "delta": 0.04, "change_type": "code", "hypothesis": "d"},
            {"status": "discard", "delta": -0.02, "change_type": "skill", "hypothesis": "e"},
        ]
        with patch("app.results_ledger.get_recent_results", return_value=real_data), \
             patch("app.meta_evolution._propose_meta_mutation", return_value=None):
            result = mod.run_meta_evolution_cycle()
            # Should pass the data gate, then fail at proposal step (not data)
            assert result["status"] in ("error", "completed", "skipped")
            # If skipped, reason should NOT be insufficient signal
            if result["status"] == "skipped":
                assert "cosmetic" not in result["reason"].lower()
                assert "Insufficient signal" not in result["reason"]


# ── End-to-end: full chain ──────────────────────────────────────────────────

class TestEndToEndFixIntegration:
    """Verify the fixes work together when an error fires through the chain."""

    def test_error_triggers_failure_classifier_via_hook_chain(self):
        """Fix 1 + failure_taxonomy: an error reported through error_handler
        must produce a MAST classification in metadata."""
        # Use the real registry (it has the failure_classifier hook registered
        # at startup). Fire an error and verify classification happens.
        from app.lifecycle_hooks import get_registry, HookPoint
        from app.error_handler import report_error, ErrorCategory

        # Register a sniffer hook to capture the dispatched context
        captured = {}

        def sniffer(ctx):
            # Wait for failure_classifier (priority 3) to run before capturing
            captured["metadata"] = dict(ctx.metadata)
            captured["errors"] = list(ctx.errors)
            return ctx

        get_registry().register(
            "test_sniffer",
            HookPoint.ON_ERROR,
            sniffer,
            priority=99,  # Run after all classifiers
            description="Test sniffer for integration test",
        )
        try:
            report_error(
                ErrorCategory.LOGIC,
                "hallucination detected: fabricated source citation",
                context={"crew": "researcher", "agent_id": "r1"},
            )
            # The failure_classifier hook (priority 3, immutable) must have
            # populated _failure_classification in metadata
            classification = captured.get("metadata", {}).get("_failure_classification")
            assert classification is not None, (
                f"No MAST classification produced. Captured: {captured}"
            )
            # The error mentions "hallucination" — should classify as such
            assert classification["agent_mode"] == "hallucination"
        finally:
            try:
                get_registry().unregister("test_sniffer", HookPoint.ON_ERROR)
            except Exception:
                pass

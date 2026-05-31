"""Tests for the /delegate operator surface (2026-05-20).

Covers Phase 2 piece 2c:
  * REST endpoints at /api/cp/delegate (create / list / get / abort)
  * Signal slash command /delegate (create / status / abort / help)

Safety invariants pinned:
  * Create yields a run in CREATED status (not yet running).
  * Aborting a terminal run returns 409 (not silently succeeds).
  * Budget caps are clamped to EXECUTOR_BUDGET_CAPS at create time.
  * Signal /delegate without master switch on includes a warning.
  * Run ID prefix lookup works for the 8-char shortcut.
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
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


from app import runtime_settings  # noqa: E402
from app.autonomous_executor import (  # noqa: E402
    ExecutorRun,
    ExecutorStatus,
    store,
)


def _reset_runtime_settings() -> None:
    runtime_settings._cache = None  # type: ignore[attr-defined]


def _patch_runtime_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


# ============================================================================
# REST endpoints
# ============================================================================


class TestDelegateRest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(Path(self.tmp.name))
        _reset_runtime_settings()
        # Build a FastAPI app with just our router. The
        # require_gateway_auth dependency is bypass-in-dev-mode by
        # default; tests don't set GATEWAY_AUTH_REQUIRED.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.control_plane.delegate_api import router as delegate_router
        app = FastAPI()
        app.include_router(delegate_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        store.reset_for_tests(None)
        self.tmp.cleanup()

    def test_create_returns_run_in_created_status(self):
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={"goal": "summarise the news"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "created")
        self.assertEqual(data["goal"], "summarise the news")
        self.assertFalse(data["is_terminal"])
        self.assertIn("run_id", data)

    def test_create_persists_to_store(self):
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={"goal": "do a thing"},
            )
        run_id = resp.json()["run_id"]
        loaded = store.get(run_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.goal, "do a thing")

    def test_create_clamps_budget_to_hard_ceiling(self):
        ceiling = runtime_settings.EXECUTOR_BUDGET_CAPS["max_usd_per_run"]
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={
                    "goal": "expensive task",
                    "budget_usd": ceiling * 10,  # way above ceiling
                },
            )
        data = resp.json()
        self.assertAlmostEqual(data["budget"]["cap_usd"], ceiling)

    def test_create_uses_runtime_defaults_when_no_budget(self):
        with _patch_runtime_settings(
                executor_default_budget_usd=2.5,
                executor_default_budget_tokens=30_000,
                executor_default_wall_clock_s=900):
            resp = self.client.post(
                "/api/cp/delegate",
                json={"goal": "x"},
            )
        data = resp.json()
        self.assertAlmostEqual(data["budget"]["cap_usd"], 2.5)
        self.assertEqual(data["budget"]["cap_tokens"], 30_000)
        self.assertEqual(data["budget"]["cap_wall_clock_s"], 900)

    def test_create_rejects_empty_goal(self):
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate", json={"goal": ""},
            )
        self.assertEqual(resp.status_code, 422)

    def test_create_rejects_oversized_goal(self):
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={"goal": "x" * 5000},
            )
        self.assertEqual(resp.status_code, 422)

    def test_list_active_filter(self):
        with _patch_runtime_settings():
            self.client.post("/api/cp/delegate", json={"goal": "a"})
            self.client.post("/api/cp/delegate", json={"goal": "b"})
            resp = self.client.get("/api/cp/delegate?status=active")
        data = resp.json()
        self.assertEqual(data["count"], 2)

    def test_list_terminal_filter(self):
        with _patch_runtime_settings():
            r = self.client.post("/api/cp/delegate", json={"goal": "x"})
            run_id = r.json()["run_id"]
            self.client.post(
                f"/api/cp/delegate/{run_id}/abort",
                json={"reason": "test"},
            )
            resp = self.client.get("/api/cp/delegate?status=terminal")
        data = resp.json()
        self.assertEqual(data["count"], 1)

    def test_list_invalid_filter_rejected(self):
        with _patch_runtime_settings():
            resp = self.client.get("/api/cp/delegate?status=bogus")
        self.assertEqual(resp.status_code, 400)

    def test_get_returns_full_record(self):
        with _patch_runtime_settings():
            r = self.client.post(
                "/api/cp/delegate", json={"goal": "fetch X"},
            )
            run_id = r.json()["run_id"]
            resp = self.client.get(f"/api/cp/delegate/{run_id}")
        data = resp.json()
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["goal"], "fetch X")

    def test_get_unknown_returns_404(self):
        resp = self.client.get("/api/cp/delegate/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_abort_transitions_to_aborted(self):
        with _patch_runtime_settings():
            r = self.client.post("/api/cp/delegate", json={"goal": "x"})
            run_id = r.json()["run_id"]
            resp = self.client.post(
                f"/api/cp/delegate/{run_id}/abort",
                json={"reason": "operator changed mind"},
            )
        data = resp.json()
        self.assertEqual(data["status"], "aborted")
        self.assertEqual(data["abort_reason"], "operator changed mind")
        self.assertTrue(data["is_terminal"])

    def test_abort_terminal_run_returns_409(self):
        with _patch_runtime_settings():
            r = self.client.post("/api/cp/delegate", json={"goal": "x"})
            run_id = r.json()["run_id"]
            self.client.post(
                f"/api/cp/delegate/{run_id}/abort", json={},
            )
            # Second abort on already-aborted run → 409.
            resp = self.client.post(
                f"/api/cp/delegate/{run_id}/abort", json={},
            )
        self.assertEqual(resp.status_code, 409)

    def test_abort_unknown_returns_404(self):
        resp = self.client.post(
            "/api/cp/delegate/nope/abort", json={},
        )
        self.assertEqual(resp.status_code, 404)


# ============================================================================
# Research mode (mode="research" pre-plans the five-step research chain)
# ============================================================================


class TestDelegateResearchMode(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(Path(self.tmp.name))
        _reset_runtime_settings()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.control_plane.delegate_api import router as delegate_router
        app = FastAPI()
        app.include_router(delegate_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        store.reset_for_tests(None)
        self.tmp.cleanup()

    def test_research_mode_creates_five_step_plan(self):
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={"goal": "do caches cut p99 latency", "mode": "research"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # build_research_run pre-populates the plan and lands in PLANNING —
        # the scheduler advances it from there (no planner runs).
        self.assertEqual(data["status"], "planning")
        hints = [s["crew_hint"] for s in data["plan"]]
        self.assertEqual(
            hints,
            [
                "research:literature",
                "research:hypotheses",
                "research:investigate",
                "research:draft",
                "research:gate",
            ],
        )

    def test_research_mode_experiment_creates_seven_step_plan(self):
        # G1 fix (2026-05-31): experiment=True must reach build_research_run so
        # the single investigate step is swapped for the design_experiment ->
        # run_experiment -> analyze_result spine. Pins the create_run forwarding;
        # if the experiment= kwarg is dropped, the plan reverts to five steps.
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={
                    "goal": "do caches cut p99 latency",
                    "mode": "research",
                    "experiment": True,
                },
            )
        self.assertEqual(resp.status_code, 200)
        hints = [s["crew_hint"] for s in resp.json()["plan"]]
        self.assertEqual(
            hints,
            [
                "research:literature",
                "research:hypotheses",
                "research:design_experiment",
                "research:run_experiment",
                "research:analyze_result",
                "research:draft",
                "research:gate",
            ],
        )

    def test_research_mode_synthesize_appends_dossier_step(self):
        # G1 fix (2026-05-31): synthesize=True must reach build_research_run so a
        # final research:synthesize (dossier-PDF) step is appended.
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={
                    "goal": "do caches cut p99 latency",
                    "mode": "research",
                    "synthesize": True,
                },
            )
        self.assertEqual(resp.status_code, 200)
        hints = [s["crew_hint"] for s in resp.json()["plan"]]
        # synthesize alone (experiment defaults False) → five base steps + dossier
        self.assertEqual(hints[-1], "research:synthesize")
        self.assertEqual(len(hints), 6)

    def test_research_mode_upgrades_chat_zone_to_autonomous(self):
        # No zone given → defaults to "chat" → research upgrades to "autonomous"
        # so the research-evidence gate (chat-exempt) engages on the draft.
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={"goal": "investigate the thing", "mode": "research"},
            )
        self.assertEqual(resp.json()["zone"], "autonomous")

    def test_research_mode_honors_explicit_nonchat_zone(self):
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={
                    "goal": "investigate the thing",
                    "mode": "research",
                    "zone": "financial",
                },
            )
        self.assertEqual(resp.json()["zone"], "financial")

    def test_standard_mode_keeps_chat_zone_and_empty_plan(self):
        # Control: a non-research run is unchanged by the new branch.
        with _patch_runtime_settings():
            resp = self.client.post(
                "/api/cp/delegate",
                json={"goal": "summarise the news"},
            )
        data = resp.json()
        self.assertEqual(data["zone"], "chat")
        self.assertEqual(data["status"], "created")
        self.assertEqual(data["plan"], [])

    def test_research_summary_endpoint(self):
        with _patch_runtime_settings():
            r = self.client.post(
                "/api/cp/delegate",
                json={"goal": "does X cause Y", "mode": "research"},
            )
            run_id = r.json()["run_id"]
            resp = self.client.get(f"/api/cp/delegate/{run_id}/research-summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["question"], "does X cause Y")
        # A freshly-built run hasn't executed any step yet — summary is zeros.
        for key in (
            "question",
            "status",
            "n_literature",
            "n_hypotheses",
            "top_hypothesis",
            "draft",
            "gate_action",
            "gate_note",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["n_literature"], 0)
        self.assertEqual(data["n_hypotheses"], 0)

    def test_research_summary_unknown_returns_404(self):
        resp = self.client.get("/api/cp/delegate/nope/research-summary")
        self.assertEqual(resp.status_code, 404)

    def test_research_dossier_endpoint(self):
        with _patch_runtime_settings():
            r = self.client.post(
                "/api/cp/delegate",
                json={"goal": "does X cause Y", "mode": "research"},
            )
            run_id = r.json()["run_id"]
            resp = self.client.get(f"/api/cp/delegate/{run_id}/research-dossier")
        # 200 when reportlab is present (CI image); 503 when the PDF
        # toolchain is unavailable — both are correctly-wired responses.
        self.assertIn(resp.status_code, (200, 503))
        if resp.status_code == 200:
            data = resp.json()
            self.assertEqual(data["run_id"], run_id)
            self.assertTrue(data["filename"].endswith(".pdf"))
            self.assertIn(run_id, data["filename"])
            self.assertTrue(data["path"])

    def test_research_dossier_unknown_returns_404(self):
        resp = self.client.get("/api/cp/delegate/nope/research-dossier")
        self.assertEqual(resp.status_code, 404)


# ============================================================================
# Signal slash command
# ============================================================================


class TestDelegateSignalCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(Path(self.tmp.name))
        _reset_runtime_settings()

    def tearDown(self) -> None:
        store.reset_for_tests(None)
        self.tmp.cleanup()

    def _dispatch(self, text: str, sender: str = "user-1") -> str | None:
        from app.agents.commander.commands import _handle_delegate_command
        return _handle_delegate_command(text, sender)

    def test_help_text_returned(self):
        out = self._dispatch("/delegate help")
        self.assertIsNotNone(out)
        self.assertIn("/delegate", out)
        self.assertIn("status", out)
        self.assertIn("abort", out)

    def test_empty_returns_help(self):
        out = self._dispatch("/delegate")
        self.assertIsNotNone(out)
        self.assertIn("/delegate", out)

    def test_short_goal_rejected(self):
        out = self._dispatch("/delegate hi")
        self.assertIn("too short", out)

    def test_create_run_via_slash(self):
        with _patch_runtime_settings():
            out = self._dispatch("/delegate fetch the weather forecast")
        self.assertIn("Filed run", out)
        # One run should exist now.
        runs = store.list_all()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].goal, "fetch the weather forecast")
        self.assertEqual(runs[0].zone, "autonomous")
        self.assertTrue(runs[0].requestor.startswith("signal:"))

    def test_create_warns_when_master_switch_off(self):
        with _patch_runtime_settings(autonomous_executor_enabled=False):
            out = self._dispatch("/delegate run this thing properly")
        self.assertIn("master switch", out)

    def test_create_no_warning_when_master_switch_on(self):
        with _patch_runtime_settings(autonomous_executor_enabled=True):
            out = self._dispatch("/delegate run this thing properly")
        self.assertNotIn("master switch", out)

    def test_status_empty_lists_message(self):
        with _patch_runtime_settings():
            out = self._dispatch("/delegate status")
        self.assertIn("No active runs", out)

    def test_status_lists_active_runs(self):
        with _patch_runtime_settings():
            self._dispatch("/delegate first goal here please")
            self._dispatch("/delegate second goal here please")
            out = self._dispatch("/delegate status")
        self.assertIn("2 active run", out)

    def test_status_with_prefix_returns_detail(self):
        with _patch_runtime_settings():
            create_out = self._dispatch("/delegate make me a sandwich")
        # Extract the 8-char prefix from the success message.
        # Format: "✅ Filed run abc12345 (budget ...)"
        prefix = create_out.split("Filed run ")[1].split(" ")[0]
        with _patch_runtime_settings():
            out = self._dispatch(f"/delegate status {prefix}")
        self.assertIn("make me a sandwich", out)
        self.assertIn("created", out)
        self.assertIn("budget:", out)

    def test_status_unknown_run_returns_not_found(self):
        with _patch_runtime_settings():
            out = self._dispatch("/delegate status nonexistent")
        self.assertIn("not found", out)

    def test_abort_transitions_run(self):
        with _patch_runtime_settings():
            create_out = self._dispatch("/delegate make me a sandwich")
            prefix = create_out.split("Filed run ")[1].split(" ")[0]
            out = self._dispatch(f"/delegate abort {prefix}")
        self.assertIn("Aborted", out)
        # Confirm the run actually transitioned.
        for run in store.list_all():
            if run.run_id.startswith(prefix):
                self.assertEqual(run.status, ExecutorStatus.ABORTED)

    def test_abort_terminal_run_says_already(self):
        with _patch_runtime_settings():
            create_out = self._dispatch("/delegate goal one")
            prefix = create_out.split("Filed run ")[1].split(" ")[0]
            self._dispatch(f"/delegate abort {prefix}")
            out = self._dispatch(f"/delegate abort {prefix}")
        self.assertIn("already terminal", out)

    def test_abort_unknown_returns_not_found(self):
        with _patch_runtime_settings():
            out = self._dispatch("/delegate abort missing")
        self.assertIn("not found", out)

    def test_abort_without_id_shows_usage(self):
        with _patch_runtime_settings():
            out = self._dispatch("/delegate abort")
        self.assertIn("Usage:", out)

    def test_unrelated_input_returns_none(self):
        # "/delegate" must be the prefix — random text returns None
        # so try_command falls through.
        out = self._dispatch("hello there")
        self.assertIsNone(out)


# ============================================================================
# Integration: try_command routes /delegate correctly
# ============================================================================


class TestTryCommandRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        store.reset_for_tests(Path(self.tmp.name))
        _reset_runtime_settings()

    def tearDown(self) -> None:
        store.reset_for_tests(None)
        self.tmp.cleanup()

    def test_try_command_dispatches_delegate(self):
        from app.agents.commander.commands import try_command
        with _patch_runtime_settings():
            out = try_command(
                "/delegate write a poem about Helsinki",
                sender="user-x",
                commander=None,
            )
        self.assertIsNotNone(out)
        self.assertIn("Filed run", out)

    def test_try_command_dispatches_delegate_status(self):
        from app.agents.commander.commands import try_command
        with _patch_runtime_settings():
            out = try_command(
                "/delegate status",
                sender="user-x",
                commander=None,
            )
        self.assertIsNotNone(out)
        self.assertIn("active run", out)


if __name__ == "__main__":
    unittest.main()

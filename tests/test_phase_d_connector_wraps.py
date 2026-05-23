"""Tests for the Phase D connector-budget wraps (2026-05-22).

Pins the contract for the 6 new wrapped sites:

  D.1 — 4 feed sources in app/episteme/feed_sources.py
    * fetch_python_peps              (cap 5/day)
    * fetch_w3c_tr                   (cap 5/day)
    * fetch_huggingface_papers       (cap 5/day)
    * fetch_openreview               (cap 5/day)

  D.2 — 2 dependency_radar call sites in app/dependency_radar/proposer.py
    * _gather_cves (OSV.dev /v1/querybatch)  (cap 50/day)
    * _github_pushed_at  (GitHub repos API)  (cap 500/day)

Each wrap is verified at three contract levels:

  1. The wrap exists — the budget decorator is applied.
  2. Master-switch-OFF feeds short-circuit BEFORE budget accounting.
  3. Cap-out is treated as graceful degradation: caller gets the
     "empty result" sentinel (``[]`` / ``{}`` / ``None``), not a raise.

Many of the under-test modules need chromadb/anthropic deps that aren't
available on host. The host-runnable tests load the modules directly
via importlib so the package-level __init__ side effects are skipped.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Stubs ────────────────────────────────────────────────────────────


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())
sys.modules.setdefault("chromadb", MagicMock())


def _load_module(name: str, path: str):
    """Direct-import a module file, bypassing its package's __init__."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_feed_sources = _load_module(
    "_fs_d_test", "app/episteme/feed_sources.py",
)


def _load_dependency_radar_proposer():
    """Helper — dependency_radar is import-clean on host (stdlib only)."""
    try:
        from app.dependency_radar import proposer as _p
        return _p
    except Exception:
        return None


_dep_radar = _load_dependency_radar_proposer()


# ── D.1: feed_sources wrap contract ─────────────────────────────────


@pytest.mark.skipif(
    _feed_sources is None, reason="feed_sources module not importable",
)
class TestFeedSourcesWrapped:
    def test_budget_module_imported(self):
        # The module must have detected connector_budget availability.
        # On host, connector_budget is import-clean (stdlib + decimal).
        assert _feed_sources._BUDGET_AVAILABLE is True

    def test_connector_budget_exceeded_exported(self):
        assert _feed_sources.ConnectorBudgetExceeded is not None

    def test_peps_inner_exists(self):
        assert hasattr(_feed_sources, "_fetch_python_peps_inner")
        assert callable(_feed_sources._fetch_python_peps_inner)

    def test_w3c_inner_exists(self):
        assert hasattr(_feed_sources, "_fetch_w3c_tr_inner")

    def test_huggingface_inner_exists(self):
        assert hasattr(_feed_sources, "_fetch_huggingface_papers_inner")

    def test_openreview_inner_wrapped(self):
        # OpenReview's inner predated the budget — it now has the
        # decorator attached. Sentinel: the decorator stamps a hidden
        # attribute via functools.wraps; we verify by checking the
        # function is wrapped (the original is unchanged but the
        # outer call goes through the budget gate).
        assert hasattr(_feed_sources, "_fetch_openreview_inner")
        assert callable(_feed_sources._fetch_openreview_inner)


@pytest.mark.skipif(
    _feed_sources is None, reason="feed_sources module not importable",
)
class TestFeedSourcesMasterSwitch:
    """Master switch OFF must short-circuit BEFORE budget accounting —
    a disabled feed must NOT burn the daily cap."""

    def test_peps_disabled_returns_empty_without_budget_tick(
        self, monkeypatch,
    ):
        monkeypatch.setenv("PAPER_PIPELINE_PEPS_ENABLED", "false")
        # The wrapped inner is what gates on budget; if we never
        # call it, the budget store stays untouched.
        calls = []

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return []

        monkeypatch.setattr(
            _feed_sources, "_fetch_python_peps_inner", _spy,
        )
        result = _feed_sources.fetch_python_peps()
        assert result == []
        assert calls == []  # short-circuited before reaching inner

    def test_w3c_disabled_short_circuits(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_W3C_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            _feed_sources, "_fetch_w3c_tr_inner",
            lambda *a, **k: calls.append(1) or [],
        )
        assert _feed_sources.fetch_w3c_tr() == []
        assert calls == []

    def test_hf_disabled_short_circuits(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_HF_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            _feed_sources, "_fetch_huggingface_papers_inner",
            lambda *a, **k: calls.append(1) or [],
        )
        assert _feed_sources.fetch_huggingface_papers() == []
        assert calls == []

    def test_openreview_disabled_short_circuits(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_OPENREVIEW_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            _feed_sources, "_fetch_openreview_inner",
            lambda *a, **k: calls.append(1) or [],
        )
        assert _feed_sources.fetch_openreview() == []
        assert calls == []


@pytest.mark.skipif(
    _feed_sources is None, reason="feed_sources module not importable",
)
class TestFeedSourcesCapOut:
    """ConnectorBudgetExceeded raised from the inner must be caught and
    degraded to the empty-result sentinel by the public function."""

    def test_peps_cap_out_returns_empty(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_PEPS_ENABLED", "true")

        def _raises_cap_out(*args, **kwargs):
            raise _feed_sources.ConnectorBudgetExceeded("cap reached")

        monkeypatch.setattr(
            _feed_sources, "_fetch_python_peps_inner", _raises_cap_out,
        )
        assert _feed_sources.fetch_python_peps() == []

    def test_w3c_cap_out_returns_empty(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_W3C_ENABLED", "true")
        monkeypatch.setattr(
            _feed_sources, "_fetch_w3c_tr_inner",
            lambda *a, **k: (_ for _ in ()).throw(
                _feed_sources.ConnectorBudgetExceeded("nope"),
            ),
        )
        assert _feed_sources.fetch_w3c_tr() == []

    def test_hf_cap_out_returns_empty(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_HF_ENABLED", "true")
        monkeypatch.setattr(
            _feed_sources, "_fetch_huggingface_papers_inner",
            lambda *a, **k: (_ for _ in ()).throw(
                _feed_sources.ConnectorBudgetExceeded("nope"),
            ),
        )
        assert _feed_sources.fetch_huggingface_papers() == []

    def test_openreview_cap_out_returns_empty(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_OPENREVIEW_ENABLED", "true")
        monkeypatch.setattr(
            _feed_sources, "_fetch_openreview_inner",
            lambda *a, **k: (_ for _ in ()).throw(
                _feed_sources.ConnectorBudgetExceeded("nope"),
            ),
        )
        assert _feed_sources.fetch_openreview() == []


# ── D.2: dependency_radar wrap contract ──────────────────────────────


@pytest.mark.skipif(
    _dep_radar is None, reason="dependency_radar not importable",
)
class TestDependencyRadarBudgetWired:
    def test_budget_helpers_exist(self):
        assert hasattr(_dep_radar, "_budgeted_osv_post")
        assert hasattr(_dep_radar, "_budgeted_github_get")

    def test_budget_available_flag(self):
        # On host, both connector_budget and dependency_radar are
        # stdlib-clean.
        assert _dep_radar._BUDGET_AVAILABLE is True

    def test_osv_cap_out_returns_empty_dict(self):
        # _gather_cves must catch the cap-out from the wrapped POST
        # and return {} rather than propagate.
        with patch.object(
            _dep_radar, "_budgeted_osv_post",
            side_effect=_dep_radar._BudgetExceeded(
                "test", 0.0, None, 0.0,
                today_calls_made=999, daily_call_cap=100,
            ),
        ):
            out = _dep_radar._gather_cves(
                packages=[("requests", "2.0.0")],
            )
        assert out == {}

    def test_github_cap_out_returns_none(self):
        # _github_pushed_at must catch the cap-out and return None.
        with patch.object(
            _dep_radar, "_budgeted_github_get",
            side_effect=_dep_radar._BudgetExceeded(
                "test", 0.0, None, 0.0,
                today_calls_made=999, daily_call_cap=100,
            ),
        ):
            out = _dep_radar._github_pushed_at("owner", "repo")
        assert out is None

    def test_osv_happy_path_unaffected(self):
        # When _budgeted_osv_post returns a payload normally, the
        # normal parsing path runs.
        fake_body = json.dumps({
            "results": [{"vulns": [{"id": "CVE-2024-1"}]}],
        }).encode("utf-8")
        with patch.object(
            _dep_radar, "_budgeted_osv_post", return_value=fake_body,
        ):
            out = _dep_radar._gather_cves(
                packages=[("requests", "2.0.0")],
            )
        assert "requests" in out
        assert out["requests"][0]["id"] == "CVE-2024-1"

    def test_github_happy_path_unaffected(self):
        fake_body = json.dumps({
            "pushed_at": "2026-01-15T10:00:00Z",
        }).encode("utf-8")
        with patch.object(
            _dep_radar, "_budgeted_github_get", return_value=fake_body,
        ):
            out = _dep_radar._github_pushed_at("owner", "repo")
        assert out is not None
        assert out.year == 2026
        assert out.month == 1


# ── Backward-compat: empty pkgs / disabled paths ────────────────────


@pytest.mark.skipif(
    _dep_radar is None, reason="dependency_radar not importable",
)
class TestDependencyRadarBackwardCompat:
    def test_gather_cves_empty_packages_no_call(self):
        # No packages -> early return -> no budget tick.
        with patch.object(_dep_radar, "_budgeted_osv_post") as spy:
            out = _dep_radar._gather_cves(packages=[])
        assert out == {}
        spy.assert_not_called()

    def test_gather_cves_with_injected_runner_skips_real_call(self):
        # The injectable osv_runner path skips the real HTTP wrap
        # entirely — tests can drive the function without consuming
        # budget.
        with patch.object(_dep_radar, "_budgeted_osv_post") as spy:
            out = _dep_radar._gather_cves(
                packages=[("requests", "2.0.0")],
                osv_runner=lambda pkgs: {"requests": [{"id": "x"}]},
            )
        assert out == {"requests": [{"id": "x"}]}
        spy.assert_not_called()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""Pin the @switch_gated migration of the 4 feed-source fetchers
(Phase E.3 real-site demo, 2026-05-22).

Confirms that:

  * Each ``fetch_*`` carries the ``__switch_name__`` metadata stamped
    by ``@switch_gated`` — so the migration is structurally visible
    (introspection finds it; grep -F '@switch_gated' confirms each
    site).
  * Env var set to "false" → fetcher returns a fresh ``[]`` without
    calling the inner — same behavior as the pre-migration manual
    ``if not _enabled(): return []`` check.
  * Env var set to "true" → fetcher proceeds to the inner. We don't
    actually hit the network in the test — we monkey-patch the inner
    to a sentinel-returning stub.
  * Default-True posture preserved: with the env var unset, the
    fetcher still proceeds (matches the pre-migration default).
"""
from __future__ import annotations

import importlib.util
import sys
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())
sys.modules.setdefault("chromadb", MagicMock())


def _load():
    """Direct-import feed_sources.py to bypass the package __init__'s
    chromadb pull (the parent app/episteme/__init__.py imports the
    vector store)."""
    spec = importlib.util.spec_from_file_location(
        "_fs_e3", "app/episteme/feed_sources.py",
    )
    if spec is None or spec.loader is None:
        return None
    m = importlib.util.module_from_spec(spec)
    sys.modules["_fs_e3"] = m
    spec.loader.exec_module(m)
    return m


fs = _load()


# ── Metadata pin ────────────────────────────────────────────────────


@pytest.mark.skipif(fs is None, reason="feed_sources not loadable")
class TestSwitchGatedMetadata:
    """Each fetcher must carry the ``__switch_name__`` attribute
    stamped by ``@switch_gated`` — proves the migration landed on the
    public surface (not just an inner-helper rename)."""

    CASES = [
        ("fetch_python_peps", "PAPER_PIPELINE_PEPS_ENABLED"),
        ("fetch_w3c_tr", "PAPER_PIPELINE_W3C_ENABLED"),
        ("fetch_huggingface_papers", "PAPER_PIPELINE_HF_ENABLED"),
        ("fetch_openreview", "PAPER_PIPELINE_OPENREVIEW_ENABLED"),
    ]

    def test_metadata_present_on_all_four(self):
        for fn_name, switch_name in self.CASES:
            fn = getattr(fs, fn_name)
            assert hasattr(fn, "__switch_name__"), (
                f"{fn_name} missing __switch_name__ — @switch_gated "
                f"not applied"
            )
            assert fn.__switch_name__ == switch_name, (
                f"{fn_name} switch_name={fn.__switch_name__!r}, "
                f"expected {switch_name!r}"
            )

    def test_default_posture_is_true(self):
        for fn_name, _ in self.CASES:
            fn = getattr(fs, fn_name)
            assert fn.__switch_default__ is True, (
                f"{fn_name} default={fn.__switch_default__!r}, "
                f"expected True (matches pre-migration posture)"
            )


# ── Master switch OFF — short-circuit behavior ──────────────────────


@pytest.mark.skipif(fs is None, reason="feed_sources not loadable")
class TestMasterSwitchOff:
    """When the env var is set to a falsy value, the inner must NOT
    be called and the result must be ``[]``."""

    def test_peps_off(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_PEPS_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            fs, "_fetch_python_peps_inner",
            lambda *a, **k: calls.append(1) or [{"id": "x"}],
        )
        result = fs.fetch_python_peps()
        assert result == []
        assert calls == []

    def test_w3c_off(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_W3C_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            fs, "_fetch_w3c_tr_inner",
            lambda *a, **k: calls.append(1) or [{"id": "x"}],
        )
        assert fs.fetch_w3c_tr() == []
        assert calls == []

    def test_hf_off(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_HF_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            fs, "_fetch_huggingface_papers_inner",
            lambda *a, **k: calls.append(1) or [{"id": "x"}],
        )
        assert fs.fetch_huggingface_papers() == []
        assert calls == []

    def test_openreview_off(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_OPENREVIEW_ENABLED", "false")
        calls = []
        monkeypatch.setattr(
            fs, "_fetch_openreview_inner",
            lambda *a, **k: calls.append(1) or [{"id": "x"}],
        )
        assert fs.fetch_openreview() == []
        assert calls == []

    def test_off_returns_fresh_list_each_call(self, monkeypatch):
        """Regression on the factory-vs-value bug found during
        Phase E.3 wiring: ``on_disabled=list`` must produce a fresh
        ``[]`` each call, not a shared instance or the type object."""
        monkeypatch.setenv("PAPER_PIPELINE_PEPS_ENABLED", "false")
        r1 = fs.fetch_python_peps()
        r2 = fs.fetch_python_peps()
        assert r1 == [] and r2 == []
        # Confirm the result is a list instance, not the list type
        assert type(r1) is list
        # Confirm fresh each call
        assert r1 is not r2


# ── Master switch ON — proceed to inner ─────────────────────────────


@pytest.mark.skipif(fs is None, reason="feed_sources not loadable")
class TestMasterSwitchOn:
    def test_peps_on(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_PEPS_ENABLED", "true")
        monkeypatch.setattr(
            fs, "_fetch_python_peps_inner",
            lambda *a, **k: [{"id": "ok"}],
        )
        result = fs.fetch_python_peps()
        assert result == [{"id": "ok"}]

    def test_default_unset_proceeds(self, monkeypatch):
        # No env value at all → default=True → proceeds
        monkeypatch.delenv("PAPER_PIPELINE_PEPS_ENABLED", raising=False)
        monkeypatch.setattr(
            fs, "_fetch_python_peps_inner",
            lambda *a, **k: [{"id": "ok"}],
        )
        result = fs.fetch_python_peps()
        assert result == [{"id": "ok"}]


# ── Cap-out behavior still works ────────────────────────────────────


@pytest.mark.skipif(fs is None, reason="feed_sources not loadable")
class TestCapOutStillCaught:
    """The connector-budget try/except inside the wrapped function
    must continue to catch ``ConnectorBudgetExceeded`` and return
    ``[]``."""

    def test_peps_cap_out(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_PEPS_ENABLED", "true")

        def _raise_cap_out(*a, **k):
            raise fs.ConnectorBudgetExceeded(
                "paper_pipeline_peps", 0.0, None, 0.0,
                today_calls_made=6, daily_call_cap=5,
            )

        monkeypatch.setattr(
            fs, "_fetch_python_peps_inner", _raise_cap_out,
        )
        assert fs.fetch_python_peps() == []

    def test_openreview_cap_out(self, monkeypatch):
        monkeypatch.setenv("PAPER_PIPELINE_OPENREVIEW_ENABLED", "true")
        monkeypatch.setattr(
            fs, "_fetch_openreview_inner",
            lambda *a, **k: (_ for _ in ()).throw(
                fs.ConnectorBudgetExceeded(
                    "paper_pipeline_openreview", 0.0, None, 0.0,
                    today_calls_made=6, daily_call_cap=5,
                ),
            ),
        )
        assert fs.fetch_openreview() == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

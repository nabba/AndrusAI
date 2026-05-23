"""Tests for POST /api/cp/changes/{id}/check-types (2026-05-22).

Operator-triggered fresh pyright pass against the CR's new_content.

Skips on host without fastapi/psycopg2 (matches the pattern of
test_reviews_api.py + test_capability_regression_api.py); runs in CI.

Covers:
  * 404 when CR doesn't exist
  * Non-.py path → ran:false with explanatory reason
  * Pyright unavailable → ran:false
  * Disabled sidecar → ran:false
  * Happy path: clean file → ran:true, error_count=0
  * Errors present: diagnostics + counts returned
  * Pyright timed_out → ran:false with duration
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

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


@pytest.fixture
def client(tmp_path, monkeypatch):
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.control_plane.changes_api import router
    except Exception as exc:
        pytest.skip(f"fastapi/router import failed: {exc}")
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)


def _make_fake_cr(path="app/x.py", new_content="x: int = 1\n"):
    """Lightweight ChangeRequest stub the endpoint can serialize."""
    class _CR:
        pass
    cr = _CR()
    cr.id = "test-cr-1"
    cr.path = path
    cr.new_content = new_content
    return cr


class TestForceTypeCheckEndpoint:
    def test_unknown_cr_returns_404(self, client, monkeypatch):
        import app.control_plane.changes_api as mod
        monkeypatch.setattr(mod, "get", lambda _id: None)
        resp = client.post("/api/cp/changes/unknown/check-types")
        assert resp.status_code == 404

    def test_non_py_path_cooperative(self, client, monkeypatch):
        import app.control_plane.changes_api as mod
        cr = _make_fake_cr(path="docs/README.md")
        monkeypatch.setattr(mod, "get", lambda _id: cr)
        resp = client.post("/api/cp/changes/test-cr-1/check-types")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ran"] is False
        assert "not a Python file" in data["reason"]

    def test_binary_unavailable(self, client, monkeypatch):
        import app.control_plane.changes_api as mod
        cr = _make_fake_cr()
        monkeypatch.setattr(mod, "get", lambda _id: cr)
        # Patch the import path: when changes_api imports
        # is_available we want False.
        from app.code_intel import pyright_sidecar as ps
        monkeypatch.setattr(ps, "is_available", lambda: False)
        resp = client.post("/api/cp/changes/test-cr-1/check-types")
        data = resp.json()
        assert data["ran"] is False
        assert "not on PATH" in data["reason"]

    def test_happy_path_clean(self, client, monkeypatch):
        import app.control_plane.changes_api as mod
        from app.code_intel import pyright_sidecar as ps
        from app.code_intel.pyright_sidecar import PyrightReport

        cr = _make_fake_cr()
        monkeypatch.setattr(mod, "get", lambda _id: cr)
        monkeypatch.setattr(ps, "is_available", lambda: True)
        monkeypatch.setattr(
            ps, "check_paths",
            lambda *a, **kw: PyrightReport(
                diagnostics=[], available=True, duration_s=0.1,
            ),
        )
        resp = client.post("/api/cp/changes/test-cr-1/check-types")
        data = resp.json()
        assert data["ran"] is True
        assert data["error_count"] == 0
        assert data["warning_count"] == 0
        assert data["diagnostics"] == []
        assert data["path"] == "app/x.py"
        # config_root present in response (empty when defaults)
        assert "config_root" in data
        assert data["config_root"] == ""

    def test_config_root_surfaced_when_present(self, client, monkeypatch):
        """When pyright discovered a project config, the endpoint
        surfaces its path so the operator can see which rules
        applied."""
        import app.control_plane.changes_api as mod
        from app.code_intel import pyright_sidecar as ps
        from app.code_intel.pyright_sidecar import PyrightReport

        cr = _make_fake_cr()
        monkeypatch.setattr(mod, "get", lambda _id: cr)
        monkeypatch.setattr(ps, "is_available", lambda: True)
        monkeypatch.setattr(
            ps, "check_paths",
            lambda *a, **kw: PyrightReport(
                diagnostics=[], available=True, duration_s=0.1,
                config_root="/work/myproject",
            ),
        )
        resp = client.post("/api/cp/changes/test-cr-1/check-types")
        data = resp.json()
        assert data["config_root"] == "/work/myproject"

    def test_with_errors(self, client, monkeypatch):
        import app.control_plane.changes_api as mod
        from app.code_intel import pyright_sidecar as ps
        from app.code_intel.pyright_sidecar import (
            PyrightDiagnostic, PyrightReport,
        )

        cr = _make_fake_cr()
        monkeypatch.setattr(mod, "get", lambda _id: cr)
        monkeypatch.setattr(ps, "is_available", lambda: True)
        monkeypatch.setattr(
            ps, "check_paths",
            lambda *a, **kw: PyrightReport(
                diagnostics=[
                    PyrightDiagnostic(
                        "x.py", 5, 1, "error", "rule1", "bad assignment"
                    ),
                ],
                available=True,
                duration_s=0.2,
            ),
        )
        resp = client.post("/api/cp/changes/test-cr-1/check-types")
        data = resp.json()
        assert data["ran"] is True
        assert data["error_count"] == 1
        assert len(data["diagnostics"]) == 1
        assert data["diagnostics"][0]["message"] == "bad assignment"

    def test_timed_out(self, client, monkeypatch):
        import app.control_plane.changes_api as mod
        from app.code_intel import pyright_sidecar as ps
        from app.code_intel.pyright_sidecar import PyrightReport

        cr = _make_fake_cr()
        monkeypatch.setattr(mod, "get", lambda _id: cr)
        monkeypatch.setattr(ps, "is_available", lambda: True)
        monkeypatch.setattr(
            ps, "check_paths",
            lambda *a, **kw: PyrightReport(
                diagnostics=[], available=True,
                timed_out=True, duration_s=30.0,
            ),
        )
        resp = client.post("/api/cp/changes/test-cr-1/check-types")
        data = resp.json()
        assert data["ran"] is False
        assert "timed out" in data["reason"]

    def test_disabled_sidecar(self, client, monkeypatch):
        import app.control_plane.changes_api as mod
        from app.code_intel import pyright_sidecar as ps
        from app.code_intel.pyright_sidecar import PyrightReport

        cr = _make_fake_cr()
        monkeypatch.setattr(mod, "get", lambda _id: cr)
        monkeypatch.setattr(ps, "is_available", lambda: True)
        monkeypatch.setattr(
            ps, "check_paths",
            lambda *a, **kw: PyrightReport(
                disabled=True, available=True,
            ),
        )
        resp = client.post("/api/cp/changes/test-cr-1/check-types")
        data = resp.json()
        assert data["ran"] is False
        assert "disabled" in data["reason"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

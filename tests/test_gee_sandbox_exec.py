"""Tests for sandboxed gee_run_script (#5 follow-up).

Host-runnable: injected fake container runner — no Docker, no Earth Engine. The
SA-forwarding + in-container re-auth (the part that needs EE + Docker) is gated
default-OFF + validated by the operator after building the evolver image.
"""
import base64
import json

import pytest

gee = pytest.importorskip("app.tools.gee_tool")
from app.tools import gee_compose_job  # noqa: E402  (stdlib-only at import)


def test_extract_result_roundtrip():
    inner = {"ok": True, "result": {"ok": True, "stdout": "", "result": None, "error": None,
                                    "rendered_maps": [], "artifacts": [], "rejected_artifacts": []}}
    logs = "stderr noise\n" + gee_compose_job._RESULT_BEGIN + json.dumps(inner) + gee_compose_job._RESULT_END + "\n"
    assert gee_compose_job.extract_result(logs) == inner


def test_extract_result_missing_raises():
    with pytest.raises(ValueError):
        gee_compose_job.extract_result("no sentinel")


def test_encode_artifacts_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(gee_compose_job, "_MAX_ARTIFACT_BYTES", 8)
    big = tmp_path / "big.png"; big.write_bytes(b"x" * 40)
    ok = tmp_path / "ok.png"; ok.write_bytes(b"hi")
    enc, rej = gee_compose_job._encode_artifacts([str(big), str(ok)])
    assert [a["name"] for a in enc] == ["ok.png"]
    assert [r["name"] for r in rej] == ["big.png"]


def _sa_file(tmp_path):
    p = tmp_path / "sa.json"
    p.write_text(json.dumps({"project_id": "test-proj", "client_email": "x@y.iam.gserviceaccount.com"}))
    return p


def test_sandboxed_forwards_creds_bridge_and_writes_maps(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(_sa_file(tmp_path)))
    maps = tmp_path / "maps"
    monkeypatch.setattr(gee, "_GEE_MAPS_DIR", maps)
    captured: dict = {}

    def fake_run(job, **kw):
        captured["job"] = job
        captured["network_mode"] = kw.get("network_mode")
        png = base64.b64encode(b"\x89PNG fake").decode()
        return {"ok": True, "result": {"ok": True, "stdout": "ran", "result": {"x": 1}, "error": None,
                                       "rendered_maps": ["/app/workspace/output/maps/m.png"],
                                       "artifacts": [{"name": "m.png", "b64": png, "bytes": 8}],
                                       "rejected_artifacts": []}}

    out = gee._run_user_script_sandboxed("result=ee.Number(1)", timeout_s=5, run_job=fake_run)
    assert out is not None and out["ok"] is True
    # SA credential forwarded inside the job; bridge network (EE needs internet)
    assert captured["job"]["project"] == "test-proj"
    assert "client_email" in captured["job"]["sa_json"]
    assert captured["network_mode"] == "bridge"
    # decoded PNG written to the REAL maps dir + surfaced as a rendered map
    assert (maps / "m.png").read_bytes() == b"\x89PNG fake"
    assert out["rendered_maps"] == [str(maps / "m.png")]


def test_sandboxed_no_creds_falls_back(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    # no credential to forward → None so the caller uses the in-process path
    assert gee._run_user_script_sandboxed("x=1", run_job=lambda *a, **k: {"ok": True}) is None


def test_sandboxed_infra_failure_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(_sa_file(tmp_path)))
    assert gee._run_user_script_sandboxed("x=1", run_job=lambda *a, **k: {"ok": False, "error": "spawn"}) is None
    assert gee._run_user_script_sandboxed(
        "x=1", run_job=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))) is None


def test_switch_default_off():
    rs = pytest.importorskip("app.runtime_settings")
    assert rs._defaults().get("sandboxed_gee_exec_enabled") is False

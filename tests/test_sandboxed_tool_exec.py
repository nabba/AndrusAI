"""Tests for the #5 sandboxed-tool-exec path (pdf_compose → evolver container).

Host-runnable: they exercise the gateway-side wiring + the sentinel/encode
helpers with an INJECTED fake container runner — no Docker, no reportlab. The
full in-container run (real `_run_user_script` over reportlab/matplotlib) is
validated in the Docker CI / by the operator after building the evolver image.
"""
import base64
import json

import pytest

pdf = pytest.importorskip("app.tools.pdf_compose")  # imports clean (heavy deps are try/excepted)
from app.tools import pdf_compose_job  # noqa: E402  (stdlib-only at import)


# ── sentinel + encode helpers (pure) ─────────────────────────────────────────

def test_extract_result_roundtrip():
    inner = {"ok": True, "result": {"ok": True, "artifacts": [], "stdout": "",
                                    "stderr": "", "result": None, "error": None,
                                    "rejected_artifacts": []}}
    logs = ("noise on stderr\n" + pdf_compose_job._RESULT_BEGIN
            + json.dumps(inner) + pdf_compose_job._RESULT_END + "\ntrailing noise")
    assert pdf_compose_job.extract_result(logs) == inner


def test_extract_result_missing_sentinel_raises():
    with pytest.raises(ValueError):
        pdf_compose_job.extract_result("container produced no sentinel")


def test_encode_artifacts_rejects_oversize_does_not_truncate(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_compose_job, "_MAX_ARTIFACT_BYTES", 10)
    big = tmp_path / "big.pdf"; big.write_bytes(b"x" * 50)
    small = tmp_path / "small.pdf"; small.write_bytes(b"hi")
    enc, rej = pdf_compose_job._encode_artifacts([str(big), str(small)])
    assert [a["name"] for a in enc] == ["small.pdf"]
    assert base64.b64decode(enc[0]["b64"]) == b"hi"      # round-trips intact
    assert [r["name"] for r in rej] == ["big.pdf"]       # surfaced, not silently dropped


# ── gateway-side runner (injected fake container) ────────────────────────────

def _envelope(artifacts, *, ok=True, inner_ok=True, rejected=None):
    return {"ok": ok, "result": {"ok": inner_ok, "stdout": "done", "stderr": "",
                                 "result": "/x.pdf", "error": None,
                                 "artifacts": artifacts,
                                 "rejected_artifacts": rejected or []}}


def test_sandboxed_writes_artifacts_to_real_output(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_OUTPUT_DIR", tmp_path)
    art = {"name": "report.pdf", "b64": base64.b64encode(b"%PDF-1.4 fake").decode(), "bytes": 13}
    out = pdf._run_user_script_sandboxed(
        "result='x'", timeout_s=5, run_job=lambda *a, **k: _envelope([art]),
    )
    assert out is not None and out["ok"] is True
    assert len(out["files"]) == 1
    written = tmp_path / "report.pdf"
    assert written.exists() and written.read_bytes() == b"%PDF-1.4 fake"


def test_sandboxed_surfaces_rejected_artifacts_in_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_OUTPUT_DIR", tmp_path)
    env = _envelope([], rejected=[{"name": "huge.pdf", "bytes": 99, "reason": "too big"}])
    out = pdf._run_user_script_sandboxed("x=1", run_job=lambda *a, **k: env)
    assert out is not None
    assert "huge.pdf" in out["stderr"] and "too big" in out["stderr"]


def test_sandboxed_falls_back_to_none_on_infra_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_OUTPUT_DIR", tmp_path)
    # spawn/transport/parse failure envelope -> None (caller falls back in-process)
    assert pdf._run_user_script_sandboxed(
        "x=1", run_job=lambda *a, **k: {"ok": False, "error": "spawn failed"}) is None
    # runner raised -> None
    assert pdf._run_user_script_sandboxed(
        "x=1", run_job=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))) is None


def test_sandboxed_script_error_is_NOT_a_fallback(tmp_path, monkeypatch):
    # The container RAN but the script errored (inner ok=False). This must return
    # a normal dict (ok=False), NOT None — so a malicious script cannot force the
    # insecure in-process fallback.
    monkeypatch.setattr(pdf, "_OUTPUT_DIR", tmp_path)
    out = pdf._run_user_script_sandboxed(
        "boom", run_job=lambda *a, **k: _envelope([], inner_ok=False))
    assert out is not None and out["ok"] is False


def test_master_switch_defaults_off(monkeypatch):
    rs = pytest.importorskip("app.runtime_settings")
    # Fresh default must be OFF (in-process path stays the live default).
    assert rs._defaults().get("sandboxed_tool_exec_enabled") is False

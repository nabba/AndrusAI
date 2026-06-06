"""gee_compose_job — ephemeral-container entrypoint for sandboxed Earth Engine.

Runs as ``python3 -m app.tools.gee_compose_job`` inside the evolver image, with
the ENTRYPOINT overridden. Mirrors ``pdf_compose_job``, but Earth Engine needs
(a) the service-account credential and (b) outbound network — so the gateway
side forwards the SA-JSON **content** inside the job dict (it rides in
``$AAI_GEE_JOB``; the ``VOLUMES:0`` container has no ``/app/secrets`` mount) and
runs the container with ``network_mode="bridge"``.

Net security vs. in-process: in-process ``gee_run_script`` can read every
gateway secret + the chromadb files + reach the docker-proxy (→ host root). In a
bridge-network container the sandboxed script can read ONLY the EE SA key (which
it could read in-process too) + reach the internet — it CANNOT touch the
gateway's other secrets, chromadb, or the docker-proxy. So containerizing
strictly shrinks the blast radius; the residual (EE key + egress) is why it's a
DISTINCT, default-OFF operator opt-in (``sandboxed_gee_exec_enabled``).

I/O contract: ``$AAI_GEE_JOB`` = ``{"script","timeout_s","sa_json","project"}``.
Output: ``<<<GEE_RESULT>>>{"ok":true,"result":{...}}<<<GEE_END>>>`` on stdout;
rendered PNGs are base64'd back (no shared fs). All logging → stderr.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("gee_compose_job")

_RESULT_BEGIN = "<<<GEE_RESULT>>>"
_RESULT_END = "<<<GEE_END>>>"

_MAX_ARTIFACT_BYTES = 12 * 1024 * 1024
_MAX_TOTAL_ARTIFACT_BYTES = 24 * 1024 * 1024


def _encode_artifacts(paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Return ``(encoded, rejected)`` — oversize files are rejected (with a
    reason), never truncated (a truncated base64 is unrecoverable)."""
    encoded: list[dict] = []
    rejected: list[dict] = []
    total = 0
    for p in paths:
        name = Path(p).name
        try:
            data = Path(p).read_bytes()
        except Exception as exc:
            rejected.append({"name": name, "bytes": -1, "reason": f"read failed: {exc}"})
            continue
        n = len(data)
        if n > _MAX_ARTIFACT_BYTES:
            rejected.append({"name": name, "bytes": n, "reason": f"exceeds per-file cap ({_MAX_ARTIFACT_BYTES})"})
            continue
        if total + n > _MAX_TOTAL_ARTIFACT_BYTES:
            rejected.append({"name": name, "bytes": n, "reason": "exceeds total artifact budget"})
            continue
        total += n
        encoded.append({"name": name, "b64": base64.b64encode(data).decode("ascii"), "bytes": n})
    return encoded, rejected


def run_gee(job: dict) -> dict:
    """Materialise the forwarded EE credential, run the user's GEE script via the
    real ``gee_tool._run_user_script``, and base64 the rendered PNGs back."""
    script = job.get("script")
    base = {"ok": False, "stdout": "", "result": None, "error": None,
            "rendered_maps": [], "artifacts": [], "rejected_artifacts": []}
    if not isinstance(script, str) or not script.strip():
        return {**base, "error": "gee job missing non-empty 'script'"}
    try:
        timeout_s = int(job.get("timeout_s", 60))
    except (TypeError, ValueError):
        timeout_s = 60

    sa_json = job.get("sa_json")
    if sa_json:
        cred = Path(tempfile.gettempdir()) / "gee_sa.json"
        cred.write_text(sa_json if isinstance(sa_json, str) else json.dumps(sa_json))
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred)
    if job.get("project"):
        os.environ["GEE_PROJECT"] = str(job["project"])

    from app.tools import gee_tool

    ok, err = gee_tool._ensure_initialised()
    if not ok:
        return {**base, "error": f"ee init failed: {err}"}

    out = gee_tool._run_user_script(script, timeout_s=timeout_s)
    encoded, rejected = _encode_artifacts(out.get("rendered_maps") or [])
    return {
        "ok": bool(out.get("ok")),
        "stdout": out.get("stdout") or "",
        "result": out.get("result"),
        "error": out.get("error"),
        "rendered_maps": out.get("rendered_maps") or [],
        "artifacts": encoded,
        "rejected_artifacts": rejected,
    }


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raw = os.environ.get("AAI_GEE_JOB", "")
    if not raw and argv:
        raw = argv[0]
    try:
        job = json.loads(raw) if raw else {}
        out: dict[str, Any] = {"ok": True, "result": run_gee(job)}
    except Exception as exc:
        logger.exception("gee_compose_job failed")
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(_RESULT_BEGIN + json.dumps(out) + _RESULT_END + "\n")
    sys.stdout.flush()
    return 0 if out.get("ok") else 1


def extract_result(logs: str) -> dict:
    """Gateway-side: pull the result JSON out of the container's stdout logs."""
    begin = logs.rfind(_RESULT_BEGIN)
    end = logs.rfind(_RESULT_END)
    if begin < 0 or end < 0 or end <= begin:
        raise ValueError("no gee result sentinel found in logs")
    return json.loads(logs[begin + len(_RESULT_BEGIN) : end])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))

"""pdf_compose_job — ephemeral-container entrypoint for sandboxed PDF composition.

Runs as ``python3 -m app.tools.pdf_compose_job`` INSIDE the throwaway evolver
image (``Dockerfile.evolver``), with the container's ENTRYPOINT overridden to
this module. The gateway side (``app.tools.pdf_compose._run_user_script_sandboxed``)
spawns the container through ``evolver_spawn.run_container_job`` with
``AAI_PDF_COMPOSE_JOB`` as the job env-var and ``network_mode="none"`` (PDF
rendering needs no network — pdf_compose's stated design).

Why containerize: ``pdf_compose`` runs LLM-authored Python via ``exec()``; the
in-process ``_safe_exec`` hardening is defence-in-depth, NOT a boundary (its own
docstring says a crafted payload can escape via object-subclass traversal). The
container is the real isolation — ephemeral fs, no network, mem/pids caps,
OOM-victim score — and, critically, it does NOT share the gateway's chromadb
files, so it cannot reintroduce the dual-writer corruption a host subprocess
would risk (CLAUDE.md §77 rejected out-of-process offload for exactly that
reason; an isolated container side-steps it).

I/O contract (container-native — no shared volume, ``VOLUMES: 0`` on the proxy):
  * **Input**: ``$AAI_PDF_COMPOSE_JOB`` = ``{"script": <python>, "timeout_s"?: int}``.
  * **Output**: ONE sentinel-wrapped JSON line on stdout::

        <<<PDF_COMPOSE_RESULT>>>{"ok":true,"result":{...}}<<<PDF_COMPOSE_END>>>

    ``result`` = ``{ok, stdout, stderr, result, error, artifacts, rejected_artifacts}``.
    Produced files (PDF/PNG/CSV) are base64'd back because there is no shared
    filesystem; the gateway decodes + writes them to the REAL workspace/output.
    All logging goes to stderr — the result line is the only stdout payload.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("pdf_compose_job")

_RESULT_BEGIN = "<<<PDF_COMPOSE_RESULT>>>"
_RESULT_END = "<<<PDF_COMPOSE_END>>>"

# Artifacts travel as base64 in the single sentinel line, which the gateway
# reads from one ``GET /logs`` into memory. base64 inflates ~33%. An artifact
# over the per-file cap is REJECTED (not truncated — a truncated base64 is
# unrecoverable) with a clear reason so the operator can raise the cap or split
# the report. PDFs are normally well under this.
_MAX_ARTIFACT_BYTES = 12 * 1024 * 1024        # per produced file (raw)
_MAX_TOTAL_ARTIFACT_BYTES = 24 * 1024 * 1024   # all artifacts combined


def _encode_artifacts(paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Return ``(encoded, rejected)``.

    ``encoded`` items: ``{name, b64, bytes}``. ``rejected`` items:
    ``{name, bytes, reason}`` (oversize or unreadable — never silently dropped).
    """
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
            rejected.append({"name": name, "bytes": n,
                             "reason": f"exceeds per-file cap ({_MAX_ARTIFACT_BYTES} bytes)"})
            continue
        if total + n > _MAX_TOTAL_ARTIFACT_BYTES:
            rejected.append({"name": name, "bytes": n,
                             "reason": "exceeds total artifact budget for sentinel transport"})
            continue
        total += n
        encoded.append({"name": name, "b64": base64.b64encode(data).decode("ascii"), "bytes": n})
    return encoded, rejected


def run_pdf_compose(job: dict) -> dict:
    """Run the PDF-compose script in-container; return a serialisable result.

    Reuses the real ``app.tools.pdf_compose._run_user_script`` (same sandbox +
    ``_safe_exec`` hardening + output-dir delta), then base64-encodes the files
    it produced so they can travel back over the stdout sentinel. Never raises.
    """
    script = job.get("script")
    if not isinstance(script, str) or not script.strip():
        return {"ok": False, "stdout": "", "stderr": "", "result": None,
                "error": "pdf_compose job missing non-empty 'script'",
                "artifacts": [], "rejected_artifacts": []}
    try:
        timeout_s = int(job.get("timeout_s", 60))
    except (TypeError, ValueError):
        timeout_s = 60

    # Imported here (not at module load) so the import cost / any heavy-dep
    # failure is captured as a result rather than crashing the entrypoint.
    from app.tools.pdf_compose import _run_user_script

    out = _run_user_script(script, timeout_s=timeout_s)
    encoded, rejected = _encode_artifacts(out.get("files") or [])
    return {
        "ok": bool(out.get("ok")),
        "stdout": out.get("stdout") or "",
        "stderr": out.get("stderr") or "",
        "result": out.get("result"),
        "error": out.get("error"),
        "artifacts": encoded,
        "rejected_artifacts": rejected,
    }


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raw = os.environ.get("AAI_PDF_COMPOSE_JOB", "")
    if not raw and argv:
        raw = argv[0]
    try:
        job = json.loads(raw) if raw else {}
        out: dict[str, Any] = {"ok": True, "result": run_pdf_compose(job)}
    except Exception as exc:
        logger.exception("pdf_compose_job failed")
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    sys.stdout.write(_RESULT_BEGIN + json.dumps(out) + _RESULT_END + "\n")
    sys.stdout.flush()
    return 0 if out.get("ok") else 1


def extract_result(logs: str) -> dict:
    """Gateway-side: pull the result JSON out of the container's stdout logs."""
    begin = logs.rfind(_RESULT_BEGIN)
    end = logs.rfind(_RESULT_END)
    if begin < 0 or end < 0 or end <= begin:
        raise ValueError("no pdf_compose result sentinel found in logs")
    return json.loads(logs[begin + len(_RESULT_BEGIN) : end])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))

"""experiment_job — the ephemeral-container entrypoint for the auto-research
experiment spine (Phase C).

Runs as ``python3 -m app.research.experiment_job`` inside the SAME throwaway
sandbox image the verified mutation engine uses (``Dockerfile.evolver``), but
with the container's ENTRYPOINT overridden to this module. It owns no
infrastructure: ``app.research.experiment`` (gateway side) spawns the
container through the existing docker-proxy via the generic
``evolver_spawn.run_container_job`` transport, passing this module as the
entrypoint and ``AAI_EXPERIMENT_JOB`` as the job env-var.

Why a SEPARATE entrypoint + sentinels from ``evolver_job``: the two jobs do
unrelated things (a verified mutation vs. an arbitrary measurement script) and
share only the Docker transport mechanics. Distinct sentinels
(``<<<EXPERIMENT_RESULT>>>``) keep ``app.research`` decoupled from
``app.self_improvement`` internals — the only contract between them is the
generic create → start → wait → logs → extract → remove transport.

I/O contract (container-native — no shared volume, per ``VOLUMES: 0`` on the
docker-proxy):
  * **Input**: the job spec arrives as JSON in ``$AAI_EXPERIMENT_JOB``
    (``{"script": <python source>, "timeout_s"?: int}``).
  * **Output**: a single JSON object on stdout, sentinel-wrapped so the
    gateway can extract it from any surrounding log noise::
        ``<<<EXPERIMENT_RESULT>>>{"ok":true,"result":{...}}<<<EXPERIMENT_END>>>``
    The inner ``result`` carries ``ok`` / ``returncode`` / ``stdout`` /
    ``stderr`` / ``timed_out``. All logging goes to stderr.

The script runs as a child ``python3`` process in a fresh temp directory (so
its relative file I/O stays contained) under a hard wallclock timeout. The
container itself supplies the real isolation (memory + pids caps,
no-new-privileges, ephemeral filesystem, OOM-victim score); this entrypoint
just frames the run and reports the outcome. The script is NOT given the repo
on ``sys.path`` — experiments are self-contained measurement scripts over the
baked dependencies, not back-doors into the gateway's own modules.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("experiment_job")

_RESULT_BEGIN = "<<<EXPERIMENT_RESULT>>>"
_RESULT_END = "<<<EXPERIMENT_END>>>"

# Output caps — keep the sentinel line small enough to survive the proxy's log
# read intact, while preserving enough of a measurement script's output to be
# useful. stdout carries the result; stderr carries diagnostics.
_MAX_STDOUT = 16000
_MAX_STDERR = 8000

# Per-experiment wallclock. The job may request less; it may never request
# more than the ceiling — a runaway script must not pin the container for the
# whole container-wait budget.
_DEFAULT_TIMEOUT_S = 300
_MAX_TIMEOUT_S = 600


def _as_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", "replace")
    return str(val)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(truncated; {len(text)} chars total)"


def run_experiment(job: dict) -> dict:
    """Run one experiment script and return the structured outcome.

    Never raises — an unparseable job, a missing script, a timeout, or a
    spawn failure all come back as a ``{"ok": False, ...}`` result dict so the
    gateway-side analyze_result step can record the outcome rather than crash
    the run.
    """
    script = job.get("script")
    if not isinstance(script, str) or not script.strip():
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "experiment job missing non-empty 'script'",
            "timed_out": False,
        }
    try:
        timeout_s = int(job.get("timeout_s", _DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout_s = _DEFAULT_TIMEOUT_S
    timeout_s = max(1, min(timeout_s, _MAX_TIMEOUT_S))

    with tempfile.TemporaryDirectory(prefix="experiment_") as tmp:
        script_path = Path(tmp) / "experiment.py"
        script_path.write_text(script, encoding="utf-8")
        try:
            proc = subprocess.run(
                ["python3", str(script_path)],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            stderr = (_as_text(exc.stderr) + f"\n[timed out after {timeout_s}s]").strip()
            return {
                "ok": False,
                "returncode": -1,
                "stdout": _truncate(_as_text(exc.stdout), _MAX_STDOUT),
                "stderr": _truncate(stderr, _MAX_STDERR),
                "timed_out": True,
            }
        except Exception as exc:  # spawn failure — python3 missing, OOM, …
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
            }

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": _truncate(proc.stdout or "", _MAX_STDOUT),
        "stderr": _truncate(proc.stderr or "", _MAX_STDERR),
        "timed_out": False,
    }


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raw = os.environ.get("AAI_EXPERIMENT_JOB", "")
    if not raw and argv:
        raw = argv[0]
    try:
        job = json.loads(raw) if raw else {}
        out: dict[str, Any] = {"ok": True, "result": run_experiment(job)}
    except Exception as exc:
        logger.exception("experiment_job failed")
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # Single clean result line on stdout, sentinel-wrapped for robust extraction.
    sys.stdout.write(_RESULT_BEGIN + json.dumps(out) + _RESULT_END + "\n")
    sys.stdout.flush()
    return 0 if out.get("ok") else 1


def extract_result(logs: str) -> dict:
    """Gateway-side helper: pull the result JSON out of the container logs."""
    begin = logs.rfind(_RESULT_BEGIN)
    end = logs.rfind(_RESULT_END)
    if begin < 0 or end < 0 or end <= begin:
        raise ValueError("no experiment result sentinel found in logs")
    payload = logs[begin + len(_RESULT_BEGIN) : end]
    return json.loads(payload)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))

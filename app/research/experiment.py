"""app.research.experiment — gateway-side shipper for the experiment spine (Phase C).

Composition only: turns one measurement script into one ephemeral-container
run and returns the structured outcome. It owns no transport — it reuses the
generic ``evolver_spawn.run_container_job`` (create → start → wait → logs →
extract → remove through the existing docker-proxy) with the experiment job
env-var, an entrypoint override, and the experiment result extractor. Same
sandbox image as the verified mutation engine (``Dockerfile.evolver``); only
the baked ENTRYPOINT is swapped for ``app.research.experiment_job``.

The script runs FULLY AUTONOMOUSLY — there is no per-experiment operator gate
— but it is bounded three ways:

  1. the per-run executor :class:`Budget` (enforced by the driver upstream);
  2. the ephemeral sandbox (memory + pids caps, no-new-privileges,
     OOM-victim score, throwaway filesystem) supplied by
     :func:`evolver_spawn.build_create_payload`;
  3. the default-OFF ``research_experiments_enabled`` master switch, checked
     by the caller in :mod:`app.research.run` *before* any script reaches
     this module.

Module load is pure stdlib; the (stdlib-only) transport + entrypoint are
imported lazily inside the function so this stays host-importable.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# A measurement script needs far less than the mutation engine's worktree +
# pytest + judge, so it gets a smaller memory cap.
_EXPERIMENT_MEMORY_BYTES = 2 * 1024**3
_EXPERIMENT_ENTRYPOINT = ["python3", "-m", "app.research.experiment_job"]
_DEFAULT_IMAGE = "botarmy-evolver:latest"


def run_experiment_script(
    script: str,
    *,
    timeout_s: int = 300,
    image: Optional[str] = None,
    transport: Optional[Callable[..., tuple[int, bytes]]] = None,
) -> dict:
    """Run ``script`` in an ephemeral container; return the outcome envelope.

    On a clean run returns the transport envelope::

        {"ok": True, "result": {"ok": bool, "returncode": int,
                                "stdout": str, "stderr": str,
                                "timed_out": bool}}

    and on a spawn/transport failure or an in-container harness error returns
    ``{"ok": False, "error": "..."}``. Never raises — ``run_container_job``
    converts every failure into an error dict so the caller's analyze_result
    step stays simple.
    """
    from app.research import experiment_job
    from app.self_improvement import evolver_spawn

    image = image or os.environ.get("EVOLVER_IMAGE", _DEFAULT_IMAGE)
    job = {"script": script, "timeout_s": int(timeout_s)}
    return evolver_spawn.run_container_job(
        job,
        image=image,
        job_env_var="AAI_EXPERIMENT_JOB",
        entrypoint=_EXPERIMENT_ENTRYPOINT,
        extract_fn=experiment_job.extract_result,
        memory_bytes=_EXPERIMENT_MEMORY_BYTES,
        timeout_s=int(timeout_s),
        transport=transport,  # None → evolver_spawn's default http transport
    )


__all__ = ["run_experiment_script"]

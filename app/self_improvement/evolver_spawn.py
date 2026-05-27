"""evolver_spawn — gateway-side client that runs one verified-mutation job in a
throwaway "evolver" container (2026-05-27).

Talks to the Docker Engine API through the existing ``docker-proxy``
(``DOCKER_HOST=tcp://docker-proxy:2375``; the proxy already allows
``CONTAINERS``/``IMAGES``/``POST`` for the Ollama fleet, so no proxy change is
needed). Stdlib ``urllib`` only — no docker SDK dependency.

Flow: create → start → wait → read logs → extract the sentinel result JSON →
remove. The job spec goes in via the ``AAI_EVOLVE_JOB`` env var; the result
comes back via the container's stdout logs (no shared volume — the proxy runs
with ``VOLUMES: 0``). ``Tty: true`` keeps the logs un-multiplexed for easy
parsing.

This is OPEN/GENERATION-tier orchestration — it spawns the sandbox and relays
the verdict; the verdict itself is computed by the TIER_IMMUTABLE worktree_eval
running inside the container.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from app.self_improvement.evolver_job import extract_result

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = "botarmy-evolver:latest"

# LLM provider keys forwarded into the sandbox so the editor + judge can run.
# Only keys actually present in the gateway env are passed through.
_LLM_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
    "MINIMAX_API_KEY",
    "MISTRAL_API_KEY",
)

# transport(method, path, body=None, timeout=None) -> (status_code, body_bytes)
Transport = Callable[..., tuple[int, bytes]]


def _docker_base() -> str:
    host = os.environ.get("DOCKER_HOST", "tcp://docker-proxy:2375")
    if host.startswith("tcp://"):
        return "http://" + host[len("tcp://") :]
    if host.startswith(("http://", "https://")):
        return host
    return "http://docker-proxy:2375"


def _http_transport(
    method: str, path: str, body: Optional[dict] = None, timeout: Optional[float] = 30
) -> tuple[int, bytes]:
    url = _docker_base() + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def build_create_payload(
    image: str,
    job: dict,
    *,
    extra_env: Optional[dict[str, str]] = None,
    memory_bytes: int = 4 * 1024**3,
    pids_limit: int = 512,
) -> dict[str, Any]:
    """Docker ``POST /containers/create`` body. Pure — unit-tested."""
    env = [f"AAI_EVOLVE_JOB={json.dumps(job)}"]
    for key in _LLM_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env.append(f"{key}={val}")
    for key, val in (extra_env or {}).items():
        env.append(f"{key}={val}")
    return {
        "Image": image,
        "Env": env,
        "Tty": True,  # raw (un-multiplexed) logs → simple sentinel extraction
        "Labels": {"app": "botarmy-evolver"},
        "HostConfig": {
            "AutoRemove": False,  # we read logs after exit, then remove
            "NetworkMode": "bridge",  # default bridge → internet for LLM calls
            "Memory": memory_bytes,
            "PidsLimit": pids_limit,
            "SecurityOpt": ["no-new-privileges:true"],
        },
    }


def run_evolver_job(
    job: dict,
    *,
    image: Optional[str] = None,
    timeout_s: int = 1800,
    transport: Optional[Transport] = None,
) -> dict:
    """Run one job in an ephemeral evolver container; return the result dict.

    Returns ``{"ok": True, "result": {...pipeline verdict...}}`` or
    ``{"ok": False, "error": "..."}``. Never raises — a spawn/transport failure
    becomes an error dict so the caller's gate logic stays simple.
    """
    image = image or os.environ.get("EVOLVER_IMAGE", _DEFAULT_IMAGE)
    tx = transport or _http_transport

    status, data = tx("POST", "/containers/create", build_create_payload(image, job), 30)
    if status not in (200, 201):
        return {"ok": False, "error": f"container create failed ({status}): {data[:200]!r}"}
    try:
        cid = json.loads(data)["Id"]
    except (json.JSONDecodeError, KeyError) as exc:
        return {"ok": False, "error": f"bad create response: {exc}"}

    try:
        status, _ = tx("POST", f"/containers/{cid}/start", None, 30)
        if status not in (200, 204):
            return {"ok": False, "error": f"container start failed ({status})"}

        # Block until the container exits (or our wallclock budget elapses).
        tx("POST", f"/containers/{cid}/wait", None, timeout_s)

        status, logs = tx("GET", f"/containers/{cid}/logs?stdout=1&stderr=1", None, 30)
        if status != 200:
            return {"ok": False, "error": f"could not read container logs ({status})"}
        return extract_result(logs.decode("utf-8", "replace"))
    except Exception as exc:  # transport/timeout/parse — never propagate
        logger.warning("run_evolver_job: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            tx("DELETE", f"/containers/{cid}?force=1", None, 30)
        except Exception:
            logger.debug("run_evolver_job: container %s cleanup failed", cid, exc_info=True)


def image_exists(image: Optional[str] = None, *, transport: Optional[Transport] = None) -> bool:
    """True if the evolver image is present on the host daemon. Lets the caller
    give a clear 'run docker compose --profile evolver build evolver' message."""
    image = image or os.environ.get("EVOLVER_IMAGE", _DEFAULT_IMAGE)
    tx = transport or _http_transport
    try:
        status, _ = tx("GET", f"/images/{image}/json", None, 15)
        return status == 200
    except Exception:
        return False

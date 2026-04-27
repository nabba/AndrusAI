"""Python sandbox runtime — pure compute, no I/O (Phase 2 limitation).

Reuses the existing code_executor's Docker pattern: network disabled, RO
filesystem, mem/CPU caps, all caps dropped. The forged tool's source is
wrapped in a small harness that:

  1. Reads the input parameters JSON from stdin
  2. Calls the tool's ``run(**params)`` function
  3. Prints the JSON-serialised result to stdout

A future phase will add a capability-mediated I/O bridge (the tool would call
``forge.http_get(url)`` which RPCs to the gateway, runs through the HTTP
guard, returns the response). For now: pure compute. Tools that need network
access should be declarative.
"""
from __future__ import annotations

import json
import logging
import pathlib
import tempfile
import time
from typing import Any

from app.config import get_settings
from app.forge.manifest import ToolManifest

logger = logging.getLogger(__name__)


_HARNESS_TEMPLATE = '''# Forge sandbox harness — auto-prepended.
import json, sys, traceback

_PARAMS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {{}}
{user_source}

if __name__ == "__main__":
    try:
        _result = run(**_PARAMS)
    except Exception as exc:
        print(json.dumps({{
            "_forge_error": f"{{type(exc).__name__}}: {{exc}}",
            "_forge_traceback": traceback.format_exc(),
        }}), flush=True)
        sys.exit(1)
    try:
        print(json.dumps({{"_forge_ok": True, "result": _result}}), flush=True)
    except (TypeError, ValueError) as exc:
        print(json.dumps({{
            "_forge_error": f"result not JSON-serialisable: {{exc}}",
        }}), flush=True)
        sys.exit(2)
'''


_WORKSPACE_ROOT = "/app/workspace"


def _to_host_path(container_path: str) -> str:
    settings = get_settings()
    host_ws = settings.workspace_host_path or ""
    if host_ws and container_path.startswith(_WORKSPACE_ROOT):
        return host_ws + container_path[len(_WORKSPACE_ROOT):]
    return container_path


def run_python_sandbox(
    manifest: ToolManifest,
    source_code: str,
    params: dict[str, Any],
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Execute a python_sandbox tool. Pure-compute only in this phase.

    Returns the same shape as run_declarative for symmetry:
      ok, result, error, elapsed_ms, capability_used (always None — no I/O),
      resolved_ip (None), status_code (None).
    """
    import docker

    settings = get_settings()
    timeout = timeout_seconds or settings.sandbox_timeout_seconds

    sandbox_tmpdir = pathlib.Path("/app/workspace/output/.forge_sandbox").resolve()
    sandbox_tmpdir.mkdir(parents=True, exist_ok=True)

    full_source = _HARNESS_TEMPLATE.format(user_source=source_code)
    if len(full_source.encode()) > 512_000:
        return {
            "ok": False,
            "result": None,
            "error": "source too large (max 512 KB)",
            "capability_used": None,
            "resolved_ip": None,
            "status_code": None,
            "elapsed_ms": 0,
        }

    with tempfile.NamedTemporaryFile(
        suffix=".py", dir=sandbox_tmpdir, delete=False, mode="w", encoding="utf-8",
    ) as f:
        f.write(full_source)
        host_path = pathlib.Path(f.name)

    container_path = f"/sandbox/{host_path.name}"
    sandbox_mount_host = _to_host_path(str(sandbox_tmpdir))
    params_json = json.dumps(params or {})

    client = docker.from_env(timeout=10)
    start = time.monotonic()
    output_text = ""
    try:
        result_bytes = client.containers.run(
            settings.sandbox_image,
            command=["python3", container_path, params_json],
            volumes={sandbox_mount_host: {"bind": "/sandbox", "mode": "ro"}},
            network_disabled=True,
            read_only=True,
            mem_limit=settings.sandbox_memory_limit,
            nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            remove=True,
            timeout=timeout,
            stdout=True,
            stderr=True,
        )
        output_text = result_bytes.decode("utf-8", errors="replace")
    except docker.errors.ContainerError as e:
        output_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else str(e)
        return _shape_failure(output_text, start)
    except Exception as exc:
        logger.warning("forge.python_sandbox: container failed: %s", exc)
        return _shape_failure(f"sandbox failed: {type(exc).__name__}: {exc}", start)
    finally:
        try:
            host_path.unlink()
        except OSError:
            pass

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Pull the last JSON line from stdout (harness emits exactly one).
    last_json: dict[str, Any] | None = None
    for line in reversed(output_text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            last_json = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    if last_json is None:
        return {
            "ok": False,
            "result": None,
            "error": f"no JSON output captured; raw: {output_text[:2000]}",
            "capability_used": None,
            "resolved_ip": None,
            "status_code": None,
            "elapsed_ms": elapsed_ms,
        }

    if last_json.get("_forge_error"):
        return {
            "ok": False,
            "result": None,
            "error": str(last_json["_forge_error"]),
            "capability_used": None,
            "resolved_ip": None,
            "status_code": None,
            "elapsed_ms": elapsed_ms,
            "traceback": last_json.get("_forge_traceback"),
        }

    return {
        "ok": True,
        "result": last_json.get("result"),
        "error": None,
        "capability_used": None,
        "resolved_ip": None,
        "status_code": None,
        "elapsed_ms": elapsed_ms,
    }


def _shape_failure(msg: str, start: float) -> dict[str, Any]:
    return {
        "ok": False,
        "result": None,
        "error": msg[:4000],
        "capability_used": None,
        "resolved_ip": None,
        "status_code": None,
        "elapsed_ms": int((time.monotonic() - start) * 1000),
    }

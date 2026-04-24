"""run_host.py — Host-native gateway launcher (bypasses Docker entirely).

Why this exists
===============
The gateway's normal deployment is a Docker container built from
``Dockerfile`` with workspace paths mounted at ``/app/workspace``.
On macOS, repeated ``exit code 137`` SIGKILL events during heavy
research-orchestrator loads (2026-04-25) suggested a Docker-Desktop
level cause we couldn't pin down from inside the container.

This script runs the same gateway app directly on the host Python,
bypassing Docker entirely.  If the host-native run survives a full
research task that the containerized run can't, Docker Desktop is
conclusively the ceiling.

What it does
============
1. Monkey-patches the dozen-or-so ``Path("/app/...")`` constants in
   the codebase BEFORE their owning modules are imported.  This
   avoids a reboot-requiring ``/etc/synthetic.conf`` hack or a
   time-consuming editathon through every hardcoded path.
2. Sets the env vars that would normally be injected by docker-compose
   (DB hosts, signal-cli URL, Ollama base URL) so they point at the
   host's loopback addresses where the DB containers are
   port-published (see ``docker-compose.yml`` temporary ``ports:``
   entries).
3. Starts uvicorn with the normal ``app.main:app`` ASGI target.

Pre-requisites
==============
* Python 3.13 venv at ``.venv/`` with all deps installed
* DB containers running with ports published to 127.0.0.1:
    - postgres:   5432
    - neo4j:      7687
    - chromadb:   8811  (maps container 8000)
* host-bridge (launchd ``com.crewai.bridge``) running on 9100
* signal-cli running on host at 127.0.0.1:7583 (or SIGNAL_HTTP_URL)
* Ollama running on host at 127.0.0.1:11434

Run with
========

    PROJECT_ROOT=/Users/andrus/BotArmy/crewai-team \\
    .venv/bin/python run_host.py
"""
from __future__ import annotations

import os
import pathlib
import sys

# ── 0. Project root ────────────────────────────────────────────────
PROJECT_ROOT = pathlib.Path(
    os.environ.get("PROJECT_ROOT", "/Users/andrus/BotArmy/crewai-team")
).resolve()
if not PROJECT_ROOT.exists():
    print(f"FATAL: PROJECT_ROOT {PROJECT_ROOT} does not exist", file=sys.stderr)
    sys.exit(1)

# ── 1. Ensure the project is on sys.path ───────────────────────────
# So ``from app.main import app`` resolves without needing an install.
sys.path.insert(0, str(PROJECT_ROOT))

# ── 2. Monkey-patch pathlib.Path so ``Path("/app/...")`` anywhere in
#     the codebase transparently redirects to ``PROJECT_ROOT/...``.
# This is a surgical override: only the string prefix is rewritten;
# the resulting Path still behaves like a real Path.
_REAL_PATH_CLS = pathlib.Path
_APP_PREFIX = "/app"
_REPLACEMENT = str(PROJECT_ROOT)


class _RewritingPath(type(_REAL_PATH_CLS)):
    """Metaclass returning a rewired Path when the argument starts
    with ``/app/``.  Transparent for all other inputs."""

    def __call__(cls, *args, **kwargs):
        if args and isinstance(args[0], str) and args[0].startswith(_APP_PREFIX):
            rewritten = _REPLACEMENT + args[0][len(_APP_PREFIX):]
            args = (rewritten,) + args[1:]
        return super().__call__(*args, **kwargs)


# Apply the metaclass override — any future ``Path("/app/foo")`` call
# anywhere in the app code will get re-routed.  Done BEFORE any app
# module imports.
pathlib.Path = _RewritingPath(  # type: ignore[misc]
    "Path", (_REAL_PATH_CLS,), dict(_REAL_PATH_CLS.__dict__),
)

# Also handle PurePosixPath / PosixPath for thoroughness.
for _cls_name in ("PosixPath", "PurePosixPath"):
    _orig = getattr(pathlib, _cls_name)
    setattr(
        pathlib, _cls_name,
        _RewritingPath(_cls_name, (_orig,), dict(_orig.__dict__)),
    )


# ── 2b. Also patch raw ``os.makedirs`` / ``os.mkdir`` / ``open`` ─────
# Some code (app/main.py:588, others) bypasses pathlib and calls
# ``os.makedirs("/app/...")`` with a raw string.  We intercept these
# too so host-run doesn't try to write to the read-only macOS root.
import os as _os_mod
import builtins as _builtins_mod


def _rewrite_path(p):
    """Rewrite a string path starting with /app/ to PROJECT_ROOT/."""
    if isinstance(p, str) and p.startswith(_APP_PREFIX + "/"):
        return _REPLACEMENT + p[len(_APP_PREFIX):]
    if isinstance(p, str) and p == _APP_PREFIX:
        return _REPLACEMENT
    return p


_orig_makedirs = _os_mod.makedirs
_orig_mkdir = _os_mod.mkdir
_orig_rmdir = _os_mod.rmdir
_orig_open = _builtins_mod.open
_orig_os_open = _os_mod.open
_orig_remove = _os_mod.remove
_orig_unlink = _os_mod.unlink
_orig_rename = _os_mod.rename
_orig_replace = _os_mod.replace
_orig_stat = _os_mod.stat
_orig_listdir = _os_mod.listdir
_orig_scandir = _os_mod.scandir
_orig_exists_posix = _os_mod.path.exists
_orig_isdir = _os_mod.path.isdir
_orig_isfile = _os_mod.path.isfile


def _makedirs(path, *args, **kwargs):
    return _orig_makedirs(_rewrite_path(path), *args, **kwargs)


def _mkdir(path, *args, **kwargs):
    return _orig_mkdir(_rewrite_path(path), *args, **kwargs)


def _patched_open(file, *args, **kwargs):
    return _orig_open(_rewrite_path(file), *args, **kwargs)


def _patched_os_open(path, *args, **kwargs):
    return _orig_os_open(_rewrite_path(path), *args, **kwargs)


def _patched_remove(path, *args, **kwargs):
    return _orig_remove(_rewrite_path(path), *args, **kwargs)


def _patched_unlink(path, *args, **kwargs):
    return _orig_unlink(_rewrite_path(path), *args, **kwargs)


def _patched_rename(src, dst, *args, **kwargs):
    return _orig_rename(_rewrite_path(src), _rewrite_path(dst), *args, **kwargs)


def _patched_replace(src, dst, *args, **kwargs):
    return _orig_replace(_rewrite_path(src), _rewrite_path(dst), *args, **kwargs)


def _patched_stat(path, *args, **kwargs):
    return _orig_stat(_rewrite_path(path), *args, **kwargs)


def _patched_listdir(path="."):
    return _orig_listdir(_rewrite_path(path))


def _patched_scandir(path="."):
    return _orig_scandir(_rewrite_path(path))


def _patched_exists(path):
    return _orig_exists_posix(_rewrite_path(path))


def _patched_isdir(path):
    return _orig_isdir(_rewrite_path(path))


def _patched_isfile(path):
    return _orig_isfile(_rewrite_path(path))


def _patched_rmdir(path, *args, **kwargs):
    return _orig_rmdir(_rewrite_path(path), *args, **kwargs)


_os_mod.makedirs = _makedirs
_os_mod.mkdir = _mkdir
_os_mod.rmdir = _patched_rmdir
_os_mod.remove = _patched_remove
_os_mod.unlink = _patched_unlink
_os_mod.rename = _patched_rename
_os_mod.replace = _patched_replace
_os_mod.stat = _patched_stat
_os_mod.listdir = _patched_listdir
_os_mod.scandir = _patched_scandir
_os_mod.open = _patched_os_open
_os_mod.path.exists = _patched_exists
_os_mod.path.isdir = _patched_isdir
_os_mod.path.isfile = _patched_isfile
_builtins_mod.open = _patched_open


# ── 3. Env var overrides that would normally come from docker-compose ──
# Only set if not already in the environment (lets user override).
_DEFAULT_ENV = {
    # DBs — container names → 127.0.0.1 with published ports
    "MEM0_POSTGRES_HOST":    "127.0.0.1",
    "MEM0_POSTGRES_PORT":    "5432",
    "MEM0_NEO4J_URL":        "bolt://127.0.0.1:7687",

    # Host services (Ollama, signal-cli, host-bridge, firecrawl)
    "LOCAL_LLM_BASE_URL":    "http://127.0.0.1:11434",
    "SIGNAL_HTTP_URL":       "http://127.0.0.1:7583",
    "FIRECRAWL_API_URL":     "http://127.0.0.1:3002",
    "HOST_BRIDGE_URL":       "http://127.0.0.1:9100",

    # Reduce the noise-level of some startup warnings about docker
    "DOCKER_HOST":           "unix:///var/run/docker.sock",
}
for k, v in _DEFAULT_ENV.items():
    os.environ.setdefault(k, v)

# ── 4. Load .env for all the other settings (API keys, passwords, etc.) ──
# We use python-dotenv if available, fallback to manual parse.
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)
    except ImportError:
        # Minimal parse — just KEY=VALUE lines.
        for line in _env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

# Hostnames the dockerized app uses for inter-container reach — need
# to be overridden AFTER .env load since .env may contain the docker
# variants.
_HOST_REWRITES = {
    "SIGNAL_HTTP_URL":    "http://127.0.0.1:7583",
    "LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434",
    "FIRECRAWL_API_URL":  "http://127.0.0.1:3002",
}
for k, v in _HOST_REWRITES.items():
    cur = os.environ.get(k, "")
    if "host.docker.internal" in cur or "firecrawl-api" in cur or not cur:
        os.environ[k] = v

# ── 5. Sanity log before handing off to uvicorn ────────────────────
print(f"[run_host] PROJECT_ROOT = {PROJECT_ROOT}")
print(f"[run_host] Path rewrite: /app/* → {PROJECT_ROOT}/*")
print(f"[run_host] MEM0_POSTGRES_HOST = {os.environ['MEM0_POSTGRES_HOST']}")
print(f"[run_host] MEM0_NEO4J_URL = {os.environ['MEM0_NEO4J_URL']}")
print(f"[run_host] SIGNAL_HTTP_URL = {os.environ['SIGNAL_HTTP_URL']}")
print(f"[run_host] LOCAL_LLM_BASE_URL = {os.environ['LOCAL_LLM_BASE_URL']}")
print()

# ── 6. Launch uvicorn ─────────────────────────────────────────────
import uvicorn
uvicorn.run(
    "app.main:app",
    host="127.0.0.1",
    port=int(os.environ.get("GATEWAY_PORT", "8765")),
    reload=False,
    log_level="info",
)

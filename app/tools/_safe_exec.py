"""Hardened exec namespace for agent-authored scripts.

``pdf_compose`` and ``gee_tool`` run LLM-generated Python *in-process* via
``exec(script, namespace)``. With a plain namespace, Python injects the FULL
``__builtins__`` — so a prompt-injected or simply-erroneous script could
``import os; os.system(...)``, ``import socket``/``requests`` to exfiltrate, or
``open('/proc/self/environ')`` / read a token file to lift the gateway's
secrets. (Found in the 2026-06-04 whole-project review.)

This module builds a restricted ``__builtins__`` that:
  * removes dynamic-exec builtins (``eval`` / ``exec`` / ``compile`` /
    ``breakpoint`` / ``input``),
  * installs a guarded ``__import__`` that REFUSES egress / process-spawn /
    introspection-escape modules (subprocess, socket, requests, ctypes,
    multiprocessing, sys, importlib, ...) and hands back a SAFE ``os`` shim
    (``os.path`` + a few filesystem helpers; no ``system``/``popen``/
    ``environ``/``exec*``),
  * guards ``open`` against ``/proc`` · ``/sys`` · ``/dev`` and secret-looking
    paths (token / secret / credential / ``.env`` / ``.key``).

DESIGN INTENT IS PRESERVED: report/geo scripts can still ``import`` their
data/plotting/geo libraries, use ``os.path``, ``open`` files under the
workspace, and call the pre-bound helpers (``plt``/``np``/``ee``/``render_map``).
Legit scripts never need subprocess/socket/os.system.

HONEST CAVEAT — this is a proportionate barrier against the realistic threat
(an LLM emitting ``import os``/``import socket``), NOT a hardened sandbox. A
*crafted* payload can still reach an already-imported dangerous module via
object-subclass traversal. Complete isolation requires running the script in a
separate container with no secret env/mounts — which is incompatible with
these tools' needs (gee requires network + auth; pdf writes the shared output
dir), so it is deliberately not pursued here. Use this for defence-in-depth.
"""
from __future__ import annotations

import builtins as _builtins
import os as _os
import types as _types
from typing import Any

# Modules an agent report/geo script never legitimately needs, but which enable
# secret-reading, network/shell exfiltration, process control, or sandbox
# escape. ``import X`` / ``from X import ...`` for any of these (or a submodule)
# raises ImportError.
_BLOCKED_IMPORTS: frozenset[str] = frozenset({
    # process spawn / native
    "subprocess", "multiprocessing", "pty", "ctypes", "_ctypes", "cffi",
    # network egress
    "socket", "ssl", "ftplib", "smtplib", "telnetlib", "poplib", "imaplib",
    "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "websocket",
    "websockets", "paramiko", "asyncio",
    # serialization / arbitrary load
    "pickle", "marshal", "shelve", "dbm",
    # low-level / escape surface
    "mmap", "fcntl", "resource", "signal", "importlib", "imp", "runpy",
    "code", "codeop", "pdb", "sys", "builtins", "__builtin__", "gc",
})


def _make_os_shim() -> _types.SimpleNamespace:
    """A safe stand-in for ``os``: path ops + benign FS helpers only.

    Excludes ``system`` / ``popen`` / ``exec*`` / ``environ`` / ``fork`` /
    ``kill`` / ``remove`` / ``setuid`` and everything else that spawns,
    networks, or reads secrets.
    """
    shim = _types.SimpleNamespace()
    shim.path = _os.path           # pure path-string + stat helpers (safe)
    shim.sep = _os.sep
    shim.linesep = _os.linesep
    shim.extsep = _os.extsep
    shim.getcwd = _os.getcwd
    shim.listdir = _os.listdir
    shim.makedirs = _os.makedirs
    shim.scandir = _os.scandir
    shim.fspath = _os.fspath
    return shim


# Built once — os.path etc. are stateless.
_OS_SHIM = _make_os_shim()

# Path prefixes / filename substrings that ``open()`` must refuse.
_DENIED_PATH_PREFIXES = ("/proc", "/sys", "/dev")
_DENIED_NAME_SUBSTRINGS = (
    "token", "secret", "credential", "password", ".env", ".key", ".pem", "id_rsa",
)


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = (name or "").split(".")[0]
    if name in _BLOCKED_IMPORTS or root in _BLOCKED_IMPORTS:
        raise ImportError(
            f"import of '{name}' is not permitted in sandboxed agent scripts"
        )
    if name == "os" or root == "os":
        # Hand back the safe shim instead of the real os module. ``import os``
        # and ``from os import path`` keep working; ``os.system`` etc. vanish.
        return _OS_SHIM
    return _builtins.__import__(name, globals, locals, fromlist, level)


def _guarded_open(file, *args, **kwargs):
    try:
        p = _os.fspath(file)
        rp = _os.path.realpath(p) if isinstance(p, str) else ""
        low = rp.lower()
        if any(rp.startswith(pre) for pre in _DENIED_PATH_PREFIXES) or any(
            sub in low for sub in _DENIED_NAME_SUBSTRINGS
        ):
            raise PermissionError(f"open('{p}') is blocked in sandboxed scripts")
    except (TypeError, ValueError):
        pass  # non-path file objects (fd ints) fall through to real open
    return _builtins.open(file, *args, **kwargs)


# Direct builtins removed from the script namespace (dynamic-exec / debug).
_DENIED_BUILTINS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "breakpoint", "input", "__import__", "open",
})


def restricted_builtins() -> dict[str, Any]:
    """Return a ``__builtins__`` mapping safe to hand an ``exec(script, ns)``."""
    safe = {k: v for k, v in vars(_builtins).items() if k not in _DENIED_BUILTINS}
    safe["__import__"] = _guarded_import
    safe["open"] = _guarded_open
    return safe


def harden(namespace: dict[str, Any]) -> dict[str, Any]:
    """Install the restricted ``__builtins__`` on an exec namespace (in place).

    Call right before ``exec(script, namespace)``. Pre-bound helpers already in
    ``namespace`` (plt/np/ee/render_map/...) are untouched and keep working —
    they run with their own modules' builtins, not the script's.
    """
    namespace["__builtins__"] = restricted_builtins()
    return namespace

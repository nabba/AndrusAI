"""Tests for app.tools._safe_exec — the hardened exec namespace used by
pdf_compose + gee_tool to run agent-authored scripts (whole-project review
2026-06-04).

Verifies the restriction blocks the realistic prompt-injection / LLM-error
exfil vectors while leaving legitimate report/geo scripting intact.
"""
import pytest

from app.tools._safe_exec import harden, restricted_builtins


def _exec(script: str, ns: dict | None = None) -> dict:
    ns = harden(ns if ns is not None else {})
    exec(script, ns)
    return ns


# ── blocked: egress / process-spawn / escape imports ──────────────────────
@pytest.mark.parametrize("mod", [
    "subprocess", "socket", "requests", "urllib", "urllib.request",
    "http", "ctypes", "multiprocessing", "pty", "sys", "importlib",
    "pickle", "ftplib", "smtplib",
])
def test_blocks_dangerous_import(mod):
    with pytest.raises(ImportError):
        _exec(f"import {mod}")


def test_blocks_from_import_of_blocked():
    with pytest.raises(ImportError):
        _exec("from subprocess import Popen")


# ── os → safe shim (path ok; system/environ gone) ─────────────────────────
def test_os_import_returns_shim_with_path():
    ns = _exec("import os\nresult = os.path.join('a', 'b')")
    assert ns["result"] == "a/b"


def test_os_system_is_gone():
    with pytest.raises(AttributeError):
        _exec("import os\nos.system('echo pwned')")


def test_os_environ_is_gone():
    with pytest.raises(AttributeError):
        _exec("import os\nx = os.environ")


# ── dynamic-exec builtins removed ─────────────────────────────────────────
@pytest.mark.parametrize("call", ["eval('1+1')", "exec('x=1')", "compile('1','<s>','eval')"])
def test_dynamic_exec_builtins_removed(call):
    with pytest.raises(NameError):
        _exec(call)


# ── open guarded against /proc + secret files ─────────────────────────────
def test_open_blocks_proc_environ():
    with pytest.raises(PermissionError):
        _exec("open('/proc/self/environ').read()")


def test_open_blocks_secret_filenames():
    for path in ("/app/workspace/google_token.json", "/x/my.env", "/x/api.key", "/x/credentials.json"):
        with pytest.raises(PermissionError):
            _exec(f"open({path!r})")


def test_open_allows_normal_file(tmp_path):
    p = tmp_path / "report_data.csv"
    _exec(f"open({str(p)!r}, 'w').write('hello')")
    assert p.read_text() == "hello"


# ── legitimate scripting still works ──────────────────────────────────────
def test_allows_safe_imports_and_compute():
    ns = _exec("import json, math\nresult = json.dumps({'r': math.sqrt(4)})")
    assert ns["result"] == '{"r": 2.0}'


def test_normal_computation():
    ns = _exec("result = sum(range(10))")
    assert ns["result"] == 45


def test_prebound_helpers_survive_harden():
    # Pre-bound objects (plt/np/ee/render_map analog) must remain usable.
    sentinel = {"called": False}

    def helper():
        sentinel["called"] = True
        return 42

    ns = _exec("result = helper()", {"helper": helper})
    assert ns["result"] == 42 and sentinel["called"] is True


def test_restricted_builtins_keeps_core_names():
    b = restricted_builtins()
    for name in ("len", "range", "dict", "list", "print", "sum", "min", "max", "__import__"):
        assert name in b, f"{name} must remain available to scripts"
    for name in ("eval", "exec", "compile"):
        assert name not in b, f"{name} must be removed"

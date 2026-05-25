"""chromadb smoke runner (Gap 2 reference implementation).

What this exercises that pytest does not:

  * Whether the BUMPED chromadb library — installed in the trial venv,
    not the gateway venv — can open a real production snapshot SQLite
    file. A bump that changes the on-disk format or vendored sqlite
    version will fail HERE while the unit-test suite (which builds
    fresh fixtures each run) still passes.
  * Whether ``PersistentClient.list_collections()`` round-trips a
    real KB without raising. The dual-writer corruption incident of
    2026-04-25 and the chain-race incident of 2026-05-23 both showed
    in this surface first.

Mechanism — runs in the trial venv as a subprocess:

  1. Locate the newest ``.sqlite_snapshots/*.db`` under WORKSPACE_ROOT
     (any KB; the snapshots are produced daily by
     :mod:`app.memory.chromadb_integrity`).
  2. Copy the snapshot to a private scratch dir under the sandbox as
     ``chroma.sqlite3`` (the filename chromadb's PersistentClient
     expects). Reading from the original would deadlock if a backup is
     concurrently writing; the copy is cheap (~50 MB worst case).
  3. Subprocess: ``<venv-python> -c <snippet> <scratch-dir>``. The
     snippet imports chromadb, opens PersistentClient, lists
     collections, and prints a single JSON line.
  4. Parse output → SmokeResult dict.

Failure-isolated: anything raising in the runner is caught by the
trial harness; we additionally catch our own subprocess + JSON errors
here so the dict shape is always well-formed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from app.upgrade_lifecycle.smokes import register

logger = logging.getLogger(__name__)


# Injectable for tests — production runs the real subprocess.
SubprocessRunner = Callable[[list[str], int], "subprocess.CompletedProcess"]
SnapshotLocator = Callable[[], Optional[Path]]


_SMOKE_TIMEOUT_S = 60
_SNAPSHOT_DIRNAME = ".sqlite_snapshots"
_TARGET_FILENAME = "chroma.sqlite3"
_SUBPROCESS_SCRIPT = """
import sys, json
try:
    import chromadb
    c = chromadb.PersistentClient(path=sys.argv[1])
    cols = c.list_collections()
    out = {"ok": True, "collections": len(cols)}
except Exception as exc:
    out = {"ok": False, "error": str(exc)[:300]}
sys.stdout.write(json.dumps(out))
"""


def _default_locate_snapshot() -> Optional[Path]:
    """Find the newest ``.sqlite_snapshots/*.db`` under WORKSPACE_ROOT.

    Walks one level deep — each KB owns a ``.sqlite_snapshots`` dir,
    and we want the most recent across all of them. None when no
    snapshot exists yet (first-run grace; the smoke skips with status
    ``ok``)."""
    try:
        from app.paths import WORKSPACE_ROOT
        root = Path(WORKSPACE_ROOT)
    except Exception:
        # Fall back to the canonical container path so the smoke can
        # still run under deployment defaults.
        root = Path(os.getenv("WORKSPACE_ROOT", "/app/workspace"))
    if not root.exists():
        return None
    newest: Optional[Path] = None
    newest_mtime = 0.0
    for snap_dir in root.glob(f"*/{_SNAPSHOT_DIRNAME}"):
        if not snap_dir.is_dir():
            continue
        for db_file in snap_dir.glob("*.db"):
            try:
                mtime = db_file.stat().st_mtime
            except OSError:
                continue
            if mtime > newest_mtime:
                newest_mtime = mtime
                newest = db_file
    return newest


def _default_venv_python(sandbox: Path) -> Path:
    """Mirror the trial_runner venv layout — POSIX bin/ first, then
    Windows Scripts/."""
    posix = sandbox / ".trial_venv" / "bin" / "python"
    if posix.exists():
        return posix
    win = sandbox / ".trial_venv" / "Scripts" / "python.exe"
    if win.exists():
        return win
    return posix


def _default_subprocess_runner(argv: list[str],
                                timeout_s: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, timeout=timeout_s, capture_output=True, text=True,
    )


def run(
    sandbox: Path,
    *,
    locate_snapshot: SnapshotLocator = _default_locate_snapshot,
    venv_python: Optional[Callable[[Path], Path]] = None,
    subprocess_runner: SubprocessRunner = _default_subprocess_runner,
) -> dict[str, Any]:
    """Execute the chromadb smoke.

    Returns a SmokeResult dict; never raises. All errors collapse to
    ``status="error"`` with ``details`` carrying the diagnostic.
    """
    result: dict[str, Any] = {
        "name": "chromadb",
        "status": "error",
        "details": "",
    }

    snap = None
    try:
        snap = locate_snapshot()
    except Exception as exc:
        result["details"] = f"locate raised: {exc!s}"[:200]
        return result

    if snap is None or not snap.exists():
        # No snapshot yet → first-run grace. Report ok so the trial
        # isn't penalized; details note the skip.
        result["status"] = "ok"
        result["details"] = "no snapshot available; smoke skipped"
        return result

    scratch = sandbox / ".chromadb_smoke"
    target = scratch / _TARGET_FILENAME
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        shutil.copy(snap, target)
    except Exception as exc:
        result["details"] = f"snapshot copy failed: {exc!s}"[:200]
        return result

    vp = venv_python or _default_venv_python
    python_bin = vp(sandbox)
    if not python_bin.exists():
        result["details"] = f"venv python missing at {python_bin}"
        return result

    try:
        cp = subprocess_runner(
            [str(python_bin), "-c", _SUBPROCESS_SCRIPT, str(scratch)],
            _SMOKE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        result["details"] = f"smoke subprocess timed out >{_SMOKE_TIMEOUT_S}s"
        return result
    except Exception as exc:
        result["details"] = f"subprocess launch failed: {exc!s}"[:200]
        return result

    stdout = (cp.stdout or "").strip()
    if cp.returncode != 0 and not stdout:
        result["status"] = "fail"
        result["details"] = (cp.stderr or "")[:300] or f"rc={cp.returncode}"
        return result

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        result["details"] = f"smoke output not JSON: {stdout[:200]!r}"
        return result

    if parsed.get("ok"):
        result["status"] = "ok"
        result["details"] = f"opened snapshot, {parsed.get('collections', 0)} collections"
        result["collections"] = int(parsed.get("collections", 0) or 0)
    else:
        result["status"] = "fail"
        result["details"] = str(parsed.get("error") or "client refused snapshot")[:300]
    return result


# Auto-register at module import so anyone who explicitly imports this
# module wires the runner in. Discovery is therefore explicit (operator
# adds the import in their bootstrap) rather than implicit-by-package-
# name. Idempotent against re-import.
register("chromadb", run)


__all__ = ["run"]

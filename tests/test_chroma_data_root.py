"""Pinning tests for the CHROMA_DATA_ROOT named-volume split (Phase 1a).

ChromaDB KBs are derived artifacts (PROGRAM §56) and may live on a Docker
named volume at CHROMA_DATA_ROOT while source ledgers, snapshots and texts
stay on the workspace bind mount. These tests pin:

  1. Identity default — env unset ⇒ chroma paths == workspace paths
     (host-native dev + every existing test keeps its exact behavior).
  2. Env redirect — CHROMA_DATA_ROOT set ⇒ chroma_kb_dir/chroma_root and
     every per-KB config resolve under it.
  3. Per-KB env overrides (EPISTEME_CHROMA_DIR, …) still win over the root.
  4. Snapshot placement — split active ⇒ snapshots land under
     workspace/<kb>/.sqlite_snapshots (host-visible), never on the volume.
  5. chromadb_kbs() discovery walks the chroma root.
  6. Compose: gateway mounts chroma_data; the worker service must NEVER
     gain the mount or the env (single-writer discipline, §55).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_KB_CONFIG_MODULES = {
    "episteme": ("app.episteme.config", "EPISTEME_CHROMA_DIR"),
    "experiential": ("app.experiential.config", "EXPERIENTIAL_CHROMA_DIR"),
    "philosophy": ("app.philosophy.config", "PHIL_CHROMA_DIR"),
    "knowledge": ("app.knowledge_base.config", "KB_CHROMA_DIR"),
    "tensions": ("app.tensions.config", "TENSIONS_CHROMA_DIR"),
    "aesthetics": ("app.aesthetics.config", "AESTHETICS_CHROMA_DIR"),
}


def _reload_paths(monkeypatch, chroma_root: str | None, workspace: str | None = None):
    """Reload app.paths with a controlled environment; returns the module."""
    if workspace is not None:
        monkeypatch.setenv("WORKSPACE_ROOT", workspace)
    if chroma_root is None:
        monkeypatch.delenv("CHROMA_DATA_ROOT", raising=False)
    else:
        monkeypatch.setenv("CHROMA_DATA_ROOT", chroma_root)
    import app.paths
    return importlib.reload(app.paths)


@pytest.fixture(autouse=True)
def _restore_modules():
    """Reload app.paths + per-KB configs back to ambient env after each test
    so module-level constants don't leak into sibling tests."""
    yield
    import app.paths
    importlib.reload(app.paths)
    for mod_name, _ in _KB_CONFIG_MODULES.values():
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])


# ── 1. Identity default ───────────────────────────────────────────────────


def test_default_is_identity_with_workspace_root(monkeypatch, tmp_path):
    paths = _reload_paths(monkeypatch, None, workspace=str(tmp_path))
    assert paths.CHROMA_DATA_ROOT == paths.WORKSPACE_ROOT
    assert paths.chroma_kb_dir("memory") == paths.WORKSPACE_ROOT / "memory"
    assert paths.chroma_root() == paths.WORKSPACE_ROOT


def test_empty_env_is_identity(monkeypatch, tmp_path):
    paths = _reload_paths(monkeypatch, "", workspace=str(tmp_path))
    assert paths.CHROMA_DATA_ROOT == paths.WORKSPACE_ROOT


# ── 2. Env redirect ───────────────────────────────────────────────────────


def test_env_redirects_chroma_kb_dir(monkeypatch, tmp_path):
    croot = tmp_path / "chroma"
    paths = _reload_paths(monkeypatch, str(croot), workspace=str(tmp_path / "ws"))
    assert paths.CHROMA_DATA_ROOT == croot.resolve()
    assert paths.chroma_kb_dir("episteme") == croot.resolve() / "episteme"
    # Workspace paths must NOT move.
    assert paths.WORKSPACE_ROOT == (tmp_path / "ws").resolve()


@pytest.mark.parametrize("kb", sorted(_KB_CONFIG_MODULES))
def test_per_kb_configs_follow_chroma_root(monkeypatch, tmp_path, kb):
    pytest.importorskip("chromadb")  # package __init__ pulls in vectorstores
    mod_name, override_env = _KB_CONFIG_MODULES[kb]
    croot = tmp_path / "chroma"
    monkeypatch.delenv(override_env, raising=False)
    _reload_paths(monkeypatch, str(croot), workspace=str(tmp_path / "ws"))
    mod = importlib.import_module(mod_name)
    mod = importlib.reload(mod)
    assert mod.CHROMA_PERSIST_DIR == str(croot.resolve() / kb)


@pytest.mark.parametrize("kb", sorted(_KB_CONFIG_MODULES))
def test_per_kb_env_override_still_wins(monkeypatch, tmp_path, kb):
    pytest.importorskip("chromadb")  # package __init__ pulls in vectorstores
    mod_name, override_env = _KB_CONFIG_MODULES[kb]
    croot = tmp_path / "chroma"
    custom = str(tmp_path / "custom" / kb)
    monkeypatch.setenv(override_env, custom)
    _reload_paths(monkeypatch, str(croot), workspace=str(tmp_path / "ws"))
    mod = importlib.import_module(mod_name)
    mod = importlib.reload(mod)
    assert mod.CHROMA_PERSIST_DIR == custom


# ── 4. Snapshot placement ─────────────────────────────────────────────────


def test_snapshot_dir_legacy_next_to_db(monkeypatch, tmp_path):
    """Identity mode: snapshots stay next to the database (existing
    behavior — keeps every pre-split test and dev workflow intact)."""
    _reload_paths(monkeypatch, None, workspace=str(tmp_path))
    from app.memory import chromadb_integrity as ci
    importlib.reload(ci)
    db = tmp_path / "memory" / "chroma.sqlite3"
    assert ci._snapshot_dir_for(db) == db.parent / ".sqlite_snapshots"


def test_snapshot_dir_split_redirects_to_workspace(monkeypatch, tmp_path):
    """Split mode: a db on the volume snapshots to workspace/<kb>/ —
    host-visible + warm-spare-replicated."""
    ws = tmp_path / "ws"
    croot = tmp_path / "chroma"
    (croot / "memory").mkdir(parents=True)
    _reload_paths(monkeypatch, str(croot), workspace=str(ws))
    from app.memory import chromadb_integrity as ci
    importlib.reload(ci)
    db = croot / "memory" / "chroma.sqlite3"
    expect = ws.resolve() / "memory" / ".sqlite_snapshots"
    assert ci._snapshot_dir_for(db) == expect
    # A db OUTSIDE the chroma root (drill scratch dirs) keeps legacy placement.
    scratch = tmp_path / "scratch" / "kbx" / "chroma.sqlite3"
    assert ci._snapshot_dir_for(scratch) == scratch.parent / ".sqlite_snapshots"


# ── 5. Discovery walks the chroma root ────────────────────────────────────


def test_chromadb_kbs_walks_chroma_root(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    croot = tmp_path / "chroma"
    for kb in ("memory", "episteme"):
        d = croot / kb
        d.mkdir(parents=True)
        (d / "chroma.sqlite3").write_bytes(b"x")
    # Quarantined dirs are skipped.
    bad = croot / "memory.corrupt_20260101_000000"
    bad.mkdir()
    (bad / "chroma.sqlite3").write_bytes(b"x")
    # A KB left on the workspace must NOT be discovered once split is active.
    stale = ws / "philosophy"
    stale.mkdir(parents=True)
    (stale / "chroma.sqlite3").write_bytes(b"x")

    _reload_paths(monkeypatch, str(croot), workspace=str(ws))
    from app.memory import chromadb_integrity as ci
    importlib.reload(ci)
    found = {p.parent.name for p in ci.chromadb_kbs()}
    assert found == {"memory", "episteme"}


# ── 6. Compose pins ───────────────────────────────────────────────────────


_COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

# In-image test runs mount only app/ + tests/ — the compose file lives in
# the repo checkout. Skip there; host + CI-with-checkout enforce the pins.
_needs_compose = pytest.mark.skipif(
    not _COMPOSE_PATH.exists(), reason="docker-compose.yml not in this context"
)


def _compose_text() -> str:
    return _COMPOSE_PATH.read_text(encoding="utf-8")


def _service_block(text: str, name: str) -> str:
    """Crude but stable: slice from `  <name>:` to the next 2-space key."""
    import re
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \w|\Z)", text, re.S | re.M)
    assert m, f"service {name} not found in docker-compose.yml"
    return m.group(0)


@_needs_compose
def test_compose_defines_chroma_volume_and_gateway_mount():
    text = _compose_text()
    assert "chroma_data:" in text.split("volumes:")[-1], (
        "top-level chroma_data volume missing"
    )
    gw = _service_block(text, "gateway")
    assert "chroma_data:/chroma" in gw, "gateway must mount chroma_data at /chroma"


@_needs_compose
def test_worker_never_gets_chroma_mount_or_env():
    """Single-writer discipline (§55): the worker must never see the chroma
    volume. _guard_worker() fail-closes in code; this pins the physical layer."""
    text = _compose_text()
    wk = _service_block(text, "worker")
    assert "chroma_data" not in wk, "worker must NOT mount the chroma volume"
    assert "CHROMA_DATA_ROOT" not in wk, "worker must NOT set CHROMA_DATA_ROOT"

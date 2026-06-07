"""Worker-mode ChromaDB proxy tests (serving/compute split, Increment 3).

Pins the §55-critical invariants: the proxy NEVER opens ChromaDB, writes go to
the source ledger (embeddings dropped) and succeed even with the gateway down,
reads route to the gateway and are fail-soft, and a worker must not destroy a
collection. Pure logic — source_ledger + HTTP are monkeypatched, so host-runnable.
"""
import sys
import types

import pytest

kb_proxy = pytest.importorskip("app.memory.kb_proxy")


@pytest.fixture(autouse=True)
def _no_replay(monkeypatch):
    # Don't spawn real replay-trigger threads during unit tests.
    monkeypatch.setattr(kb_proxy, "_trigger_replay", lambda kb: None)


def test_proxy_never_opens_chromadb():
    import inspect
    src = inspect.getsource(kb_proxy)
    assert "import chromadb" not in src, "proxy must not import chromadb"
    assert "PersistentClient(" not in src, "proxy must never construct a chromadb client"


def test_is_worker(monkeypatch):
    monkeypatch.setenv("IDLE_SCHEDULER_ROLE", "worker")
    assert kb_proxy._is_worker() is True
    monkeypatch.setenv("IDLE_SCHEDULER_ROLE", "gateway")
    assert kb_proxy._is_worker() is False


def test_proxy_client_is_cached_per_kb():
    c1 = kb_proxy.proxy_client_for_kb("episteme")
    assert kb_proxy.proxy_client_for_kb("episteme") is c1
    assert kb_proxy.proxy_client_for_kb("memory") is not c1


def _fake_ledger(monkeypatch, sink: dict):
    fake = types.SimpleNamespace(
        hook_collection_add=lambda kb, col, ids, docs, metas=None: sink.update(add=(kb, col, ids, docs, metas)),
        hook_collection_update=lambda kb, col, ids, docs=None, metas=None: sink.update(update=(kb, col, ids, docs, metas)),
        hook_collection_delete=lambda kb, col, ids: sink.update(delete=(kb, col, ids)),
    )
    monkeypatch.setitem(sys.modules, "app.memory.source_ledger", fake)


def test_add_writes_ledger_dropping_embeddings(monkeypatch):
    sink: dict = {}
    _fake_ledger(monkeypatch, sink)
    col = kb_proxy._ProxyCollection("episteme", "episteme_research")
    col.add(ids=["t1"], documents=["doc text"], metadatas=[{"k": "v"}], embeddings=[[0.0, 1.0]])
    assert sink["add"] == ("episteme", "episteme_research", ["t1"], ["doc text"], [{"k": "v"}])
    # embeddings are NOT forwarded — the gateway re-embeds on replay (§56).


def test_upsert_recorded_as_add(monkeypatch):
    sink: dict = {}
    _fake_ledger(monkeypatch, sink)
    kb_proxy._ProxyCollection("memory", "c").upsert(ids=["a"], documents=["x"])
    assert sink["add"][0:3] == ("memory", "c", ["a"])


def test_write_succeeds_when_gateway_unreachable(monkeypatch):
    sink: dict = {}
    _fake_ledger(monkeypatch, sink)
    # Even if every gateway call would fail, the local ledger write must land.
    monkeypatch.setattr(kb_proxy, "_internal_post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    kb_proxy._ProxyCollection("memory", "c").add(ids=["x"], documents=["y"])
    assert sink.get("add") is not None


def test_query_routes_to_gateway_with_embeddings(monkeypatch):
    captured: dict = {}

    def fake_post(path, body, timeout=30):
        captured["path"] = path
        captured["body"] = body
        return {"result": {"ids": [["t1"]], "documents": [["d"]], "metadatas": [[{}]], "distances": [[0.1]]}}

    monkeypatch.setattr(kb_proxy, "_internal_post", fake_post)
    res = kb_proxy._ProxyCollection("episteme", "c").query(query_embeddings=[[0.1, 0.2]], n_results=2)
    assert captured["path"] == "/internal/kb/collection/query"
    assert captured["body"]["query_embeddings"] == [[0.1, 0.2]]
    assert res["documents"] == [["d"]]


def test_query_fail_soft(monkeypatch):
    monkeypatch.setattr(kb_proxy, "_internal_post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    res = kb_proxy._ProxyCollection("episteme", "c").query(query_embeddings=[[0.1]], n_results=2)
    assert res == kb_proxy._EMPTY_QUERY  # degraded RAG context, not a crash


def test_delete_collection_is_suppressed():
    client = kb_proxy.proxy_client_for_kb("episteme")
    client.delete_collection("anything")  # must not raise / must not destroy
    assert client.list_collections() == []

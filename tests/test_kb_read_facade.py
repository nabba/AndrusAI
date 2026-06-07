"""Routing tests for the worker-safe KB read facade (serving/compute split).

Pins that ``kb_read.query`` routes to the gateway over HTTP in the worker
process and to the local store on the gateway, and that it is fail-soft. Pure
routing logic — chromadb/network are monkeypatched, so this runs on the host.
"""
import pytest

kb_read = pytest.importorskip("app.memory.kb_read")


def test_is_worker_honors_env(monkeypatch):
    monkeypatch.setenv("IDLE_SCHEDULER_ROLE", "worker")
    assert kb_read._is_worker() is True
    monkeypatch.setenv("IDLE_SCHEDULER_ROLE", "gateway")
    assert kb_read._is_worker() is False
    monkeypatch.delenv("IDLE_SCHEDULER_ROLE", raising=False)
    assert kb_read._is_worker() is False  # default "all" → not worker


def _boom(*_a, **_k):
    raise AssertionError("wrong branch taken")


def test_query_routes_remote_in_worker(monkeypatch):
    monkeypatch.setenv("IDLE_SCHEDULER_ROLE", "worker")
    monkeypatch.setattr(kb_read, "_query_remote", lambda kb, qt, n, where=None: [{"text": "via-gateway"}])
    monkeypatch.setattr(kb_read, "query_local", _boom)
    assert kb_read.query("episteme", "q", 2) == [{"text": "via-gateway"}]


def test_query_routes_local_on_gateway(monkeypatch):
    monkeypatch.setenv("IDLE_SCHEDULER_ROLE", "gateway")
    monkeypatch.setattr(kb_read, "query_local", lambda kb, qt, n, where=None: [{"text": "local"}])
    monkeypatch.setattr(kb_read, "_query_remote", _boom)
    assert kb_read.query("episteme", "q", 2) == [{"text": "local"}]


def test_query_is_fail_soft(monkeypatch):
    monkeypatch.setenv("IDLE_SCHEDULER_ROLE", "worker")
    monkeypatch.setattr(kb_read, "_query_remote", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net down")))
    # A transport failure degrades RAG context to [], never crashes the job.
    assert kb_read.query("episteme", "q", 2) == []


def test_query_local_rejects_unknown_kb():
    with pytest.raises(ValueError):
        kb_read.query_local("not-a-kb", "q", 1)

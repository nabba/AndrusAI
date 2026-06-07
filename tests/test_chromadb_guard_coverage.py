"""Pin: every per-KB vectorstore opens chromadb through the guarded,
process-cached ``get_client_for_path`` — NOT a raw ``chromadb.PersistentClient``.

Serving/compute-split safety invariant. The ``IDLE_SCHEDULER_ROLE=worker`` guard
(``app/memory/chromadb_manager._guard_worker``) only fires inside ``get_client``
/ ``get_kb_client`` / ``get_client_for_path``. A vectorstore that constructs
``chromadb.PersistentClient(...)`` directly BYPASSES the guard, so in the worker
process it would silently open a second writer on a KB and reintroduce the §55
dual-writer corruption. Closing that gap (2026-06-07) routed the 5 remaining
per-KB stores through ``get_client_for_path``; this test keeps every KB
vectorstore there.

Pure stdlib source-grep so it runs on the host (no chromadb/pydantic import).
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# Every per-KB vectorstore that opens a chromadb client for a workspace KB dir.
_KB_VECTORSTORES = [
    "app/episteme/vectorstore.py",
    "app/experiential/vectorstore.py",
    "app/philosophy/vectorstore.py",
    "app/aesthetics/vectorstore.py",
    "app/tensions/vectorstore.py",
    "app/knowledge_base/vectorstore.py",
]


def test_kb_vectorstores_route_through_guarded_accessor():
    offenders = []
    for rel in _KB_VECTORSTORES:
        src = (_REPO / rel).read_text()
        if "chromadb.PersistentClient(" in src:
            offenders.append(
                f"{rel}: opens chromadb.PersistentClient directly — bypasses the "
                f"IDLE_SCHEDULER_ROLE=worker guard (§55 dual-writer risk). Route "
                f"through app.memory.chromadb_manager.get_client_for_path."
            )
        if "get_client_for_path" not in src:
            offenders.append(f"{rel}: does not use get_client_for_path")
    assert not offenders, (
        "KB vectorstore guard-coverage regression:\n" + "\n".join(offenders)
    )

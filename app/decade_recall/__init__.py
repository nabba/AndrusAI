"""decade_recall — unified audit-log index across all hash-chained
trails. Gap 4 of the 2026-05-24 ultrathink analysis closure.

What it indexes
===============

The system maintains 6+ hash-chained audit/ledger files. Each captures
a different dimension of the system's history:

  * ``identity/continuity_ledger.jsonl`` — identity-shaping events
    (substrate migrations, drift acceleration, sentience observations,
    21 IDENTITY_EVENT_KIND surfaces).
  * ``change_requests/audit.jsonl`` — every CR transition (created /
    approved / applied / rolled-back).
  * ``resilience/drill_audit.jsonl`` — every drill run + state
    transition + baseline ratification.
  * ``autonomous_executor/audit.jsonl`` — every executor run +
    milestone + status flip.
  * ``self_model/agreement_ledger.jsonl`` — operator agreement record
    over the system's proactive suggestions.
  * ``governance/audit.jsonl`` — Tier-3 amendment lifecycle (when
    present; depends on env / version).

Q17.8 conversation_memory already covers the canonical
``audit.log`` request_received chain. ``decade_recall`` is its
sibling for the OTHER six chains — together they answer "what
happened across the whole system over the last decade?"

How it works
============

  1. Incremental scan — each source has its own scan-cursor JSON
     at ``workspace/decade_recall/cursors.json``. Daily idle job
     picks up new rows; idempotent.
  2. Compact searchable index at
     ``workspace/decade_recall/index.jsonl``. One row per source row.
     Schema: ``{ts, scope, kind, ref, preview, tokens}``.
  3. PII redaction at the scan edge — email / phone tokenized to
     ``<email>``/``<phone>`` BEFORE the row enters the index.
  4. Token-overlap retrieval (same design as Q17.8 — deliberately
     NOT a vector index, so robust against embedding-model rotation).
  5. Vendor-rotation tolerant: no LLM, no embedding model. Pure
     stdlib + regex.

Why a separate index from conversation_memory
=============================================

  * Different sources, different schemas. The conversation_memory
    code is tightly coupled to ``audit.log``'s shape — extending it
    to 6 more chains would muddy that boundary.
  * Scope-filterable retrieval needs a discriminator column. The
    conversation_memory index doesn't have one and adding it would
    break backward compatibility.
  * Different cadence. Conversation_memory scans every 10 minutes
    (high-volume); ledger scans daily (low-volume) is enough.

What it does NOT do
===================

  * **No semantic search.** Token overlap is the retrieval model.
    "Find anything about the X migration" works; "find me anything
    SIMILAR to the X migration" doesn't. That's a deliberate
    design choice for decade-scale robustness — vector indices
    drift silently with model upgrades.
  * **No content modification.** Read-only on every ledger source.
    Indexing failures never block the underlying ledgers.
  * **No de-duplication.** Each source row is one index row, even
    if the row appears semantically similar to others. The agent
    or operator dedupes downstream.

Master switch: ``decade_recall_enabled`` (default ON).
"""
from __future__ import annotations

from app.decade_recall.retrieval import (
    AuditReference,
    recall_history,
    summary,
)
from app.decade_recall.indexer import (
    SOURCES,
    rebuild_index,
    run_scan,
    scan_all_sources,
)

__all__ = [
    "AuditReference",
    "SOURCES",
    "rebuild_index",
    "recall_history",
    "run_scan",
    "scan_all_sources",
    "summary",
]

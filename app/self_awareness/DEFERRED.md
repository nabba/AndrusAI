# Phase 1 DEFER list — not migrated to app/subia/

The Phase 1 layout migration moved 34 modules from `app/consciousness/`
and `app/self_awareness/` into `app/subia/` via sys.modules-alias shims.
These 5 files were intentionally **not** migrated because they are not
part of the SubIA consciousness surface. They belong in other locations
to be chosen during dedicated clean-up work, not as part of the unified
consciousness program.

Each entry below records **why** the module was left in place and
**where** a future cleanup should move it.

---

## emergent_infrastructure.py (235 LOC, 2 importers)

Purpose: "Agents propose new tools/capabilities, all requiring human
approval via Signal CLI before deployment. Meta Hyperagents (2026)
adapted".

**Why deferred:** This is agent tooling / governance infrastructure,
not a consciousness representation or runtime signal. It belongs
alongside `app/governance.py` or as its own top-level `app/emergent_infrastructure.py`.

**Target (future):** `app/emergent_infrastructure.py` (top-level) or
under `app/governance/` if that becomes a package.

---

## inspect_tools.py (490 LOC, 10 importers)

Purpose: "Six read-only self-inspection tools. Gives the system
grounded self-knowledge by inspecting its own code, configuration,
runtime state, memory backends, and self-model."

**Why deferred:** These are CrewAI *tools* consumed by agents, not
consciousness state. They belong next to the other tools in
`app/tools/`. Moving them is straightforward but independent of the
SubIA program.

**Target (future):** `app/tools/self_inspect.py` (or split across
several files under `app/tools/inspect/`).

---

## journal.py (188 LOC, 17 importers)

Purpose: "Unified chronological activity journal. Single timeline
of all system events: task completions, failures, evolution outcomes,
self-reflections, configuration changes, etc."

**Why deferred:** This is the system-wide audit journal. The
architectural audit flagged it as one of several overlapping audit
stores (`app/audit.py`, `app/auditor.py`, `app/atlas/audit_log.py`,
this). Consolidation is a larger refactor that belongs in a dedicated
cleanup — not in the Phase 1 layout move.

**Target (future):** Merge with `app/audit.py` into a single
unified audit backend. See architectural audit cluster #10.

---

## knowledge_ingestion.py (243 LOC, 7 importers)

Purpose: "AST-based code chunking into ChromaDB self_knowledge.
Ingests the system's own codebase into a searchable ChromaDB collection
so agents can answer detailed questions about their own implementation."

**Why deferred:** This is an ingestion pipeline specialized for
Python source code. It overlaps with `app/knowledge_base/ingestion.py`
(generic doc ingestion) and `app/philosophy/ingestion.py`. The
architectural audit's cluster #2 (ingestion pipelines) covers
consolidation of all three. That work is a dedicated refactor,
not a layout move.

**Target (future):** `app/knowledge_base/ast_ingestion.py` as a
pluggable chunker under a unified `DocumentIngestionPipeline`.

---

## prosocial_learning.py (298 LOC, 2 importers)

Purpose: "Prosocial Preference Learning via coordination games. Agents
develop ethical dispositions through repeated multi-agent coordination
games. Preferences emerge from interaction patterns, not static rules."

**Why deferred:** This is a personality / developmental-psychology
mechanism, not a consciousness indicator. It is personality-adjacent
(VIA-Youth, TMCQ in `app/personality/`) rather than a SubIA kernel
component. Whether it belongs under `app/personality/` or stays in
`self_awareness/` is a Phase 2 discussion — moving it now would
prejudge a design question.

**Target (future):** Decide between:
  (a) `app/personality/prosocial_learning.py` — if it is part of the
      personality development pipeline
  (b) keep in `app/self_awareness/` — if it is operating at the
      consciousness boundary but doesn't fit GWT/HOT/PP frames

---

## Summary

Running inventory after Phase 1:

| Location | Count after migration |
|---|---|
| `app/consciousness/` | 10 files (all shims + `config.py` which stays bounded-mutable) |
| `app/self_awareness/` | 17 files (12 shims + 5 deferred per this document) |
| `app/subia/` | 34 migrated modules across 10 subpackages |

The deferred files are tracked here so Phase 2+ work does not
inadvertently assume they should have been migrated.

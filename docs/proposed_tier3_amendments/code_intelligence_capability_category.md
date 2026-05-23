# Tier-3 amendment proposal — `code-intelligence` capability category

**Status**: **APPLIED** — operator-approved 2026-05-23
**Target file**: `app/tool_registry/capabilities.py` (TIER_IMMUTABLE)
**Verified Implementation Plan §5** (2026-05-22)
**Proposal author**: AndrusAI agent (machine-drafted; operator-approved + applied 2026-05-23)

---

## Why this needs the Tier-3 protocol

`app/tool_registry/capabilities.py` is on the TIER_IMMUTABLE list: the
capability vocabulary is the bounded namespace of "things tools do"
and every entry must be operator-blessed. The auto_deployer refuses
agent-originated CRs that touch it.

`code_intel` ships with 6 query tools (`code_intel_references`,
`code_intel_callers`, `code_intel_coverage`, `code_intel_deps`,
`code_intel_type_check`, `code_intel_test_for`) but they currently
register under generic tags. There is no canonical tag describing the
*kind* of capability: "symbol-level read-only retrieval over the live
codebase." That gap makes `tool_search` discovery noisy — a query for
"find usages of X" can't filter to the symbol-level tools.

This proposal adds the `code-intelligence` category.

---

## What changes

Adds a new top-level capability category `code-intelligence` to the
vocabulary, with 4 tags describing read-only retrieval over the
code_intel index (AST + tree-sitter + pyright):

```python
# Insertion point: between "ratelimit" and "code-development"
"code-intelligence": {
    "queries-code-symbols": (
        "Query the code_intel symbol index (definitions, "
        "references, callers) over the live codebase. Read-only."
    ),
    "checks-types": (
        "Type-check a coding-session worktree via the pyright "
        "sidecar. Returns structured ``TypeError`` records. "
        "Read-only; never modifies code."
    ),
    "finds-test-coverage": (
        "Find which tests cover a given file/symbol. Reads the "
        "coverage snapshot. Read-only."
    ),
    "finds-deps": (
        "Find which modules import / depend on a given symbol or "
        "module. Reads the dependency graph. Read-only."
    ),
},
```

**No existing capabilities are removed or renamed.** This is a pure
addition. The new tags are READ surfaces; mutation tags live in
`code-development`.

---

## Demonstrated value

The 6 `code_intel` tools shipped in Phases C.2, C.4, and C.5 already
deliver the runtime. What's missing is the discoverability layer:

| Live primitive | What it does | What's missing without the tag |
|---|---|---|
| `code_intel.indexer` | AST-walks `app/`, persists to JSONL | Discovery by capability tag |
| `code_intel.pyright_sidecar` | Type-checks coding-session worktrees | Same |
| `code_intel.coverage` | Reads `.coverage` files | Same |
| `code_intel.deps` | Builds import graph | Same |
| `code_intel.agent_tools` | 6 CrewAI tools wrapping the queries | Tools have to declare generic capabilities |

The tag category becomes the operator's mental model: "this tool
reads the symbol index; here are all such tools."

---

## Operator action

Applied 2026-05-23 by direct operator authorization (the operator's
message: "for gap 4 I allow changes in TIER_IMMUTABLE"). The edit
to `app/tool_registry/capabilities.py` is recorded in the same commit
as this status update.

---

## Why this isn't urgent

The runtime works WITHOUT the tag. An operator who never queries by
capability never notices the gap. The pyright sidecar runs, the
references tool returns results, the coverage check fires.

The tag becomes valuable when:

* A future agent wants to filter to "tools that read the codebase
  without modifying it" (vs the `code-development` set which writes).
* A future tool-registry audit wants to enumerate "every read-only
  code-introspection surface."
* The operator wants to know "which tools the coder agent uses for
  understanding before writing."

None of those are shipping immediately. This proposal exists so the
capability vocabulary is ready when they do.

---

## What gets pinned by tests

After APPLIED, the natural follow-up tests are:

1. `is_known("queries-code-symbols")` returns `True` (and 3 sibling
   tags).
2. `tool_search(capabilities=["checks-types"])` returns the type-check
   tool surface (after the follow-up tagging PR migrates the 6
   `code_intel_*` tools onto these tags).
3. `category_for("checks-types") == "code-intelligence"`.

These tests live in `tests/test_capabilities_vocabulary.py` (created
in the same commit as this proposal's APPLIED status).

---

## Rollback

The Tier-3 amendment protocol's standard rollback applies. Removing
the 4 tags has zero behavioral impact on the runtime — the code_intel
package doesn't consume capability tags. Tools currently tagged with
`reads-file` would simply lose the `code-intelligence` sub-category
discoverability.

---

## Cross-references

* `crewai-team/docs/PHASES_ABCDE_SUMMARY.md` — operator runbook
* `crewai-team/docs/TIER3_AMENDMENT.md` — protocol spec
* `app/tool_registry/capabilities.py` — current vocabulary (post-amendment)
* `app/code_intel/` — the runtime primitive these tags describe
* `app/code_intel/pyright_sidecar.py` — the type-check probe
* `docs/proposed_tier3_amendments/ratelimit_capability_category.md` — sibling amendment applied at the same time

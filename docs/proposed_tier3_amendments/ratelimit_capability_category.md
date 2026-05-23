# Tier-3 amendment proposal — `ratelimit` capability category

**Status**: **APPLIED** — operator-approved 2026-05-23
**Target file**: `app/tool_registry/capabilities.py` (TIER_IMMUTABLE)
**Autonomous Developer & Enterprise Agent Gap #6** (2026-05-22) — see `docs/AUTONOMOUS_DEVELOPER_AND_ENTERPRISE_AGENT.md`
**Proposal author**: AndrusAI agent (machine-drafted; operator-approved + applied 2026-05-23)

---

## Why this needs the Tier-3 protocol

`app/tool_registry/capabilities.py` is on the TIER_IMMUTABLE list:
the capability vocabulary is the bounded namespace of "things tools
do" and every entry must be operator-blessed. The auto_deployer
refuses agent-originated CRs that touch it.

The verified-plan items so far (Phases D.1, D.2, D.3) all explicitly
SKIPPED capability-tag additions to avoid this gate. C.1 migrated 12
tools using existing tags only. That kept the work moving but left a
discoverability gap: an agent doing `tool_search(capabilities=
["quota-limited-anthropic"])` returns nothing because the tag doesn't
exist.

This proposal closes that gap via the canonical Tier-3 amendment
flow.

---

## What changes

Adds a new top-level capability category `ratelimit` to the
vocabulary, with 6 tags that describe per-vendor quota constraints
the connector-budget primitive (`app/connector_budget/`) already
enforces at runtime:

```python
# Insertion point: after the "tickets" category, before "code-development"
"ratelimit": {
    "quota-limited-anthropic": (
        "Tool calls Anthropic Claude. Subject to "
        "``anthropic_daily_cap_usd`` vendor-level cap + per-tier "
        "circuit breakers. Cap-out → tool may return empty / refuse."
    ),
    "quota-limited-brave": (
        "Tool calls Brave Search. Subject to the documented free-tier "
        "monthly quota; the connector_budget primitive enforces a "
        "daily call cap on top."
    ),
    "quota-limited-google-workspace": (
        "Tool calls Google Workspace (Calendar / Gmail / Docs / "
        "Sheets / Slides / Drive). Subject to per-day OAuth quota."
    ),
    "quota-limited-openai": (
        "Tool calls OpenAI (GPT-4o / GPT-5 / embeddings). Subject to "
        "the project's OpenAI org-level rate limit."
    ),
    "quota-limited-osv": (
        "Tool calls OSV.dev /v1/querybatch. Subject to the "
        "``dependency_radar_osv`` connector budget (50 calls/UTC-day "
        "by default)."
    ),
    "quota-limited-github": (
        "Tool calls GitHub API (unauthenticated). Subject to the "
        "``dependency_radar_github`` connector budget (500 calls/UTC-"
        "day by default) plus GitHub's 60/hr unauthenticated ceiling."
    ),
},
```

**No existing capabilities are removed or renamed.** This is a pure
addition.

---

## Demonstrated value

Phases D.2 + D.3 already shipped the runtime enforcement; what's
missing is the discoverability layer:

| Live primitive | What it does | What's missing without the tag |
|---|---|---|
| `app/llm_anthropic_budget.pre_check` | Gates Anthropic calls at the cap (wired into 12 sites) | An agent can't filter `tool_search` to "tools that respect the cap" |
| `app/connector_budget/decorator` | Per-connector daily caps (USD or call count); 8 connectors wired | Discovery via capability tag is the standard way to find these |
| `/api/cp/anthropic-budget/state` | REST surface for the cap | No way to enumerate "which tools the cap affects" without a tag |
| `/budgets` Signal command | Operator-facing cap status | Same gap |

The tag category becomes the operator's mental model: "this tool
talks to X; X is rate-limited; here are the tools that share the
quota."

---

## Operator action

Two options:

### Option A — apply via Tier-3 amendment protocol

1. Operator reads this proposal.
2. Calls the canonical Tier-3 amendment producer:
   ```python
   from app.tools.request_tier3_amendment import RequestTier3AmendmentTool
   tool = RequestTier3AmendmentTool()
   tool._run(
       target_file="app/tool_registry/capabilities.py",
       proposal_body=open(
           "docs/proposed_tier3_amendments/ratelimit_capability_category.md"
       ).read(),
       diff_summary="Add 'ratelimit' capability category (6 tags).",
       proposer="operator:andrus",
   )
   ```
3. Standard Tier-3 lifecycle: PROPOSED → STAGED → COOLDOWN_OK → APPROVED → APPLIED → STABLE.
4. After APPLIED, the migrated tools in `app/tools/` add the appropriate `quota-limited-*` capability tag in their `@register_tool` decoration (separate non-Tier-3 PR).

### Option B — apply via React /cp/amendments

1. Open `/cp/amendments` in the dashboard.
2. Use the "Propose new" form with this file's body.
3. Same lifecycle.

---

## Why this isn't urgent

The runtime enforcement works WITHOUT the tag. An operator who never
queries by capability never notices the gap. The cost ceilings still
fire, the `/budgets` command still works, the React cards still
render.

The tag becomes valuable when:

* A future LLM-driven router wants to plan around vendor quotas
  ("avoid Anthropic this hour because we're at 95%")
* A future capability-regression detector wants to alert on
  "all `quota-limited-anthropic` tools became unusable simultaneously"
* An operator audit wants to enumerate "every tool that respects
  cost ceilings"

None of those are shipping in the verified-plan scope. This proposal
exists so the capability vocabulary is ready when they do.

---

## What gets pinned by tests

If the operator approves + applies this amendment, the natural
follow-up tests are:

1. `is_known("quota-limited-anthropic")` returns `True` (and 5
   sibling tags).
2. `tool_search(capabilities=["quota-limited-anthropic"])` returns
   the tools that should be tagged (after the follow-up tagging
   PR).
3. The existing `tests/test_capabilities_vocabulary.py` (if it
   exists) acknowledges the new category.

These tests live in the FOLLOW-UP PR, not this amendment itself.

---

## Rollback

The Tier-3 amendment protocol's standard rollback applies. Removing
the 6 tags has zero behavioral impact on the runtime — the
connector_budget primitive doesn't consume capability tags.

---

## Cross-references

* `crewai-team/docs/PHASES_ABCDE_SUMMARY.md` — operator runbook
* `crewai-team/docs/TIER3_AMENDMENT.md` — protocol spec
* `app/tool_registry/capabilities.py` — current vocabulary
* `app/connector_budget/` — the runtime primitive these tags describe
* `app/llm_anthropic_budget.py` — the Anthropic-specific cap

# Framework-migration playbook

**PROGRAM §63.10 — D#c.** Operator runbook for when a framework that
the upgrade-lifecycle subsystem deliberately treats as *non-bumpable
through the same system* (CrewAI, FastAPI, Pydantic, ChromaDB,
Starlette, Anthropic SDK, pip itself) needs to be replaced or
migrated to a successor.

This is **architectural-decision territory**, not subsystem code.
The upgrade-lifecycle ships a fixed `FRAMEWORK_PACKAGES` set
([`changelog_fetcher.py`](../app/upgrade_lifecycle/changelog_fetcher.py)
line ~62) so framework bumps **never** reach U4's MAJOR auto-CR
gate. Every framework transition routes through the annual ecosystem
snapshot + operator-explicit acceptance, then a hand-authored
migration plan. This document is the template.

## Why frameworks are different from libraries

A library can usually be bumped in isolation — `requirements.txt`
gets one line changed, the tests pass, the operator clicks Accept.
A framework typically:

- **Owns the runtime model.** CrewAI defines what an "Agent" is;
  swapping framework rewrites every agent. FastAPI defines what a
  "route" is; swapping rewrites every endpoint.
- **Has 1:N dependent code.** A typical framework bump touches
  10-100+ files. The trial harness's 10-minute pytest budget can't
  exercise a thousand call sites in one go.
- **Has uneven semver.** Frameworks routinely break minor versions
  (Pydantic 1→2 was the obvious one; FastAPI's `lifespan` migration
  is another). A `MAJOR auto-CR` would catastrophically misfire.
- **Often has no drop-in successor.** "Replace CrewAI with X" needs
  X to exist + be production-grade + have community gravity. That's
  an evaluation, not a CR.

So: the upgrade-lifecycle subsystem **detects + alerts**, but
migration is hand-authored.

## The protocol

### 0. The trigger

You'll usually learn about a needed framework migration from one of:

- **Annual ecosystem snapshot** (`/cp/ecosystem`, written every
  January) — framework-health section flags
  `last_release_age_days > 365` or a low GitHub commit cadence.
- **`vendor_sunset` healing monitor** — the framework's maintainer
  shipped a deprecation notice that the monitor caught.
- **External signal** — security advisory, license change, the
  framework getting acquired by a hostile party, etc.

When you see any of these, **don't try to bump the framework via
the standard CR flow**. The system will refuse on
`FRAMEWORK_PACKAGES`. Open this playbook instead.

### 1. Evaluate alternatives (week 1)

Make a wiki page at `wiki/architecture/migration_<framework>_<year>.md`
with:

- **Why are we considering migration?** One sentence. (e.g.
  "CrewAI shipped its last release 18 months ago; the GitHub repo
  has zero merged PRs in the last 60 days.")
- **What alternatives exist?** List 2–4 candidates. For each:
  release cadence, GitHub stars, community signal (Discord?
  conferences?), license, Python-version support, the closest
  conceptual match to what we're using.
- **What stays the same?** Make this exhaustive. Most of the
  agents' SOULs don't need to change. Most of the tool registry
  doesn't change. Most of the LLM factory doesn't change. List
  these explicitly so the migration scope stays tight.
- **What HAS to change?** File-by-file inventory. Use the existing
  `code_intel` indexer to find every import + usage:

      python -m app.code_intel query --imports-package crewai

  Save the output. This is the size of the work.

The output of this step is a markdown document the operator (you)
reads end-to-end before deciding anything.

### 2. Pick one (week 2)

The decision is yours. The system doesn't try to pick for you. But
two heuristics that have served well:

- **Pick the framework with the smaller blast radius**, not the
  shinier one. Migration cost is usually 10x the obvious estimate.
- **Pick the framework that lets you keep most of your tests.**
  If you have to rewrite the test suite, you'll be in the
  migration for months.

Write the decision down in the same wiki page. Include the runner-up
+ why you didn't pick it (so future-you doesn't relitigate).

### 3. Open a tracking thread (week 2)

Use the existing thread system:

    /thread start framework-migration: crewai → <successor>

Threads survive operator absence; you'll want this one to outlive
any single sprint.

Add sub-questions to the thread for each "what HAS to change" group
from step 1.

### 4. Coding-session per group (weeks 3–N)

For each "what HAS to change" group:

    /coding-session start --base main \
        --purpose "migrate agents/researcher.py to <successor>"

Inside the session:

1. Read the existing file (`coding_session_read`).
2. Write the new version (`coding_session_write`).
3. Run the tests for that file (`coding_session_run -- pytest path/to/test`).
4. Iterate until green.
5. Submit (`coding_session_submit`) — this fans out one CR per
   touched file through the standard operator-gated flow.

This is the same flow the autonomous executor uses for any
multi-file change. The migration is just a *very long* application
of it.

**Important**: do NOT add the successor framework to
`FRAMEWORK_PACKAGES` yet. While the migration is in flight you want
its CRs going through the standard path so you can iterate. Add it
to the exclusion list *after* migration completes (step 6).

### 5. Cut over (month N)

When every group is migrated AND the test suite is green:

1. Update `requirements.txt` to remove the old framework + add the
   new one. (This is one of the few times the operator hand-edits
   `requirements.txt` rather than going through `requirements_writer`.)
2. Update `app/upgrade_lifecycle/changelog_fetcher.py` —
   `FRAMEWORK_PACKAGES` removes the old, adds the new.
3. Update `app/upgrade_lifecycle/ecosystem_snapshot.py` —
   `compose_framework_health_section` updates the curated list.
4. Update `CLAUDE.md`'s quick-reference + the `Codebase Conventions`
   section.
5. Open a single, large CR documenting the migration in
   `docs/`. Reference the wiki page from step 1. This becomes
   the durable history of the decision.

### 6. Identity-continuity event

After cutover, emit a one-time continuity-ledger event:

```python
from app.identity.continuity_ledger import record_event
record_event(
    kind="ecosystem_snapshot",
    actor="operator",
    summary=f"Framework migration: <old> → <new> complete",
    detail={
        "subkind": "framework_migration",
        "from_framework": "<old>",
        "to_framework": "<new>",
        "wiki_page": "wiki/architecture/migration_<old>_<year>.md",
        "duration_weeks": <weeks>,
        "files_touched": <count>,
    },
)
```

This is what the annual reflection ([`identity/annual_reflection.py`](../app/identity/annual_reflection.py))
reads to surface "this is the year we migrated off CrewAI" in the
year-end essay. Don't skip it — operator-decade narratives need the
breadcrumbs.

### 7. Re-arm the system

The new framework is now an `is_framework=True` row in the
ecosystem snapshot. Future MAJOR bumps of the new framework will
again be operator-accept-only via `/cp/ecosystem`, never via U4
auto-CR. You're back to the steady state, just with a different
framework underneath.

## Anti-patterns to avoid

- **Don't try to bump a framework via U4.** The framework
  exclusion set exists because the auto-CR gate genuinely can't
  reason about multi-file framework breaks. Adding a framework to
  the exclusion set requires no CR — it's a one-line edit at step
  5.3.
- **Don't run the migration as one giant CR.** The CR system caps
  total content size + the operator gate becomes useless on a
  10kLOC diff. Group-by-group submission is the path.
- **Don't migrate during operator-absence.** The
  `absence_policy` deliberately excludes framework changes from
  the auto-apply lane. A returning operator should never find a
  framework swap they didn't approve.
- **Don't delete the wiki page after cutover.** Future migrations
  benefit from prior migrations' write-ups. Keep them all.

## What the system DOES help with during migration

- **`code_intel` queries** for finding all usage sites.
- **Coding sessions** for iterating on each file.
- **The standard CR flow** for each per-file submission.
- **The change-request audit log** as durable history.
- **The thread system** for tracking blockers across weeks.

What the system DOES NOT do:

- Decide which framework to pick.
- Translate code from old framework to new framework — that's the
  coding-agent + your judgement.
- Skip the operator gate on any individual migration CR.
- Auto-update `FRAMEWORK_PACKAGES` — operator-only.

## Cross-references

- [`UPGRADE_LIFECYCLE.md`](UPGRADE_LIFECYCLE.md) — operator runbook for the
  normal (non-framework) upgrade path (to be written).
- [`app/upgrade_lifecycle/changelog_fetcher.py`](../app/upgrade_lifecycle/changelog_fetcher.py) — `FRAMEWORK_PACKAGES`
  exclusion set.
- [`app/upgrade_lifecycle/ecosystem_snapshot.py`](../app/upgrade_lifecycle/ecosystem_snapshot.py) — annual snapshot's
  framework-health section.
- `wiki/architecture/migration_*.md` — historical migrations
  (populated as you do them).

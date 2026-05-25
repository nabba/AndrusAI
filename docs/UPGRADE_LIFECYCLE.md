# Upgrade lifecycle — operator runbook

PROGRAM §63 + §63.7–§63.11. Decade-scale self-upgrading. This is the
operator-facing companion to the PROGRAM.md entries; if you only
read one document about the upgrade-lifecycle subsystem, read this
one.

The framework-migration counterpart is in
[`UPGRADE_LIFECYCLE_FRAMEWORK_MIGRATION.md`](UPGRADE_LIFECYCLE_FRAMEWORK_MIGRATION.md).
That one covers the case the subsystem deliberately won't auto-handle
(replacing CrewAI / FastAPI / Pydantic / ChromaDB / Starlette / Anthropic
SDK / pip itself). This document covers everything else.

---

## 1. What it actually does

```
                       ┌──────────────────┐
                       │  dependency_radar│  (Q13.2 — finds outdated)
                       │   weekly idle    │
                       └────────┬─────────┘
                                │ MAJOR finding
                  ┌─────────────▼─────────────┐
                  │      orchestrator         │  (P0#1b)
                  │  U1 → U2 → U3-lookup → U4 │
                  └─────────────┬─────────────┘
                                │ gate passes
                ┌───────────────▼───────────────┐
                │  proposal_bridge.stage(...)   │
                │  target_path=docs/proposed_  │
                │       upgrades/<sig>.md       │
                └───────────────┬───────────────┘
                                │
                ┌───────────────▼───────────────┐
                │ change_requests.create_request │ ← validator passes
                │            ↓                   │   (docs/ is allowed)
                │  /cp/changes ← operator approves│
                │            ↓                   │
                │     change_requests.apply      │
                │  writes docs/proposed_upgrades │
                │         /<sig>.md to disk      │
                └───────────────┬───────────────┘
                                │
                ┌───────────────▼───────────────┐
                │  apply_hook daemon (10-min)   │  (P0#1b)
                │  parses YAML front-matter:    │
                │  action: bump_requirement |   │
                │          bump_python          │
                └───────────────┬───────────────┘
                                │ dispatch
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ requirements_    │  │ pyproject_writer │  │ dockerfile_      │
│ writer (pip)     │  │ (uv/poetry/pdm)  │  │ writer (Python)  │
│ requirements.txt │  │ pyproject.toml   │  │ Dockerfile       │
└──────────────────┘  └──────────────────┘  └──────────────────┘
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                │ writes
                                ▼
                          auto_deployer
                       detects file change
                       → container rebuild
```

The full pipeline takes minutes for a small bump, days for a large
one (mostly cooldowns). **Nothing happens to your code automatically
unless you opted in to specific writer switches.** The defaults are
detect + propose + alert — execution requires explicit consent.

**Annual snapshot surfacing**: when the January idle tick generates a
new `wiki/self/ecosystem/<year>.md`, a Signal + Web Push ping fires
with iPhone + Mac dashboard links to `/cp/ecosystem` so the operator
can ratify the year's major-upgrade plan without manually checking
the page. Notify is failure-isolated — a broken push never blocks the
idle job.

---

## 2. Operator activation order

Switches default OFF, except observational ones. The recommended
opt-in sequence:

### Phase 0: observe (default; no action needed)

Detection + planning are always on. You'll see:

* Weekly Signal alerts from `dependency_radar` about outdated /
  CVE-affected packages
* Annual `ecosystem_snapshot` written to
  `wiki/self/ecosystem/<year>.md` every January
* React `/cp/ecosystem` page browsable
* Capabilities being extracted into
  `workspace/upgrade_lifecycle/capabilities/<pkg>.jsonl`

Nothing applies; everything is paper-trail.

### Phase 1: enable capability extraction LLM spend

`/cp/settings` → check that `upgrade_lifecycle_capability_extraction_enabled`
is ON (default). It needs `Anthropic` API key configured.

Budget: monthly $5 default. Capped via
`upgrade_lifecycle_extraction_budget_usd_monthly`. Each PyPI/GitHub
changelog extraction is ~$0.10 (Anthropic Haiku-class).

**Three content sources** (composed, not exclusive):

1. **PyPI metadata** — `info.description` field.
2. **GitHub releases** — release-body text between the from/to tags.
3. **Project CHANGELOG URL** — `info.project_urls.{Changelog, Changes,
   Release Notes, History}` (PEP 621). HTML pages get stripped to
   text with heading markers preserved; sliced to the version range
   bracketed by `from_version` (exclusive) and `to_version`
   (inclusive). Daily budget `upgrade_lifecycle_changelog` (200
   calls). When all three are present the LLM sees them stacked
   (changelog first, then GitHub releases), which usually produces
   the richest extraction.

**Phenomenal-language discipline** — every text fragment the LLM
emits (one per list entry plus `license_change` / `notes`) is run
through `PhenomenalLanguageLinter`. On HARD_FAIL the call is retried
once with a strengthened system prompt; if the retry still fails,
ONLY the offending fields are blanked (clean siblings preserved) and
a row lands in `workspace/threads/linter_rejections.jsonl` with
`thread_id="capability:<pkg>:<ver>"`. Cost ceiling: 2× normal worst
case.

### Phase 2: enable the auto-CR gate

`/cp/settings` → `upgrade_lifecycle_major_auto_cr_enabled` (default
ON). This makes the gate consider MAJOR auto-CRs but **still doesn't
write to disk** — it just stages CRs at `docs/proposed_upgrades/`
under standard operator gate.

### Phase 3: enable trial harness

`/cp/settings` → `upgrade_lifecycle_trial_enabled` (default ON).
Spins venv-isolated pytest runs for queued upgrades. Hourly cadence,
1 trial per tick. Trials inform the U4 gate but don't apply changes.

**Smoke runners** — pytest exercises code paths the test suite knows
about; real production data formats often aren't in scope. The trial
harness exposes a per-package `smoke_runners` hook (registered via
`app.upgrade_lifecycle.smokes.register(package, runner)`) that fires
AFTER pytest, against the BUMPED library installed in the trial venv.
Reference implementation at `app/upgrade_lifecycle/smokes/chromadb.py`:
copies the latest `.sqlite_snapshots/*.db` to a scratch dir, opens it
with the bumped chromadb in a subprocess, reports collection count.

A smoke failure is an **append-only signal** — it lands in
`TrialResult.smoke_results` as a row with `status="fail"` but does
NOT downgrade the trial's overall status. U4's auto-CR gate and
operator review treat smoke output as advisory.

### Phase 4: enable the writers (apply path)

This is where the system starts actually modifying files. Recommended
order:

1. `upgrade_lifecycle_apply_hook_enabled` (the daemon that watches
   for approved decision CRs and dispatches to writers)
2. `upgrade_lifecycle_requirements_writer_enabled` (pip → requirements.txt)

After flipping both, an approved CR at `docs/proposed_upgrades/<sig>.md`
with YAML front-matter `action: bump_requirement` triggers an
actual `requirements.txt` edit. The standard auto-deployer picks it
up + rebuilds the container.

Verify by approving one CR and watching `requirements.txt` change.

### Phase 5: enable manager-specific writers (when applicable)

* `upgrade_lifecycle_pyproject_writer_enabled` if your project uses
  uv / poetry / pdm (the detection helper picks the right one)
* `upgrade_lifecycle_dockerfile_writer_enabled` if you're ready for
  Python bumps to actually mutate the Dockerfile (and you commit to
  re-pinning the SHA after every bump — see §6)

### Phase 6: enable capability adoption (Stage E)

`/cp/settings` → `upgrade_lifecycle_capability_adoption_enabled`
(default ON). Files refactor CRs against `app/` Python files when
an upgrade introduces an idiom that applies to your code. **One CR
per ISO week, hard-capped.** Quarterly USD budget defaults $20
(`upgrade_lifecycle_capability_budget_usd_quarterly`).

These CRs go through the standard `/cp/changes` review path; you
approve or reject. The LLM generates real diffs (full-file replacement
content); your job is to read the diff before approving.

### Phase 7: enable absence policy (pre-arming for unattended operation)

This is the only flag that **widens auto-apply beyond what the
operator gate would allow**. Flip it ON only if you anticipate
periods of silence longer than 90 days.

`/cp/settings` → `AbsencePolicyCard` → check the box.

Once on, the system will auto-apply PATCH-level CRs (only) from
trusted requestors after a 14-day soak, but **only** when
`operator_transition` reports ABSENT_90D or TRANSITIONED. It refuses
during ACTIVE/ABSENT_30D/READ_MOSTLY (you're around).

---

## 3. Master switch reference

All switches live in `runtime_settings.py`. Toggle from
`/cp/settings` or `POST /api/cp/settings`.

### Default-ON (observational)

| Switch | What it gates |
|---|---|
| `upgrade_lifecycle_enabled` | Top-level kill switch. Everything below is no-op when OFF. |
| `upgrade_lifecycle_capability_extraction_enabled` | U1 — LLM-parses changelogs into structured rows |
| `upgrade_lifecycle_trial_enabled` | U3 — venv-isolated pytest trial runs |
| `upgrade_lifecycle_major_auto_cr_enabled` | U4 — five-condition gate; stages CRs |
| `upgrade_lifecycle_capability_adoption_enabled` | U5 — weekly refactor proposals |
| `ecosystem_snapshot_enabled` | U6 — annual January snapshot |
| `python_eol_proximity_monitor_enabled` | Threshold alerts at 12/6/3/1mo before Python EOL |
| `upgrade_lifecycle_health_monitor_enabled` | Backlog stale / repeated trial failure / budget burn / snapshot-unread |
| `dockerfile_pin_staleness_monitor_enabled` | 40th monitor — unpinned Dockerfile + TODO marker → alert |
| `cr_apply_consistency_monitor_enabled` | 41st monitor — docs CRs marked APPLIED but file missing → alert |

### Default-OFF (apply path — operator opt-in)

| Switch | What it gates |
|---|---|
| `upgrade_lifecycle_apply_hook_enabled` | Daemon polling for approved decision CRs |
| `upgrade_lifecycle_requirements_writer_enabled` | requirements.txt single-line writes |
| `upgrade_lifecycle_pyproject_writer_enabled` | pyproject.toml (uv/poetry/pdm) writes |
| `upgrade_lifecycle_dockerfile_writer_enabled` | Dockerfile FROM python: writes (drops SHA pin) |
| `upgrade_lifecycle_absence_policy_enabled` | Widens auto-apply during ABSENT_90D+ |

### Numeric

| Setting | Default | Cap |
|---|---|---|
| `upgrade_lifecycle_capability_budget_usd_quarterly` | $20 | $500 |
| `upgrade_lifecycle_extraction_budget_usd_monthly` | $5 | $100 |

---

## 4. CR flow — step by step

A clean MAJOR bump (e.g. `click 8.0 → 9.0`) flows like this:

1. **Detection** (weekly): `dependency_radar` finds `click` outdated
   in `pip list --outdated`.
2. **Orchestration** (immediate): orchestrator runs U1 (capability
   extraction via Haiku), U2 (AST walk for impact analysis),
   U3-lookup (queries the trial scheduler's cached results).
3. **Trial scheduling** (1/hour): if no cached trial exists for
   `click@9.0`, the orchestrator queues one. The trial_scheduler
   daemon spins a venv, installs requirements.txt with the bumped
   pin, runs the full pytest suite. Result persists at
   `workspace/upgrade_lifecycle/trials/click__9_0_0.json`.
4. **Gate evaluation** (next radar tick): U4 checks five conditions:
   trial==ok, ≥30d post-PyPI-upload, breaking_hits==0,
   !tier_immutable_touched, !framework_package. All pass for click.
5. **CR staging**: proposal_bridge stages at
   `docs/proposed_upgrades/upgrade_click_9_0_0.md`. Body has YAML
   front-matter with `action: bump_requirement` + capability summary
   + impact report + trial result.
6. **CR promotion** (next promoter tick, ≤24h): proposal_bridge
   promotes STAGED → CR_FILED via `change_requests.create_request`.
   Now visible at `/cp/changes`.
7. **Operator review**: you read the body at `/cp/changes`. The
   markdown shows capability summary, breaking-change call sites
   (should be empty), trial test pass count, days since release.
   Click **Approve** if happy.
8. **Apply**: `change_requests.apply_change` writes the markdown
   body to `docs/proposed_upgrades/upgrade_click_9_0_0.md`. Marks
   CR as APPLIED. Auto-PR opens.
9. **Apply hook fires** (next 10-min tick): apply_hook polls audit,
   sees the newly-APPLIED docs CR. Parses front-matter. Dispatches
   to requirements_writer.
10. **Writer mutation**: requirements_writer reads
    `requirements.txt`, finds the `click==` line, replaces with
    `click==9.0.0`. Atomic write. Emits continuity-ledger event.
11. **Container rebuild**: auto_deployer detects requirements.txt
    change. Triggers rebuild. Gateway restarts with the new version.

End-to-end timing: 10 minutes (apply_hook poll) + container rebuild
(~5 minutes). The trial may have taken weeks earlier (it runs at
1/hour and the queue may be deep), but that's offline work.

For PATCH/MINOR bumps the flow is identical except U4 isn't
involved — `dependency_radar` proposes directly via proposal_bridge.

For Python bumps the flow is identical except step 9 dispatches to
`dockerfile_writer` instead of `requirements_writer`. The writer
drops the SHA-pin and inserts a `# TODO P0#4: re-pin` comment.
Monitor `dockerfile_pin_staleness` fires weekly until you re-pin.

---

## 5. Operator surfaces

### React pages

* **`/cp/settings`** — three relevant cards: `UpgradeLifecycleCard`
  (stage switches + budget), `AbsencePolicyCard` (absence opt-in),
  `SourceLedgerCard` (storage layer health, used at deploy time).
* **`/cp/ecosystem`** — annual snapshot browser. Year sidebar +
  per-row Accept buttons. Framework rows trigger a confirmation
  modal explaining the playbook is required (B2-P2).
* **`/cp/changes`** — standard CR review where decision artifacts
  land. `docs/proposed_upgrades/` CRs show up here.

### Signal slash commands

```
/upgrade                       Status: switches, budget, CRs this week
/upgrade budget                Quarterly budget + remaining
/upgrade capabilities <pkg>    Last 5 Capability rows for a package
/upgrade trial <pkg> <fv> <tv> Queue a U3 trial (≤1hr until pickup)
/upgrade snapshot [year]       Annual snapshot summary
```

### REST endpoints (under `/api/cp`)

```
GET    /upgrade-lifecycle/state
GET    /upgrade-lifecycle/capabilities/<pkg>
POST   /upgrade-lifecycle/capability-adoption/run-pass
GET    /ecosystem/snapshots
GET    /ecosystem/snapshots/<year>
POST   /ecosystem/snapshots/generate         (operator-initiated)
POST   /ecosystem/major-upgrades/accept
```

### Workspace files

```
workspace/upgrade_lifecycle/
├── capabilities/<pkg>.jsonl                  # hash-chained U1 rows
├── trials/<pkg>__<ver>.json                  # per-trial result
├── trials/_pending.jsonl                     # trial scheduler queue
├── ecosystem/<year>.json                     # snapshot data
├── extraction_budget_ledger.jsonl            # U1 LLM spend
├── adoption/budget_ledger.jsonl              # U5 LLM spend
├── adoption/rate_limit_state.json            # 1-CR/wk counter
├── major_auto_cr_throttle.json               # U9 window state
├── capability_adoption_pause.json            # U9 pause state
├── apply_hook_state.json                     # processed CR ids
├── github_repo_cache.json                    # PyPI fallback cache
├── absence_policy_state.json                 # promotion history
└── retention_state.json                      # last cleanup pass

wiki/self/ecosystem/<year>.md                 # rendered snapshot
docs/proposed_upgrades/<sig>.md               # operator paper trail
```

---

## 6. Troubleshooting

### "My MAJOR auto-CR gate never fires"

Likely cause: one of the five gate conditions failed. Check the
`GateOutcome.reason` returned by `evaluate_gate()`. Common reasons:

* `trial_not_run` — no cached trial result. `/upgrade trial <pkg>
  <fv> <tv>` to queue. Wait ≤1 hour, re-check.
* `post_release_too_short:<n>d` — PyPI's `upload_time` is < 30 days
  ago. Wait. The window may have widened to 60d if U9 detected high
  rejection rate (`major_auto_cr_throttle.json`).
* `breaking_hits:<n>` — U2 found call sites matching the
  capability's `breaking_changes` list. Read the impact report; hand-
  refactor the call sites; rerun.
* `tier_immutable_touched` — call site is in TIER_IMMUTABLE files.
  Refused unconditionally. Requires Tier-3 amendment for that path.
* `framework_exclusion:<pkg>` — package is in `FRAMEWORK_PACKAGES`.
  Use the annual snapshot + framework migration playbook.

### "I approved a CR but requirements.txt didn't change"

Check the apply_hook daemon:

```
/upgrade                            # shows daemon state implicitly
```

Or via REST:
```
GET /api/cp/upgrade-lifecycle/state
```

Likely cause:
* `upgrade_lifecycle_apply_hook_enabled` is OFF — flip it on
* `upgrade_lifecycle_requirements_writer_enabled` is OFF — flip
* The CR's path is not under `docs/proposed_upgrades/` (apply_hook
  filters this) — re-file from /cp/ecosystem
* The 41st monitor `cr_apply_consistency` should have alerted; check
  Signal history. If it didn't, that's itself a bug.

### "Python bump applied but image still pulls old Python"

The Dockerfile's `@sha256:<digest>` pin is dropped on bump and
replaced with a `# TODO P0#4: re-pin` comment. Until you re-pin,
the tag (`python:3.14-slim`) is what Docker resolves at build time.
The 40th monitor `dockerfile_pin_staleness` reminds you weekly.

To re-pin:
1. `docker pull python:3.14-slim`
2. `docker inspect --format='{{.RepoDigests}}' python:3.14-slim`
3. Edit `Dockerfile`: replace `# TODO P0#4: re-pin` + the FROM line
   with `FROM python:3.14-slim@sha256:<captured-digest>`
4. Commit. The pin-staleness monitor clears.

### "Annual snapshot never generated"

The idle job `upgrade-ecosystem-snapshot` only fires Jan 1–7 each
year. Force-generate from the React empty state ("Generate snapshot
now" button) or via REST:

```
POST /api/cp/ecosystem/snapshots/generate
Body: {"year": 2027, "force": false}
```

When the job DOES fire successfully you'll see a Signal + Web Push
notification titled `📅 Ecosystem snapshot <year>` with iPhone + Mac
dashboard links straight to `/cp/ecosystem`. If the snapshot landed
on disk (`wiki/self/ecosystem/<year>.md` exists) but no notify fired,
check `notify` arbiter state and Web Push subscription health —
notify is failure-isolated so the snapshot itself is unaffected.

### "Capability extraction stopped"

Check the monthly budget:
```
cat workspace/upgrade_lifecycle/extraction_budget_ledger.jsonl | \
  jq -s 'group_by(.month) | .[-1] | {month: .[0].month, spend: (map(.cost_usd) | add)}'
```

If spend ≥ budget, capability extraction returns None until the
calendar month rolls over. Either bump
`upgrade_lifecycle_extraction_budget_usd_monthly` or wait.

If spend < budget, check the U1 master switch.

### "Capability adoption keeps proposing things I reject"

After ≥6 rejected U5 CRs within 90 days, U9 Goodhart pauses the
adoption pass for 30 days. State at
`workspace/upgrade_lifecycle/capability_adoption_pause.json`.

To clear early (after fixing whatever was wrong with the proposals):
```
rm workspace/upgrade_lifecycle/capability_adoption_pause.json
```

### "Trial scheduler not processing the queue"

Check `_pending.jsonl`:
```
wc -l workspace/upgrade_lifecycle/trials/_pending.jsonl
```

Check the daemon is alive:
```
ps -ef | grep ul-trial-scheduler   # in container
```

The watchdog respawns dead daemons within 60s. If it's been dead
for >5 minutes, that's a bug — file a healing-monitor alert.

### "PyPI is down — does the system stop?"

No. A5-P1 added a GitHub fallback. The first time a package's PyPI
metadata is fetched successfully, the GitHub repo URL is cached at
`workspace/upgrade_lifecycle/github_repo_cache.json`. Subsequent
extractions can survive PyPI outage by querying GitHub releases
for the `upload_time` (re-labelled from `published_at`). Brand-new
packages still need PyPI for first discovery.

### "I'm going silent for ≥ 90 days. What do I do?"

Three preparation steps:

1. **Arm the absence policy**: `/cp/settings` →
   `AbsencePolicyCard` → check the box. This widens auto-apply for
   PATCH-level CRs from trusted requestors after 14d soak.
2. **Declare a successor (optional)**: write
   `workspace/operator_transition/successor.json` per
   `SuccessorDeclaration` schema. A successor with gateway-secret
   + Signal access can flip switches on your behalf.
3. **Bump the monthly extraction budget if you want continued
   capability discovery** — defaults are conservative.

When you return, check:
* React `/cp/changes` for absence-auto-applied CRs (each had a
  Signal alert)
* `wiki/self/ecosystem/<year>.md` for the year's snapshot
* `workspace/upgrade_lifecycle/absence_policy_state.json` for the
  promotion history

---

## 7. Master design decisions (operator-fixed)

These are documented here because they shape what the system can
and cannot do; they were not implementation choices.

* **`FRAMEWORK_PACKAGES` is operator-fixed** at
  `{crewai, chromadb, fastapi, pydantic, pydantic-settings, starlette, anthropic}`.
  Adding/removing requires a code edit + Tier-3 amendment review.
* **Operator's acceptance IS the gate** for annual snapshot rows.
  Acceptance routes to standard CR (non-framework) OR Tier-3
  amendment (framework). No second approval after Accept click.
* **The system never auto-removes a dependency.** It only bumps
  versions of existing pins or appends new ones (when a transitive
  required pkg appears).
* **Framework bumps require the framework_migration playbook.** The
  ecosystem snapshot Accept button for a framework row files a
  paper trail only; actual migration is hand-authored. See
  [`UPGRADE_LIFECYCLE_FRAMEWORK_MIGRATION.md`](UPGRADE_LIFECYCLE_FRAMEWORK_MIGRATION.md).
* **All LLM model decisions route through `app.llm_factory`.** No
  model IDs hardcoded in any upgrade-lifecycle module. Vendor
  rotation is centrally managed.
* **Lock files are NOT touched by writers.** pyproject_writer edits
  `pyproject.toml`; the operator (or CI) runs
  `uv lock --upgrade-package <pkg>` / `poetry update <pkg>` /
  `pdm update <pkg>` before next deploy. The Signal alert reminds.

---

## 8. Healing monitors that surface upgrade-lifecycle issues

| Monitor | Triggers | Cadence |
|---|---|---|
| `upgrade_lifecycle_health` | Backlog stale > 30d / trial fail ≥5× / budget burn > 80% in first half quarter / snapshot unread > 90d | Daily probe, weekly internal |
| `python_eol_proximity` | EOL ≤ 12/6/3/1 month thresholds | Daily probe, quarterly internal |
| `dockerfile_pin_staleness` | Dockerfile has `# TODO P0#4: re-pin` + unpinned FROM line | Daily probe, weekly internal |
| `cr_apply_consistency` | docs CRs marked APPLIED but file missing on disk | Daily probe, weekly internal |

All four are observational + alert-only. None block any pipeline.

---

## 9. Continuity-ledger emission

The subsystem emits one identity-continuity event kind
(`ecosystem_snapshot`) with multiple subkinds, all written to the
hash-chained `workspace/audit.log` (via `app/identity/continuity_ledger.py`):

* `subkind=acceptance` — operator accepted a snapshot row
* `subkind=requirements_bump` — requirements_writer mutated the file
* `subkind=python_version_bump` — dockerfile_writer mutated FROM
* `subkind=pyproject_bump` — pyproject_writer mutated TOML
* `subkind=absence_auto_apply` — absence_policy promoted a CR
* `subkind=regenerated` — force-regenerated snapshot

All six surface automatically in `annual_reflection.summarise_drift`
so the year-end essay narrates what landed.

---

## 10. Tests

Test files at `tests/upgrade_lifecycle/`:

* `test_changelog_fetcher.py` — U1 + budget + PyPI fallback
* `test_impact_analysis.py` — U2 AST walk
* `test_trial_runner.py` — U3 venv isolation
* `test_major_auto_cr.py` — U4 gate conditions
* `test_orchestrator.py` — U4 wiring
* `test_capability_adoption.py` — U5 gates + LLM contract
* `test_ecosystem_snapshot.py` — U6 + framework Accept side effects
* `test_routes.py` — REST endpoints
* `test_monitors.py` — U8 monitors
* `test_goodhart.py` — U9 throttles
* `test_idle_jobs.py` — F6 scheduler wiring
* `test_trial_scheduler.py` — F2 daemon + thread-liveness
* `test_signal_command.py` — F4 `/upgrade` subcommands
* `test_requirements_writer.py` — P0#1a safety envelope
* `test_dockerfile_writer.py` — P0#4 + multi-stage
* `test_pyproject_writer.py` — D#a per-manager sections
* `test_apply_hook.py` — P0#1b dispatcher
* `test_absence_policy.py` — P1#a + phase semantics + license filter
* `test_retention.py` — P1#d cleanup ops
* `test_cve_sources.py` — P2#a fallback chain
* `test_package_manager.py` — P2#b detection
* `test_e2e_pipeline.py` — P2#d composition test
* `test_dockerfile_pin_staleness.py` — A3-P1 monitor
* `test_cr_apply_consistency.py` — B3-P2 monitor
* `test_change_requests_docs_path_smoke.py` — B3-P2 smoke test

279 tests + 18 environment-skipped (route + monitor tests need
`fastapi` / `pydantic_settings`).

---

## 11. Cross-references

* `PROGRAM.md §63` — Subsystem ship
* `PROGRAM.md §63.7` — P0 closure (validator routing, U5 diffs, trial isolation, Python upgrade)
* `PROGRAM.md §63.8` — P1 hardening (absence policy, watchdog, monthly budget, retention)
* `PROGRAM.md §63.9` — P2 (CVE fallback, package-manager abstraction, license tracking, E2E)
* `PROGRAM.md §63.10` — Deferred-list closure (pyproject_writer, multi-stage Dockerfile, framework playbook)
* `PROGRAM.md §63.11` — P0+P1 audit closures + P2 onboarding/UX/consistency
* [`UPGRADE_LIFECYCLE_FRAMEWORK_MIGRATION.md`](UPGRADE_LIFECYCLE_FRAMEWORK_MIGRATION.md) — Framework-migration playbook
* `app/upgrade_lifecycle/` — 18 modules, ~5,800 LOC
* `app/healing/monitors/{upgrade_lifecycle_health,python_eol_proximity,dockerfile_pin_staleness,cr_apply_consistency}.py`
* `app/control_plane/upgrade_lifecycle_api.py` — REST router
* `dashboard-react/src/components/{UpgradeLifecycleCard,AbsencePolicyCard,EcosystemPage}.tsx`

---

## 12. Quick reference: "what should I check first?"

* **System silently doing nothing for upgrades**: check
  `upgrade_lifecycle_enabled` (top-level kill switch)
* **CRs not being filed**: check `dependency_radar` is running +
  `proposal_bridge` daemon alive
* **CRs filed but not applying**: check apply_hook switches (apply
  hook daemon + appropriate writer)
* **Code being modified without your approval**: check absence
  policy is OFF (default) unless you turned it on
* **Budget eaten quickly**: check
  `upgrade_lifecycle_capability_adoption_enabled` — U5 is the
  biggest LLM consumer
* **Container won't rebuild after CR applied**: that's auto_deployer
  + Docker territory, not upgrade-lifecycle
* **"My specific package never gets upgraded"**: check
  `FRAMEWORK_PACKAGES` — if your package is in there, only the
  annual snapshot route works

---

## 13. Living document

This runbook is meant to be edited as the subsystem evolves. New
master switches, new monitors, new failure modes — all belong
here. The framework_migration playbook is a separate document
because its workflow is different in kind (multi-week project, not
one-click operation).

Last updated: 2026-05-23 (initial ship).

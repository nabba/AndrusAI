# Multi-year resilience gap closure — operator runbook

Same-day extension of `PROGRAM §70` (2026-05-24). Twelve gaps identified by
the ultrathink audit and shipped under one umbrella. This is the
operator-facing companion to the CLAUDE.md bullet; if you only read one
document about the 12-gap closure, read this one.

The original audit framing is preserved verbatim above the
"Multi-year resilience gap closure" bullet in CLAUDE.md. This doc covers
**operator activation order** + **runtime knobs** + **expected behavior**.

---

## 1. The twelve gaps

| # | Gap | Primary module | Default |
|---|---|---|---|
| 1 | External dead-man-switch | `scripts/external_deadman.py` + `scripts/install_external_deadman.sh` | OFF (no creds wired) |
| 2 | Total monthly cost ceiling | `app/healing/monitors/total_cost_ceiling.py` | ON, $200/mo cap |
| 3 | Configuration coherence | `app/healing/monitors/config_coherence.py` | ON |
| 4 | Settings genealogy | `app/settings_genealogy.py` | ON (no switch — always records POSTs) |
| 5 | Capability inventory | `app/capability_inventory/` | ON |
| 6 | Discovery → adoption funnel | `app/observability/discovery_funnel.py` + briefing candidate | ON |
| 7 | Unified privacy audit | `app/privacy/aggregator.py` + `/api/cp/privacy/*` | ON |
| 8 | CLAUDE.md compaction | One-shot manual archive | done 2026-05-24 |
| 9 | Adversarial drill (`prompt_injection_resistance`) | `app/resilience_drills/drills/prompt_injection_resistance.py` | ON (LOW risk, quarterly) |
| 10 | Knowledge currency audit | `app/healing/monitors/knowledge_currency.py` | ON |
| 11 | Hardware health proxy | `app/healing/monitors/hardware_health.py` + `scripts/host_smart_collector.py` | host LaunchAgent ON |
| 12 | SMS + email last-resort | `app/notify/last_resort.py` | ON (no-op without Twilio/SMTP creds) |

---

## 2. Activation order for the operator

Most gaps activate automatically on the next gateway restart. Three need
operator-side setup:

### 2.1 Twilio + SMTP credentials (Gaps #1, #12)

Both the in-band `last_resort.py` and the off-host `external_deadman.py`
read the same env vars. Put them in the gateway `.env` for the in-band
path, and in `~/.config/andrusai_deadman.env` for the external script.

```bash
# Twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
OPERATOR_PHONE_NUMBER=+358...

# SMTP
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USER=alerts@example.com
SMTP_PASSWORD=...
SMTP_FROM=alerts@example.com
OPERATOR_EMAIL=andrus@example.com
```

Either Twilio OR SMTP is sufficient — both is belt-and-suspenders.

#### Install the external dead-man-switch on a SECOND machine

The point of #1 is to escape the gateway's blast radius — run it on a
separate host (second laptop, cloud cron, phone via Pythonista/Termux).
Stdlib-only.

```bash
# Print the env template, fill it in:
bash scripts/install_external_deadman.sh env-template > ~/.config/andrusai_deadman.env
# (edit the file)

# Install the cron entry (every 6h):
bash scripts/install_external_deadman.sh install

# One immediate probe:
bash scripts/install_external_deadman.sh test
```

Exit codes: 0 ok, 1 escalated, 2 below threshold, 3 config error.

### 2.2 Host SMART collector (Gap #11)

Already installed on the operator's primary host as of 2026-05-24. To
install on a fresh machine:

```bash
brew install smartmontools
bash scripts/install_host_smart_collector.sh install
bash scripts/install_host_smart_collector.sh start  # one immediate pass
```

The LaunchAgent runs daily at 04:00 local time and writes one row per
disk to `workspace/healing/host_smart.jsonl`. The gateway-side monitor
reads that file every 24h and surfaces wear/spare/error/temperature
alerts.

### 2.3 Optional: tune the monthly cost cap (Gap #2)

Default $200/mo across the entire system. Adjust via:

* **React**: `/cp/settings` → "Monthly system cost ceiling" card → set
  + Save.
* **CLI / API**: `POST /api/cp/settings` with `{"total_cost_monthly_cap_usd": 500}`.

The brake engages at 95% of cap (pauses MEDIUM+HEAVY idle jobs;
LIGHT continues with their own per-subsystem caps) and releases at 70%
(hysteresis).

---

## 3. Runtime switches added

All eleven default ON (the brake-state flag is a state, not policy):

| Key | Effect when ON | Effect when OFF |
|---|---|---|
| `config_coherence_monitor_enabled` | Weekly invariant audit + Signal alerts | No audit |
| `total_cost_ceiling_enabled` | Aggregate cost monitor + brake | No top-level cap (per-subsystem caps still apply) |
| `total_cost_monthly_cap_usd` | (float, default 200) | n/a |
| `idle_pause_due_to_budget` | State flag — set by monitor at 95%, cleared at <70% | Manual override allowed but re-evaluated next pass |
| `capability_inventory_enabled` | Weekly write of `wiki/self/capability_inventory.md` | No regeneration (existing file untouched) |
| `discovery_funnel_enabled` | Weekly snapshot + briefing section | Snapshot stale; briefing section auto-hides |
| `knowledge_currency_monitor_enabled` | Weekly per-KB stagnation audit | No audit |
| `hardware_health_monitor_enabled` | Daily SMART read from `host_smart.jsonl` | No alerts; host collector still appends |
| `privacy_audit_enabled` | `GET/POST /api/cp/privacy/*` answer | Endpoints return `{enabled: false}` |
| `deadman_last_resort_enabled` | SMS+email fired when Signal+Push both fail on critical | No fallback |
| `drill_prompt_injection_resistance_enabled` | Quarterly drill in scheduler | Drill registered but never auto-runs |

---

## 4. Surfaces

### REST

* `GET  /api/cp/budgets/total` — aggregate spend, projection, brake state.
* `GET  /api/cp/funnel?window_days=90` — discovery → adoption funnel.
* `GET  /api/cp/settings/genealogy?limit=50` — recent runtime-settings flips
  with before/after/actor/reason + hash-chain status.
* `GET  /api/cp/privacy/audit/{subject_type}/{subject_id}` — what does the
  system know about this subject?
* `POST /api/cp/privacy/forget` — forget by subject_id; confirm_phrase
  required: `"FORGET <subject_type>:<subject_id>"`.

### React (`/cp/settings`)

* **`SettingsGenealogyCard`** — last 25 flips with before/after/reason +
  chain integrity status.
* **`TotalCostCeilingCard`** — current spend / cap / brake state + cap
  adjuster.

### Briefing section

* **`discovery-funnel`** candidate auto-discovered by
  `briefing_evolution.catalog`. Surfaces in weekly briefings when there
  is funnel activity OR a stagnant source is detected. Auto-hides on
  empty data.

### CLI

No new CLI subcommands — the existing `aai status` + REST round-trips
cover the new endpoints once `Authorization: Bearer <gateway-secret>`
is set.

---

## 5. What you'll see in the wild

### First-week behavior on a healthy system

* `total_cost_ceiling` monitor runs every 6h; while spend is <80% of cap,
  no alert fires.
* `config_coherence` runs weekly; expect zero findings on canonical
  defaults.
* `knowledge_currency` runs weekly; on a fresh system every KB reports
  `n_rows: 0` (not stagnant by definition).
* `hardware_health` runs daily; if the host collector is installed, you
  get a single SMART snapshot per disk per 24h. Healthy SSDs report
  `wear_pct < 10`, `spare_pct == 100`, zero `media_errors`.
* `discovery_funnel` runs weekly; briefing section appears when
  `proposal_bridge` has stagings OR `change_requests/audit.jsonl` has
  rows in the last 90 days.

### When things start to drift

* **Spend creep** — `total_cost_ceiling` alerts at 80% mid-month (one
  warning per calendar month). At 95% the brake engages, MEDIUM+HEAVY
  idle jobs are deferred with `reason="budget_brake"` (visible in
  `_publish_deferral` events). Release at 70% fires a recovery alert.
* **Misconfiguration** — `config_coherence` collects findings across
  rules; one Signal alert per pass with the consolidated set, deduped
  28d on the rule-id set.
* **Disk degradation** — `hardware_health` fires at SSD wear ≥80% (warn),
  ≥100% (critical), pending sectors present, uncorrectable errors >0, or
  spare pool ≤ vendor threshold.
* **Stagnant KB** — `knowledge_currency` alerts once per 28d when ≥1 KB
  has median row age >365d AND last_add >180d AND n_rows ≥10.
* **Silent observation** — `discovery_funnel` surfaces stagnant sources
  (≥5 stagings, 0 applied in 90d) in the weekly briefing.

### When Signal + Push both fail on a critical alert

`last_resort.maybe_fire_last_resort` is invoked. With Twilio + SMTP creds
present, the operator gets an SMS AND an email within seconds of the
critical notify. Without creds, the helper logs a debug line and returns
silently — the operator should set up creds before relying on critical
alert delivery.

### When the whole gateway is dark

The external `external_deadman.py` cron job pings `${DASHBOARD_URL}/health`
every 6h. Three consecutive failures → SMS + email. Recovery (probe
succeeds after the alert was firing) sends a recovery message.

---

## 6. Composability with existing systems

* **Cost-ceiling brake + substrate policy** — the idle scheduler's MEDIUM
  + HEAVY phases now consult `_budget_brake_engaged()` before the
  existing `_substrate_defer_reason()` check. Both can defer; budget
  wins precedence (cheaper to skip than to consult disk pressure).
* **Privacy aggregator + person_model forget** — the aggregator's
  `forget_subject(person, ...)` delegates to `person_model.forget()`.
  Existing per-data-type forget paths are unchanged; the aggregator is
  a unifier, not a replacement.
* **Settings genealogy + audit.log** — both records exist for every
  flip; genealogy adds the operator's `__reason__` field. Both are
  hash-chained (audit.log via its existing chain, genealogy via the new
  one).
* **Capability inventory + tool registry** — inventory reads the tool
  registry. It does not modify it. The generated markdown is regenerated
  weekly; operator pin blocks survive every regeneration.
* **Adversarial drill + change_requests validator** — the drill never
  pumps probes through the real Commander path (would be ~5 min/quarter
  of LLM time). It tests the catalog of canonical injection markers is
  structurally classifiable. End-to-end injection-defense testing remains
  the responsibility of the validator + external_action_gate +
  verification_extension chain.

---

## 7. Testing

Each gap has 9–19 tests at `tests/test_<gap>.py`. All test files use
`pytest.importorskip("pydantic_settings")` at the top so they skip
cleanly on a host without the gateway deps and run end-to-end in
Docker / CI. Total: **118 new test cases**.

The host-runnable subset (12 cases, no pydantic dep): `test_settings_genealogy.py`.

---

## 8. Operator yearly check (the 30-minute version)

Once a year, verify:

1. `GET /api/cp/budgets/total` returns a `spend_usd` that's stable
   vs. last year + projection within cap.
2. `GET /api/cp/funnel` shows non-zero `cr_applied` across at least
   one source (proves observation → action is alive).
3. `GET /api/cp/settings/genealogy?limit=200` shows recent flips with
   meaningful `__reason__` strings (proves the operator is still
   recording intent, not just clicking).
4. `wiki/self/capability_inventory.md` has been regenerated within
   the last 14d (proves the LIGHT idle is running).
5. The external `external_deadman.py` cron has run at least once in
   the last 6h (`$HOME/.andrusai_deadman/state.json`'s `last_probe_at`).
6. `bash scripts/install_host_smart_collector.sh start` — manually
   trigger one SMART read, confirm the latest `host_smart.jsonl` row
   has `wear_pct < 80` for every disk.

Without this annual ritual, no resilience layer can protect against
silent operator drift.

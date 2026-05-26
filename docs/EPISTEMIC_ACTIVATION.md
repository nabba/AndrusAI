# Epistemic Activation — six-stage runbook (2026-05-26)

Closes the activation gap: the epistemic stack (`gate_output` + calibration
+ verification extension) had every consumer wired but no producer. Flipping
`EPISTEMIC_ENABLED=true` was a behavioural no-op because the claim ledger
was empty (only `improvement_narrative.py` and `dossier/collector.py` were
emitting, both infrequent paths).

This document is the operator runbook for the six-stage activation. All
changes are additive, default-conservative, no TIER_IMMUTABLE touches, no
Tier-3 amendments. Each stage is reversible via the matching runtime
setting.

## What landed

**9 new modules:**

| Module | Stage | Role |
|---|---|---|
| `app/epistemic/retrieval_producer.py` | A | One `Claim` per RAG passage. ASSUMED status, INTERNAL register, per-task LRU dedup, zero LLM cost. |
| `app/epistemic/verdict_telemetry.py` | B | `record_verdict()` + `latest_verdict_for_task()`. JSONL at `workspace/epistemic/gate_verdicts.jsonl`, capped 50k rows. |
| `app/observability/epistemic_advisory_report.py` | B | Aggregator + CLI. Mirrors `goodhart_advisory_report.py`. |
| `app/epistemic/promotion_gate.py` | C | `can_promote_to_enforcing()` returns `PromotionVerdict` with soak / sample / block-rate / bias-review gates. |
| `app/healing/monitors/epistemic_gate_health.py` | C | 43rd healing monitor. 4 alert classes: silent_gate / drift_high / drift_low_zero / starved_gate. |
| `app/risk_classifier/per_reply.py` | D | Sender-prefix + financial-regex classifier. Registers zone via existing `verification_extension.register_zone_for_task`. |
| `app/tensions/seed.py` | E | Operator-callable one-shot. 12 curated tensions across operational / epistemic / value / design. |
| `app/epistemic/reaction_bridge.py` | F | Signal-ts → task context map. 👎 routes to `record_override` (gate intervened) or `record_disagreement` (gate shipped). |
| `tests/test_epistemic_retrieval_producer.py` | A | 10 contract tests: off-by-default / dedup / score normalization / failure isolation / shape pinning. |

**8 edited files** (all additive; defaults preserve current behaviour):

| Path | Change |
|---|---|
| `app/agents/commander/context.py` | 3 RAG loaders gain `task_id` kwarg + `emit_retrieval_claims` call. |
| `app/agents/commander/orchestrator.py` | Threads `_claim_task_id` to loaders + zone classification + verdict telemetry around `gate_output()` at line 3651. |
| `app/main.py` | Captures `_reply_ts` from `send_durable`, registers bridge + adds 8th reaction handler block. |
| `app/runtime_settings.py` | 3 new keys + getters/setters: `epistemic_retrieval_producer_enabled` (default OFF), `epistemic_gate_health_monitor_enabled` (ON), `epistemic_per_reply_zone_enabled` (ON). |
| `app/healing/monitors/__init__.py` | Registers `epistemic_gate_health` monitor (daily cadence). |
| `app/cli/main.py` + `commands.py` | `aai advisory epistemic` subcommand. |
| `app/epistemic/verdict_telemetry.py` | (Self) Added `latest_verdict_for_task()` for Stage F resolution. |

## The data flow

```
Stage A producer → control_plane.epistemic_claims
                          ↓
Stage D classifier → register_zone_for_task
                          ↓
gate_output() at orchestrator.py:3651
   ├─→ Stage B telemetry → workspace/epistemic/gate_verdicts.jsonl
   │     ↓                    ↓
   │     ↓     Stage C monitor (silent / drift / starved alerts)
   │     ↓     Stage B advisory report (operator-pulled)
   │     ↓     Stage C promotion_gate (operator-pulled)
   │     ↓
   └─→ ship / revise / block → send_durable
                                    ↓
                                  reply_ts
                                    ↓
                          Stage F bridge.register(...)
                                    ↓
                    👎 → handle_reaction → record_override
                                       OR record_disagreement
                                    ↓
                          Calibration sees signal next pass
```

## Operator activation order

**Day 0 (now)** — Code is landed. No behavioural change. The two observational
master switches default ON (`epistemic_gate_health_monitor_enabled`,
`epistemic_per_reply_zone_enabled`) but both are no-ops until `EPISTEMIC_ENABLED`
is on. The producer (`epistemic_retrieval_producer_enabled`) defaults OFF.

**Day 1** — Flip Stage A producer:
```bash
curl -X POST $GW/api/cp/settings \
  -H "Authorization: Bearer $GW_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"epistemic_retrieval_producer_enabled": true}'
```
Ledger starts growing. Verify:
```bash
psql -c "SELECT COUNT(*) FROM control_plane.epistemic_claims \
         WHERE tags::text LIKE '%retrieval%' \
           AND created_at > NOW() - INTERVAL '1 day'"
```

**Day 7** — Inspect ledger shape. If sane, flip Stage B (advisory):
```bash
curl -X POST $GW/api/cp/settings -d '{"epistemic_enabled_override": true}'
```
Verdict telemetry starts persisting. **Leave `epistemic_blocking_mode_override`
untouched** (None/false). Stage C health monitor begins watching.

**Day 7–37** — Soak. Stage F is already collecting 👎 disagreements during this
window. Optional early peek:
```bash
aai advisory epistemic --window-days 7
```

**Day 37** — Run promotion check:
```bash
python -c "from app.epistemic.promotion_gate import can_promote_to_enforcing; \
           import json; \
           print(json.dumps(can_promote_to_enforcing().as_jsonable(), indent=2))"
aai advisory epistemic --window-days 30
```
If `can_promote=true` AND the top-reasons list looks defensible:
```bash
curl -X POST $GW/api/cp/settings -d '{"epistemic_blocking_mode_override": true}'
```
Stage D zone-aware thresholds engage automatically.

**Day 37+ (optional)** — If you want `gate_philosophy` (Stage E):
```bash
python -m app.tensions.seed
# soak tensions KB for ~30d as detect_and_store accumulates real conflicts
curl -X POST $GW/api/cp/settings -d '{"gate_philosophy_enabled": true}'
```

## Master switches (defaults)

| Switch | Default | Stage |
|---|---|---|
| `epistemic_retrieval_producer_enabled` | **OFF** | A |
| `epistemic_enabled_override` | None (env) | B |
| `epistemic_blocking_mode_override` | None (env) | C |
| `epistemic_gate_health_monitor_enabled` | **ON** | C |
| `epistemic_per_reply_zone_enabled` | **ON** | D |
| `verification_extension_enabled` | OFF (pre-existing) | D |
| `gate_philosophy_enabled` | OFF (pre-existing) | E |

## Reversibility

Every stage unwinds by flipping its master switch back. The producer's claims
accumulate even when the gate is off — that's fine, the data is useful when
you re-enable. To wipe:

```bash
# Wipe claim ledger:
psql -c "TRUNCATE control_plane.epistemic_claims"

# Wipe verdict telemetry:
rm workspace/epistemic/gate_verdicts.jsonl

# Wipe Stage F bridge + disagreements:
rm workspace/epistemic_reaction_bridge.json
rm workspace/epistemic/operator_disagreements.jsonl
```

## Health monitor alert classes

The `epistemic_gate_health` monitor fires week-keyed Signal alerts on:

* **`silent_gate`** — gate is on but 0 verdicts in 7d. Telemetry hook broken
  or gate isn't reached.
* **`drift_high`** — 7d would-have-blocked rate ≥2× 30d baseline AND abs >10%.
  Either a new producer source is misbehaving or the agent population is
  genuinely emitting more questionable claims.
* **`drift_low_zero`** — 7d rate is 0 but 30d was non-zero. A detector likely
  regressed silently.
* **`starved_gate`** — producer is on but ledger p50 size <2 across 7d. The
  producer isn't actually feeding the gate.

## Promotion gate decision logic

`can_promote_to_enforcing()` returns `PromotionVerdict(can_promote, reasons,
snapshot)` with these blocking conditions (in order):

1. Stage A producer off → ledger is empty.
2. Sample size < 1000 verdicts in window.
3. Would-have-blocked rate > 25% (interferes with too many real replies).

Informational only (won't block promotion):
* Top biases observed → operator must ack via React Settings card.

The React Settings card pairs this with a typed-phrase confirmation
("PROMOTE EPISTEMIC GATE TO ENFORCING") for the irreversible-ish flip.

## What deliberately didn't land

* **LLM-based auto-extractor.** Structural producer covers all 12 RAG
  context loaders. An LLM extractor (Producer P2 in the original plan) is
  Stage A+ if coverage feels thin after a week of soak.
* **React Settings card.** REST data is available via
  `python -m app.observability.epistemic_advisory_report --json`. Card is
  a one-day follow-up if you want it surfaced in `/cp/settings`.
* **New identity-event kind.** Gate-health alerts are *operational*, not
  identity-shaping — they go through the existing event surface rather
  than minting a 22nd kind.
* **Automated promotion.** `can_promote_to_enforcing()` is advisory only;
  operator typed-flips the override. Same discipline as Goodhart's
  three-mode card.

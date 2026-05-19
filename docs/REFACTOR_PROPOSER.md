# Refactor-proposal producer (Phase 2 of elegance plan)

Status: shipped 2026-05-18, default **OFF**, observational (proposes only).

## Why this exists

Phase 1 (`elegance_drift`, `architectural_drift`) gave the system *eyes*
on post-merge code health — it surfaces drift to Signal + the identity
continuity ledger. But the loop didn't close: the operator still had to
read the alert, decide what to do, and file a CR by hand.

Phase 2 is the *arrow* from signal to action. It reads the Phase 1
artefacts, picks high-confidence refactor candidates, and stages
structured proposals through the standard operator-gated CR flow.
Composes with — does not replace — the existing mutation gate
(`code_quality`, `architectural_review`); the gate catches what AVO
ships, this producer catches what the gate didn't see.

## Where it lives

| File | Role |
|---|---|
| [app/refactoring/__init__.py](crewai-team/app/refactoring/__init__.py) | Package entry; eager-starts the daemon at import |
| [app/refactoring/proposer.py](crewai-team/app/refactoring/proposer.py) | Three detectors + daemon |

Anchored from [app/healing/__init__.py](crewai-team/app/healing/__init__.py) alongside the other observational
producers (`capability_gap_analyzer`, `library_radar`, `paper_pipeline`,
`dependency_radar`).

## How a proposal flows

```
Phase 1 monitors      Phase 2 proposer            Bridge promoter         Operator
─────────────────     ────────────────             ───────────────         ────────
elegance_drift     →  detect_complexity_hotspots  → stage(...)         →  Signal 👍 / /cp/changes
                      detect_import_cycles         (workspace/             ↓ approve
architectural_drift→  detect_parallel_caps          proposal_bridge/      Apply via change_requests
                                                    refactor_proposer/)    lifecycle (60-min
                                                                            auto-revert window)
```

The proposer **never touches code**. It writes only to
`workspace/proposal_bridge/refactor_proposer/<sig>.{md,json}`. The
markdown body is what lands at `docs/proposed_refactor/<sig>.md` if
the operator approves; the JSON is bridge metadata.

## Three detectors

### `detect_complexity_hotspots`

Reads `workspace/code_quality/elegance_history.json`. For each tracked
file: re-measure the live `QualityScore` (the history only stores
composite — we need per-dimension visibility to confirm complexity is
the dominant lever). Surface when **both** hold:

- `composite ≤ 0.65`
- `complexity_score ≤ 0.40` (corresponds to McCabe ≥ ~19)

This filters out files whose low composite comes purely from missing
docstrings — those aren't worth a refactor CR, they're worth a
docstring pass.

Signature buckets per file by `composite × 10` rounded — same level
of regression yields the same proposal (bridge dedupes); a fresh drop
yields a fresh proposal.

### `detect_import_cycles`

Reads `cycles` from `workspace/code_quality/architectural_baseline.json`.
Keeps only SCCs with `2 ≤ size ≤ 20` — systemic SCCs (>20 members)
are coupling shapes, not refactor candidates.

Signature is a sha256-hash of the sorted member list — adding or
removing members changes the signature, signalling the proposer
to re-stage.

### `detect_parallel_capabilities`

Reads `capability_owners` from the same baseline. Surfaces capabilities
owned by ≥3 distinct files. Some parallels are legitimate (`registers-tool`
is a meta-tag) — the proposal body explicitly invites the operator to
either consolidate, rename, or document as an intentional meta-tag.

Signature includes the owner-set hash so adding a new owner re-stages.

## Per-pass discipline

- **Default OFF.** Conservative first ship. Operator flips ON via
  `/cp/settings` once they've reviewed Phase 1 baselines.
- **Per-detector cap: 3 candidates.** A backlog of refactor candidates
  spreads over many weeks via the bridge cooldown rather than flooding
  the operator on day one.
- **14-day bridge cooldown.** Refactors are never urgent; this also
  doubles as a "did the Phase 1 signal persist?" filter.
- **Weekly poll cadence.** Cheap — detectors are pure functions over
  persisted JSON; no fresh scan, no LLM calls.
- **Failure-isolated.** Each detector / stage call is try/except so a
  broken detector never blocks the others, and a broken bridge stage
  never blocks the next pass.

## What every proposal contains

Each `RefactorCandidate` carries:

```python
@dataclass(frozen=True)
class RefactorCandidate:
    detector: str            # 'complexity_hotspot' | 'import_cycle' | 'parallel_capability'
    signature: str           # stable hash; same input → same signature
    title: str               # operator-readable headline
    body_markdown: str       # the body that lands at target_path on approval
    target_path: str         # docs/proposed_refactor/<sig>.md
    coding_session_spec: dict[str, Any]  # scaffold for the implementer
```

`coding_session_spec` is the bridge between proposal and execution. It
follows the schema used by `library_radar`'s trial scaffolds:

```python
{
    "intent": str,              # one-sentence goal
    "files": list[str],         # files the refactor will touch
    "acceptance": list[str],    # how the implementer knows they're done
    "expected_duration_min": int,
}
```

An agent (or an operator with the coder) reads the spec and runs a
`coding_session` to attempt the refactor. The session's `submit` step
fans out CRs per touched file through the same operator gate the rest
of the system uses.

## Composition with safety nets

- **TIER_IMMUTABLE absolute.** Every `proposal_bridge.stage` call
  validates `target_path` against `change_requests.validator.validate`
  at stage time. Even if a detector hallucinated a TIER_IMMUTABLE
  path, the bridge would refuse before the cooldown timer started.
  See [test_target_paths_pass_change_request_validator](crewai-team/tests/test_refactor_proposer.py).
- **Operator gate intact.** The bridge promoter files a CR through
  `change_requests.lifecycle.create_request` — same operator-approval
  surface as every other producer, same 60-min auto-revert window.
- **No verification short-cut.** Phase 2 does not implement
  `app/coding_session/refactor_verify.py` from the original plan —
  that's the next ship. Until it lands, refactor sessions rely on the
  existing test suite + the operator's review at the CR gate. This is
  deliberate: I'd rather ship the proposer with the existing gate than
  hold both behind a single big PR.

## Live first-run findings (against the real codebase)

With Phase 1 baselines populated (see `docs/CODE_HEALTH_OBSERVATION.md`),
running the proposer once with the master switch ON surfaced 7 candidates
from 3 detectors:

- 2 **complexity hotspots**: `app/agents/commander/commands.py` (composite 0.5x),
  `app/subia/introspection/topics/scorecard.py` (composite 0.6x).
- 3 **import cycles**: `healing/monitors/{__init__, disk_quota}.py`,
  `tool_registry/{decorator, registry}.py`, `vacation_mode/{digest, state}.py`.
- 2 **parallel capabilities**: `registers-tool` (3 owners — likely meta-tag),
  `renders-pdf` (3 owners — worth investigating).

None of these are surprising — they're the same drift the Phase 1
monitors surfaced as alerts. The difference is they now land in the
proposal queue with concrete refactor plans attached, ready for the
operator's 👍.

## What this phase deliberately does NOT do

- **No `app/coding_session/refactor_verify.py`.** Semantic-equivalence
  verification of refactor sessions is the next slice — the proposer
  produces the *intent*, the verifier confirms the *behaviour* was
  preserved.
- **No auto-apply.** Every proposal goes through the standard operator
  gate. The auto-apply infrastructure exists (PROGRAM §38.3) but its
  allowlist deliberately ships empty.
- **No detector for `duplication_cluster`, `dead_code`,
  `single_use_abstraction`, or `centrality_drift`.** Originally planned
  as part of Phase 2; deferred because:
    - `duplication_cluster` needs token-level diff (jscpd or pylint
      duplicate-code dependency).
    - `dead_code` needs reference analysis — high false-positive risk
      without it.
    - `single_use_abstraction` needs a call graph — the
      `system_inventory` snapshot has the symbols but not yet the
      reference edges.
    - `centrality_drift` already surfaces as part of
      `architectural_drift`'s alert; promoting it to a CR-emitting
      detector is incremental.

  Adding any of these is a one-detector additive change against the
  same producer skeleton.

## Tests

[tests/test_refactor_proposer.py](crewai-team/tests/test_refactor_proposer.py) — 13 tests covering:

- Each detector's threshold logic
- Signature stability (idempotent re-runs)
- Per-detector cap enforcement
- Empty-baseline behaviour
- `run_one_pass` end-to-end through the bridge
- Idempotency on identical signal
- `target_path` validation against the CR validator

All 13 pass. The 21 existing `tests/proposal_bridge/` tests also pass
unchanged.

## Operator surfaces

- `workspace/proposal_bridge/refactor_proposer/<sig>.md` — staged
  bodies (cleaned up after CR resolution + 14d audit)
- `workspace/proposal_bridge/refactor_proposer/<sig>.json` — bridge
  metadata
- `/cp/changes` — the CR that lands after the 14d cooldown
- Continuity ledger event kind `architectural_debt_drift` — Phase 1
  monitors emit this, Phase 2 inherits the same kind so the annual
  reflection picks up "this year we refactored X cycles and Y
  hotspots."

## Master switch

`runtime_settings.refactor_proposer_enabled` (default OFF). Falls back
to `REFACTOR_PROPOSER_ENABLED` env var when runtime_settings is
unavailable (e.g. tests, early-boot). Flippable via `/cp/settings`
without a gateway restart.

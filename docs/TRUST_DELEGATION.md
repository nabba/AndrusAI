# Trust Delegation — Scoping (Tier 2.5)

**Status**: SCOPING ONLY. Not implemented. Requires explicit operator
decision before any code lands.

## Problem

The system has exactly one trust anchor: the primary operator. Every
gated decision (change requests, Tier-3 amendments, governance ratchet
relaxations, executor runs) terminates at the operator's 👍 / 👎 / typed
phrase. For decade-class operation, this is a single point of failure:

  * Operator becomes temporarily unavailable (illness, travel, life
    event) → the queue accumulates.
  * Operator permanently unavailable (death, incapacitation) → the
    system slowly degrades as queues overflow and watchdogs trip.

Q17.4 (`app/operator_transition/`) already detects the unavailability
phases (ACTIVE → ABSENT_30D → ABSENT_90D → READ_MOSTLY → TRANSITIONED).
The successor declaration at `workspace/operator_transition/successor.json`
is operator-authored and human-read — the system never acts on it
autonomously.

The §38.3 auto-apply CR infrastructure ships dormant (empty allowlists).
Vacation Mode (Q16 Theme 3) is the closest delegated-authority path,
time-bounded with hard caps.

## Possible design

A second trust anchor — a "designated successor" — who can authorize
lower-tier amendments when the primary operator is ABSENT_90D for >= 30
days. Mechanics:

  1. Primary declares successor (typed phrase: `DECLARE SUCCESSOR
     <key_fingerprint>`). Stored alongside successor.json.
  2. Successor's authorization key is a hardware token or signed cert,
     same trust model as the primary's gateway secret.
  3. Successor can approve change-requests up to a **delegation tier**
     (a new column on `ChangeRequest`): standard CRs only, no Tier-3,
     no governance relaxation, no TIER_IMMUTABLE.
  4. Activation: only when operator_transition phase ≥ ABSENT_90D AND
     operator-authored grace period has elapsed.
  5. Audit: every successor-authorized action lands in a new
     `successor_action` identity-continuity event kind. Visible to
     annual_reflection.

## Risks

  * **Hostile successor**: a successor turned adversary now has
    bounded-but-real authority over the primary's system. Mitigation:
    delegation tier is narrow; TIER_IMMUTABLE / Tier-3 / governance
    ratchet stay primary-only; revocation by primary is instant.
  * **Successor key compromise**: same model as primary key compromise.
    Mitigation: SOUL.md and CONSTITUTION.md absolute.
  * **False activation**: a primary on a 4-month sabbatical is not the
    same as a primary who is gone. Mitigation: extended grace period;
    primary 👎 on any successor action force-reverts.
  * **Coordination failure**: successor may have a different ethical
    posture. Mitigation: delegation tier is small; high-stakes
    decisions still wait for primary.

## Recommendation

**DEFER until explicitly requested by the operator.** The risks are
real. The benefit (less queue accumulation during 30-90d absences) is
modest — the existing absence_policy (§63 P1) already auto-applies
patch-level upgrades during operator absence with conservative gates.
The need for a second human trust anchor is not yet demonstrated.

If implemented later, the design above is the starting point. Hold for
operator decision.

## Operator decision required

  * Should we proceed with trust delegation now?
  * If yes, what delegation tier is acceptable? (recommendation: only
    `STANDARD` change-requests, never Tier-3 / governance / TIER_IMMUTABLE)
  * Who would be the designated successor?
  * What grace period before ABSENT_90D activation? (recommendation:
    30 days — gives a 4-month total grace)

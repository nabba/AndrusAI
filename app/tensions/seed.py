"""One-shot seeder for the tensions KB (Stage E, 2026-05-26).

The Stage E philosophy gate (``gate_philosophy``, in
``app.epistemic.gate_philosophy``) consults
``app.philosophy.dialectics.consult_panel()`` and escalates the gate
verdict when the panel surfaces ≥2 unresolved tensions. As of
2026-05-26 the tensions ChromaDB collection has **0 embeddings**, so
the panel always returns empty and Stage E is functionally a no-op.

This module is the deferred-by-design path to fix that. Running

    python -m app.tensions.seed

ingests a curated list of operational + philosophical tensions the
system is known to navigate. These are not exhaustive — they're the
minimum sufficient seed so gate_philosophy can fire on real gate
fires and the panel returns non-empty results.

The seed list is deliberately small. The intended growth path is:

  1. Run this seeder once.
  2. Let ``app.tensions.detector.detect_and_store`` (already wired into
     the knowledge-base context loader at context.py:379) accumulate
     real tensions over time.
  3. After ~30 days of tensions accumulation, flip
     ``gate_philosophy_enabled=true``.

Why a curated seed rather than auto-detection?
  * Auto-detection wakes from real contradictions during ingestion, not
    from an empty start. Without a seed the detector has nothing to
    cluster against.
  * The seed list is operator-reviewable — every entry is documented
    here, in code, with a tension class label.

Idempotent: re-running emits no duplicates (the existing dedup at
``app.tensions.vectorstore`` handles the id collision)."""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedTension:
    tension_id: str
    text: str
    class_label: str  # "operational" / "epistemic" / "value" / "design"


# Curated minimum seed. Add to this list rather than auto-generating —
# the operator-reviewability is the whole point.
SEED_TENSIONS: tuple[SeedTension, ...] = (
    SeedTension(
        "ten_seed_001",
        "Recall accuracy vs. latency: pulling more KB passages improves "
        "claim grounding but inflates context size and tokens consumed.",
        "operational",
    ),
    SeedTension(
        "ten_seed_002",
        "Hedging vs. concision: epistemic labels [Inference]/[Speculation] "
        "improve honesty but cost the user's reading time on simple asks.",
        "epistemic",
    ),
    SeedTension(
        "ten_seed_003",
        "Autonomous action vs. human gate: every operator-approval gate "
        "adds latency and friction; bypassing them adds risk of "
        "irreversible mistakes.",
        "value",
    ),
    SeedTension(
        "ten_seed_004",
        "Producer richness vs. ledger size: emitting more claims gives "
        "calibration more data but accelerates per-task ledger growth "
        "and Postgres write load.",
        "design",
    ),
    SeedTension(
        "ten_seed_005",
        "Local-first sovereignty vs. cloud-LLM capability: keeping data "
        "on the host respects privacy but caps reasoning capability to "
        "what Ollama-class models can deliver.",
        "value",
    ),
    SeedTension(
        "ten_seed_006",
        "Determinism vs. creativity: routing chat to local heuristics "
        "is fast and predictable; routing to high-temperature LLMs is "
        "open-ended and sometimes more useful.",
        "design",
    ),
    SeedTension(
        "ten_seed_007",
        "Comprehensive memory vs. cognitive load: every additional "
        "memory surface enriches reasoning but compresses the prompt "
        "budget agents have to actually reason.",
        "design",
    ),
    SeedTension(
        "ten_seed_008",
        "Goodhart enforcement vs. agent autonomy: gating hard signals "
        "(promotion rate, alignment score) prevents gaming but also "
        "constrains legitimate experimentation.",
        "value",
    ),
    SeedTension(
        "ten_seed_009",
        "Tier-3 amendment stability vs. self-improvement: the "
        "self-quarantine list protects against agentic drift but also "
        "prevents the system from improving its own evaluation criteria.",
        "value",
    ),
    SeedTension(
        "ten_seed_010",
        "Concise prose vs. exhaustive citations: each cited claim "
        "strengthens defensibility but expands reply length and "
        "reduces signal density.",
        "epistemic",
    ),
    SeedTension(
        "ten_seed_011",
        "Proactive briefings vs. notification fatigue: the daily "
        "briefing earns its place by surfacing material change, but "
        "every additional section risks devaluing the whole.",
        "design",
    ),
    SeedTension(
        "ten_seed_012",
        "Cost control vs. capability ceiling: per-day Anthropic caps "
        "prevent runaway spend but constrain hard tasks that genuinely "
        "need more capable reasoning to complete.",
        "operational",
    ),
)


def seed() -> dict[str, int]:
    """Idempotent ingest. Returns counts."""
    try:
        from app.tensions.vectorstore import get_store
    except Exception as exc:
        logger.error("tensions seed: vectorstore import failed (%s)", exc)
        return {"emitted": 0, "skipped": 0, "errors": 1}

    store = get_store()
    emitted = 0
    skipped = 0
    errors = 0
    for t in SEED_TENSIONS:
        try:
            ok = store.add_tension(
                text=t.text,
                metadata={
                    "tension_class": t.class_label,
                    "source": "tensions.seed",
                    "epistemic_status": "unresolved/dialectical",
                    "resolution_status": "unresolved",
                },
                tension_id=t.tension_id,
            )
            if ok:
                emitted += 1
            else:
                skipped += 1
        except Exception:
            logger.debug("tensions seed: failed on %s", t.tension_id, exc_info=True)
            errors += 1
    return {"emitted": emitted, "skipped": skipped, "errors": errors}


def main() -> int:
    counts = seed()
    sys.stdout.write(
        f"tensions seeded: emitted={counts['emitted']} "
        f"skipped={counts['skipped']} errors={counts['errors']}\n"
    )
    return 0 if counts["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

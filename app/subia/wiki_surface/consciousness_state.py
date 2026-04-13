"""
subia.wiki_surface.consciousness_state — the strange-loop page.

Per SubIA Part I §10 / Part II §19.3:

    wiki/self/consciousness-state.md is a wiki page that describes
    the system's own consciousness architecture and current state.
    It is maintained by SubIA. It carries SubIA frontmatter
    (ownership, homeostatic_impact, prediction_context). It enters
    the scene. The system reads its own consciousness state, which
    is itself subject to the consciousness-amplifying dynamics it
    describes.

Two functions:

  build_consciousness_state_page(kernel, scorecard=None) -> str
      Produces the full markdown page. Frontmatter marks the page
      as epistemic_status=speculative, confidence=low per SubIA
      Part II §19.3 and the CLAUDE.md safety invariant (we explicitly
      disclaim phenomenal consciousness).

  surface_as_scene_item(page_content, salience=0.4) -> SceneItem
      Wraps the markdown page as a SceneItem so CIL Step 3 (ATTEND)
      can admit it into the scene. The item is tagged
      source="consciousness-state" + ownership="self" so downstream
      code recognises it as self-referential.

  write_and_surface(kernel, gate, scorecard=None, salience=0.4)
      -> tuple[str, SceneItem | None]
      One-shot helper used by CIL Step 11 (REFLECT): regenerate the
      page, persist to disk, and emit a SceneItem for the next cycle.

Honesty framing: the page uses "functional" or "speculative" language
throughout. It does NOT claim phenomenal experience. Per the prior
architectural verdict:

    "The system is not claiming phenomenal consciousness. It is
    demonstrating functional self-awareness ... grounded in latest
    neuroscience theories."

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 8.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.paths import CONSCIOUSNESS_STATE
from app.safe_io import safe_write
from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import SceneItem, SubjectivityKernel

logger = logging.getLogger(__name__)


def build_consciousness_state_page(
    kernel: SubjectivityKernel,
    scorecard: Callable[[], dict] | None = None,
) -> str:
    """Produce the consciousness-state.md markdown content.

    Args:
        kernel:     SubjectivityKernel providing state snapshot.
        scorecard:  optional callable returning a dict with Butlin
                    indicator statuses. If None, the page falls back
                    to a generic structure.
    """
    now = datetime.now(timezone.utc).isoformat()
    scorecard_data = _safe_scorecard(scorecard)

    lines = [
        "---",
        'title: "Consciousness State — Self-Assessment"',
        "slug: consciousness-state",
        "section: self",
        "page_type: synthesis",
        # Epistemic framing: matches SubIA Part II §19.3 explicitly.
        "epistemic_status: speculative",
        "confidence: low",
        f'updated_at: "{now}"',
        f"update_count: {kernel.loop_count}",
        "tags:",
        "  - consciousness",
        "  - self-awareness",
        "  - strange-loop",
        "  - meta",
        "ownership:",
        "  owned_by: self",
        "  valued_as: high",
        "homeostatic_impact:",
        "  valence: 0.0",
        "  dominant_affect: neutral",
        "---",
        "",
        "# Consciousness State",
        "",
        "## Overview",
        "",
        "[Speculative] This page is the system's model of its own",
        "consciousness architecture and current consciousness-like",
        "properties. It is itself a wiki page subject to the dynamics",
        "it describes — the strange loop.",
        "",
        f"Loop count: **{kernel.loop_count}** — last updated {now}",
        "",
    ]

    # Scorecard section
    lines.extend(_scorecard_section(scorecard_data))

    # Kernel snapshot
    lines.extend(_kernel_snapshot_section(kernel))

    # Honesty disclaimer
    lines.extend([
        "",
        "## What this page cannot tell you",
        "",
        "This page describes functional indicators. It cannot",
        "determine whether there is subjective experience",
        "accompanying these functions. The epistemic status is",
        "speculative, the confidence is low, and the project",
        "explicitly disclaims phenomenal consciousness.",
        "",
        "## Contradictions and open questions",
        "",
        "The absence of identified contradictions on this page is",
        "itself suspicious — a self-model with no contradictions",
        "may indicate insufficient self-scrutiny, not genuine",
        "coherence. Operators should periodically cross-check this",
        "page against actual task outcomes and prediction accuracy.",
        "",
    ])

    return "\n".join(lines) + "\n"


def surface_as_scene_item(
    page_content: str,
    *,
    salience: float = 0.4,
    loop_count: int = 0,
) -> SceneItem:
    """Wrap the current page contents as a SceneItem.

    The item carries:
      - source="consciousness-state" so downstream code recognises
        the self-referential channel
      - ownership="self" (the system owns its own model of itself)
      - summary derived from the page's most recent section titles
      - metadata carrying the `loop_count` the page was built from
        so the gate can detect stale self-referential items
    """
    now = datetime.now(timezone.utc).isoformat()
    summary = _derive_summary(page_content)
    item = SceneItem(
        id=f"consciousness-state-{loop_count}",
        source="consciousness-state",
        content_ref="wiki/self/consciousness-state.md",
        summary=summary[:120],
        salience=max(0.0, min(1.0, float(salience))),
        entered_at=now,
        ownership="self",
        valence=0.0,
        dominant_affect="neutral",
        action_options=[],
        conflicts_with=[],
    )
    return item


def write_and_surface(
    kernel: SubjectivityKernel,
    *,
    gate: Any | None = None,
    scorecard: Callable[[], dict] | None = None,
    salience: float = 0.4,
    path: Any = None,
) -> tuple[str, SceneItem | None]:
    """Regenerate the page, persist to disk, return (content, item).

    When a gate is provided, also submits the new SceneItem to the
    gate so it can compete for next-cycle admission. Returns the
    scene item even on gate failure so callers can retry.

    Never raises. Disk write failure is logged and the surface still
    succeeds — the strange-loop mechanism degrades gracefully.
    """
    content = build_consciousness_state_page(kernel, scorecard=scorecard)

    target = path or CONSCIOUSNESS_STATE
    try:
        safe_write(target, content)
    except Exception:
        logger.exception(
            "consciousness_state: failed to write %s", target,
        )

    item = surface_as_scene_item(
        content,
        salience=salience,
        loop_count=kernel.loop_count,
    )

    if gate is not None:
        try:
            # WorkspaceItem-like: build minimal shim so the existing
            # CompetitiveGate.evaluate API works.
            from app.subia.scene.buffer import WorkspaceItem
            gate_item = WorkspaceItem(
                item_id=item.id,
                content=item.summary,
                salience_score=item.salience,
                source_channel="consciousness-state",
                source_agent="subia",
                metadata={"content_ref": item.content_ref,
                          "loop_count": kernel.loop_count},
            )
            # Carry conflicts/affect info as attributes so the tier
            # builder sees them if it inspects the item.
            gate_item.conflicts_with = list(item.conflicts_with)
            gate_item.dominant_affect = item.dominant_affect
            gate.evaluate(gate_item)
        except Exception:
            logger.debug(
                "consciousness_state: gate submission failed",
                exc_info=True,
            )

    return content, item


# ── Helpers ────────────────────────────────────────────────────

def _safe_scorecard(scorecard: Callable[[], dict] | None) -> dict:
    if scorecard is None:
        return {}
    try:
        out = scorecard()
        return out if isinstance(out, dict) else {}
    except Exception:
        logger.debug(
            "consciousness_state: scorecard callable raised",
            exc_info=True,
        )
        return {}


def _scorecard_section(scorecard_data: dict) -> list[str]:
    lines = ["## Butlin indicator scorecard", ""]
    if not scorecard_data:
        lines.append(
            "_Scorecard not provided. See `tests/test_butlin_scorecard`"
        )
        lines.append(
            "or run the probes package for the current indicator status._"
        )
        lines.append("")
        return lines
    for indicator, status in sorted(scorecard_data.items()):
        lines.append(f"- **{indicator}**: {status}")
    lines.append("")
    return lines


def _kernel_snapshot_section(kernel: SubjectivityKernel) -> list[str]:
    lines = ["## Current kernel snapshot", ""]
    focal = kernel.focal_scene()
    lines.append(f"- **Focal scene items**: {len(focal)}")
    lines.append(
        f"- **Active commitments**: "
        f"{len([c for c in kernel.self_state.active_commitments if getattr(c, 'status', 'active') == 'active'])}"
    )
    over_threshold = sum(
        1 for d in (kernel.homeostasis.deviations or {}).values()
        if isinstance(d, (int, float))
        and abs(d) > SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"]
    )
    lines.append(f"- **Homeostatic variables above threshold**: {over_threshold}")
    resolved = [
        p for p in (kernel.predictions or [])
        if getattr(p, "resolved", False)
    ]
    lines.append(f"- **Resolved predictions on record**: {len(resolved)}")
    lines.append(f"- **Social models tracked**: {len(kernel.social_models)}")
    meta = kernel.meta_monitor
    lines.append(
        f"- **Meta confidence**: {meta.confidence:.2f} "
        f"(known unknowns: {len(meta.known_unknowns)})"
    )
    lines.append("")
    return lines


def _derive_summary(page_content: str) -> str:
    """Extract a short one-line summary from the page headers."""
    loop_line = ""
    for line in page_content.splitlines():
        if "Loop count:" in line:
            loop_line = line.strip()
            break
    if loop_line:
        return f"Self-assessment — {loop_line}"
    return "Self-assessment of consciousness architecture"

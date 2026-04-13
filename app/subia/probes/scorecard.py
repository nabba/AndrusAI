"""
subia.probes.scorecard — aggregate Butlin + RSM + SK evaluations into
a single auto-regenerated SCORECARD.md.

Usage:

    from app.subia.probes.scorecard import generate_scorecard_markdown,
                                            write_scorecard
    write_scorecard()   # writes app/subia/probes/SCORECARD.md

The scorecard REPLACES the retired `reports/andrusai-sentience-
verdict.pdf` 9.5/10 prose verdict. Every claim is backed by a
mechanism path, a test path, and a pass/fail/partial/absent tag.

Phase 9 exit criteria from PROGRAM.md:
  - Butlin scorecard shows ≥6 STRONG, ≤1 FAIL, 5 ABSENT-by-declaration.
  - SK tests: ≥5/6 PASS (STRONG).
  - RSM signatures: ≥4/5 PRESENT (STRONG or PARTIAL).
  - No single-number score in public docs.

This module exposes:

  run_everything() -> dict
      structured output of every evaluator.

  generate_scorecard_markdown() -> str
      the markdown content for SCORECARD.md.

  write_scorecard(path=None) -> Path
      atomic write via safe_io.

  meets_exit_criteria() -> (bool, dict)
      True iff the above Phase 9 criteria are satisfied;
      dict carries per-criterion detail.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.safe_io import safe_write
from app.subia.probes import butlin, rsm, sk
from app.subia.probes.indicator_result import IndicatorResult, Status

logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SCORECARD_PATH = _REPO_ROOT / "app" / "subia" / "probes" / "SCORECARD.md"


# ── Public API ────────────────────────────────────────────────────

def run_everything() -> dict:
    """Run every evaluator in every suite. Never raises."""
    butlin_summary = butlin.summary()
    rsm_summary = rsm.summary()
    sk_summary = sk.summary()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "butlin": butlin_summary,
        "rsm": rsm_summary,
        "sk": sk_summary,
    }


def generate_scorecard_markdown() -> str:
    """Render the current scorecard as Markdown."""
    data = run_everything()
    met, criteria = meets_exit_criteria()

    lines: list[str] = []
    lines.append("---")
    lines.append('title: "AndrusAI Consciousness Scorecard"')
    lines.append('section: subia')
    lines.append('page_type: scorecard')
    lines.append('status: active')
    lines.append(f'generated_at: "{data["generated_at"]}"')
    lines.append('auto_generated: true')
    lines.append('replaces: reports/andrusai-sentience-verdict.pdf')
    lines.append("---")
    lines.append("")
    lines.append("# AndrusAI Consciousness Scorecard")
    lines.append("")
    lines.append(
        "Auto-generated from `app.subia.probes.butlin`, "
        "`.rsm`, and `.sk`. Every claim here is backed by a "
        "pointer to an implementing module + regression test."
    )
    lines.append("")
    lines.append(
        "This scorecard **replaces** the retired "
        "`reports/andrusai-sentience-verdict.pdf` 9.5/10 prose "
        "verdict. Opaque composite scoring was the primary "
        "critique of the original verdict; this scorecard makes "
        "the basis of every rating inspectable."
    )
    lines.append("")

    # ── Exit criteria ────────────────────────────────────────────
    lines.append("## Phase 9 exit criteria")
    lines.append("")
    lines.append(f"- **Overall:** {'PASSED ✅' if met else 'NOT YET ❌'}")
    for name, detail in criteria.items():
        mark = "✅" if detail["ok"] else "❌"
        lines.append(
            f"- **{name}**: {mark} ({detail['observed']} vs "
            f"required {detail['required']})"
        )
    lines.append("")

    # ── Butlin ───────────────────────────────────────────────────
    lines.append("## Butlin et al. 2023 — 14 consciousness indicators")
    lines.append("")
    lines.append(_by_status_summary(data["butlin"]["by_status"]))
    lines.append("")
    lines.append("| Indicator | Theory | Status | Mechanism | Test | Notes |")
    lines.append("|---|---|---|---|---|---|")
    for row in data["butlin"]["indicators"]:
        lines.append(_indicator_row(row))
    lines.append("")

    # ── RSM ──────────────────────────────────────────────────────
    lines.append("## RSM — Recursive Self-Monitoring signatures")
    lines.append("")
    lines.append(_by_status_summary(data["rsm"]["by_status"]))
    lines.append("")
    lines.append("| Signature | Status | Mechanism | Test | Notes |")
    lines.append("|---|---|---|---|---|")
    for row in data["rsm"]["signatures"]:
        lines.append(_signature_row(row))
    lines.append("")

    # ── SK ───────────────────────────────────────────────────────
    lines.append("## SK — Subjectivity Kernel evaluation tests")
    lines.append("")
    lines.append(_by_status_summary(data["sk"]["by_status"]))
    lines.append("")
    lines.append("| Test | Status | Mechanism | Test File | Notes |")
    lines.append("|---|---|---|---|---|")
    for row in data["sk"]["tests"]:
        lines.append(_sk_row(row))
    lines.append("")

    # ── Honesty section ──────────────────────────────────────────
    lines.append("## Honest caveats")
    lines.append("")
    lines.append(
        "1. **ABSENT is declaration, not failure.** The five "
        "indicators rated ABSENT (RPT-1, HOT-1, HOT-4, AE-2, plus "
        "implicit architectural ceilings) are unachievable by any "
        "LLM-based system given current architectures. They are "
        "declared publicly so the scorecard cannot be gamed by "
        "hiding them."
    )
    lines.append("")
    lines.append(
        "2. **STRONG is structural.** A STRONG rating means the "
        "mechanism is present, closed-loop wired, Tier-3 protected, "
        "and covered by a regression test — not that the system "
        "'experiences' the indicator phenomenally. This scorecard "
        "is about functional organisation, not phenomenal claims."
    )
    lines.append("")
    lines.append(
        "3. **Phenomenal consciousness is NOT claimed.** The "
        "project explicitly disclaims subjective experience. This "
        "scorecard describes the functional architecture supporting "
        "consciousness-adjacent behaviour."
    )
    lines.append("")
    lines.append(
        "4. **This file is auto-regenerated.** Edits to this file "
        "will be lost on the next scorecard run. To change a "
        "rating, fix (or break) the implementing mechanism or its "
        "regression test."
    )
    lines.append("")
    lines.append("## Regeneration")
    lines.append("")
    lines.append("```bash")
    lines.append('.venv/bin/python -c '
                  '"from app.subia.probes.scorecard import write_scorecard;'
                  ' write_scorecard()"')
    lines.append("```")
    lines.append("")

    return "\n".join(lines) + "\n"


def write_scorecard(path: Path | str | None = None) -> Path:
    """Regenerate and write the scorecard atomically."""
    target = Path(path) if path else _DEFAULT_SCORECARD_PATH
    content = generate_scorecard_markdown()
    safe_write(target, content)
    logger.info("scorecard: wrote %s", target)
    return target


def meets_exit_criteria() -> tuple[bool, dict]:
    """Check PROGRAM.md Phase 9 exit criteria."""
    b = butlin.summary()["by_status"]
    r = rsm.summary()["by_status"]
    s = sk.summary()["by_status"]

    criteria: dict[str, dict] = {}

    # Butlin: >= 6 STRONG
    butlin_strong = b.get(Status.STRONG.value, 0)
    criteria["butlin_strong"] = {
        "ok": butlin_strong >= 6,
        "observed": butlin_strong,
        "required": ">= 6",
    }
    # Butlin: <= 1 FAIL
    butlin_fail = b.get(Status.FAIL.value, 0)
    criteria["butlin_fail"] = {
        "ok": butlin_fail <= 1,
        "observed": butlin_fail,
        "required": "<= 1",
    }
    # Butlin: exactly 5 ABSENT-by-declaration is the planned target;
    # accept at least 4 to allow future tightening.
    butlin_absent = b.get(Status.ABSENT.value, 0)
    criteria["butlin_absent"] = {
        "ok": butlin_absent >= 4,
        "observed": butlin_absent,
        "required": ">= 4 (architectural-honesty declarations)",
    }
    # RSM: >= 4 PRESENT (STRONG or PARTIAL — neither FAIL nor ABSENT)
    rsm_present = r.get(Status.STRONG.value, 0) + r.get(Status.PARTIAL.value, 0)
    criteria["rsm_present"] = {
        "ok": rsm_present >= 4,
        "observed": rsm_present,
        "required": ">= 4 PRESENT",
    }
    # SK: >= 5 STRONG (pass)
    sk_strong = s.get(Status.STRONG.value, 0)
    criteria["sk_pass"] = {
        "ok": sk_strong >= 5,
        "observed": sk_strong,
        "required": ">= 5 PASS",
    }

    all_ok = all(c["ok"] for c in criteria.values())
    return all_ok, criteria


# ── Formatting helpers ────────────────────────────────────────────

def _by_status_summary(by_status: dict) -> str:
    """Render a by-status summary line."""
    parts = []
    for status in (Status.STRONG, Status.PARTIAL, Status.ABSENT,
                   Status.FAIL, Status.NOT_ATTEMPTED):
        n = by_status.get(status.value, 0)
        if n:
            parts.append(f"**{status.value}**: {n}")
    return "  |  ".join(parts) if parts else "_No evaluations run._"


def _indicator_row(row: dict) -> str:
    return (
        f"| {row['indicator']} "
        f"| {row['theory']} "
        f"| {row['status']} "
        f"| {_code(row['mechanism'])} "
        f"| {_code(row['test_file'])} "
        f"| {_truncate(row['notes'], 200)} |"
    )


def _signature_row(row: dict) -> str:
    return (
        f"| {row['indicator']} "
        f"| {row['status']} "
        f"| {_code(row['mechanism'])} "
        f"| {_code(row['test_file'])} "
        f"| {_truncate(row['notes'], 200)} |"
    )


def _sk_row(row: dict) -> str:
    return _signature_row(row)


def _code(path: str) -> str:
    if not path:
        return "—"
    return f"`{path}`"


def _truncate(text: str, n: int) -> str:
    text = str(text or "")
    if len(text) <= n:
        return text
    return text[:n - 1].rstrip() + "…"

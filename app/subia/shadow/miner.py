"""Shadow Self orchestrator (Proposal 3 §3.2)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .biases import (
    detect_attentional_bias,
    detect_prediction_bias,
    detect_avoidance,
    detect_affect_action_divergence,
    Finding,
)

logger = logging.getLogger(__name__)


@dataclass
class ShadowAdapters:
    fetch_scene_history: Callable[[int], list]               # days → [{topic: count}]
    fetch_prediction_errors: Callable[[int], list]           # days → [{domain, error}]
    fetch_restoration_queue_log: Callable[[int], list]       # days → [[var_names]]
    fetch_action_log: Callable[[int], list]                  # days → [{variable_addressed}]
    fetch_affect_log: Callable[[int], list]                  # days → [{affect, exploration_followed}]
    fetch_normalize_by: Callable[[], dict]                   # commitment_count_by_topic
    append_to_shadow_wiki: Callable[[str], None]             # markdown → None (DGM append-only)
    add_to_self_state_discovered: Callable[[list], None]     # findings → None


@dataclass
class ShadowReport:
    findings: list = field(default_factory=list)
    generated_at: str = ""
    days_covered: int = 30


class ShadowMiner:
    def __init__(self, adapters: ShadowAdapters) -> None:
        self.adapters = adapters

    def run_analysis(self, days: int = 30) -> ShadowReport:
        try:
            history = self.adapters.fetch_scene_history(days) or []
            errors = self.adapters.fetch_prediction_errors(days) or []
            rq_log = self.adapters.fetch_restoration_queue_log(days) or []
            action_log = self.adapters.fetch_action_log(days) or []
            affect_log = self.adapters.fetch_affect_log(days) or []
            normalize_by = self.adapters.fetch_normalize_by() or {}
        except Exception:
            history = errors = rq_log = action_log = affect_log = []
            normalize_by = {}

        findings: list[Finding] = []
        findings += detect_attentional_bias(history, normalize_by)
        findings += detect_prediction_bias(errors)
        findings += detect_avoidance(rq_log, action_log)
        findings += detect_affect_action_divergence(affect_log, action_log)

        report = ShadowReport(
            findings=findings,
            generated_at=datetime.now(timezone.utc).isoformat(),
            days_covered=days,
        )

        # Side-effects: append-only writes
        if findings:
            md = self._render_markdown(report)
            try:
                self.adapters.append_to_shadow_wiki(md)
            except Exception:
                pass
            try:
                self.adapters.add_to_self_state_discovered(
                    [{"name": f.name, "kind": f.kind, "detail": f.detail,
                      "discovered_at": report.generated_at}
                     for f in findings]
                )
            except Exception:
                pass
        return report

    @staticmethod
    def _render_markdown(report: ShadowReport) -> str:
        lines = [
            f"## Shadow analysis — {report.generated_at}",
            f"_Days covered: {report.days_covered}_",
            "",
        ]
        by_kind: dict[str, list] = {}
        for f in report.findings:
            by_kind.setdefault(f.kind, []).append(f)
        order = [
            ("attentional", "Attentional Biases"),
            ("prediction",  "Prediction Biases"),
            ("avoidance",   "Avoidance Patterns"),
            ("divergence",  "Affect-Action Divergences"),
        ]
        for kind, header in order:
            items = by_kind.get(kind, [])
            if not items:
                continue
            lines.append(f"### {header}")
            for f in items:
                lines.append(f"- **{f.name}** — {f.detail}")
            lines.append("")
        return "\n".join(lines)

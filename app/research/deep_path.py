"""Automatic synchronous deep-research routing and execution.

The legacy research run was reachable only through ``/delegate research`` and
then depended on an off-by-default idle executor.  This module provides a
separate, bounded path for complex research questions that must return an
answer in the current request.
"""

from __future__ import annotations

import logging
import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

_EXPLICIT_DEPTH = re.compile(
    r"\b(?:deep|extensive|comprehensive|thorough|exhaustive)\s+research\b"
    r"|\bultra(?:think|[- ]deep)\b",
    re.IGNORECASE,
)
_REVIEW_SHAPE = re.compile(
    r"\b(?:systematic|scoping|literature)\s+review\b"
    r"|\bstate[- ]of[- ]the[- ]art\b|\bevidence synthesis\b"
    r"|\bresearch report\b",
    re.IGNORECASE,
)
_SOURCE_REQUEST = re.compile(
    r"\b(?:citations?|sources?|references?|doi|papers?|primary sources?)\b",
    re.IGNORECASE,
)
_SYNTHESIS = re.compile(
    r"\b(?:synthesi[sz]e|evaluate|assess|red[- ]team|trade[- ]offs?|"
    r"recommend|triangulate)\b",
    re.IGNORECASE,
)
_BREADTH = re.compile(
    r"\b(?:compare|across|alternatives?|perspectives?|multiple|several)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeepResearchAssessment:
    """Explainable result of the deterministic depth gate."""

    use_deep: bool
    score: int
    threshold: int
    reasons: tuple[str, ...]


def assess_deep_research(
    question: str,
    *,
    difficulty: int = 5,
    threshold: int | None = None,
) -> DeepResearchAssessment:
    """Score research depth without an LLM or fuzzy intent interception."""
    text = (question or "").strip()
    try:
        if threshold is None:
            from app.runtime_settings import get_deep_research_min_score

            threshold = get_deep_research_min_score()
    except Exception:
        threshold = 4
    threshold = int(threshold or 4)

    score = 0
    reasons: list[str] = []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    if _EXPLICIT_DEPTH.search(text):
        add(4, "explicit-depth request")
    if _REVIEW_SHAPE.search(text):
        add(3, "review/evidence-synthesis shape")
    if _SOURCE_REQUEST.search(text):
        add(1, "source-verification requested")
    if _SYNTHESIS.search(text):
        add(1, "analysis/synthesis requested")
    if _BREADTH.search(text):
        add(1, "multi-perspective breadth")
    if len(text.split()) >= 30:
        add(1, "long multi-constraint question")
    if text.count("?") >= 2 or len(re.findall(r"\b(?:and|versus|vs\.?|while)\b", text, re.I)) >= 2:
        add(1, "multiple subquestions")
    if difficulty >= 9:
        add(3, "difficulty>=9")
    elif difficulty >= 8:
        add(2, "difficulty>=8")
    elif difficulty >= 7:
        add(1, "difficulty>=7")

    return DeepResearchAssessment(
        use_deep=score >= threshold,
        score=score,
        threshold=threshold,
        reasons=tuple(reasons),
    )


def promote_research_decisions(
    decisions: Iterable[dict],
    *,
    user_input: str,
) -> list[dict]:
    """Promote qualifying ordinary research decisions to deep research."""
    out = list(decisions)
    try:
        from app.runtime_settings import get_deep_research_auto_enabled

        if not get_deep_research_auto_enabled():
            return out
    except Exception:
        return out

    for decision in out:
        if decision.get("crew") != "research":
            continue
        task = str(decision.get("task") or "")
        # Matrix enrichment already has a purpose-built streaming orchestrator
        # and paid-adapter chain; replacing it with an essay pipeline regresses
        # structured completion.
        if "research_orchestrator" in task or "MATRIX TASK" in task:
            continue
        assessment = assess_deep_research(
            user_input,
            difficulty=int(decision.get("difficulty", 5) or 5),
        )
        decision["deep_research_assessment"] = {
            "score": assessment.score,
            "threshold": assessment.threshold,
            "reasons": list(assessment.reasons),
        }
        if assessment.use_deep:
            decision["crew"] = "deep_research"
            logger.info(
                "deep research auto-promoted (score=%d/%d, reasons=%s)",
                assessment.score,
                assessment.threshold,
                ", ".join(assessment.reasons),
            )
    return out


def _parse_query_plan(raw: str) -> list[str]:
    """Parse a bounded JSON-or-lines search plan from a planner completion."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    candidates: list[object] = []
    try:
        decoded = json.loads(text)
        if isinstance(decoded, dict):
            decoded = decoded.get("queries", [])
        if isinstance(decoded, list):
            candidates = decoded
    except (TypeError, ValueError, json.JSONDecodeError):
        candidates = [
            re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line)
            for line in text.splitlines()
        ]

    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        query = str(candidate or "").strip().strip('"')
        key = re.sub(r"\s+", " ", query).casefold()
        if len(query) < 8 or key in seen:
            continue
        seen.add(key)
        out.append(query[:500])
        if len(out) >= 3:
            break
    return out


def _plan_search_queries(question: str) -> list[str]:
    """Use a focused, lifecycle-instrumented model to decompose the question."""
    from app.research.run import _focused_completion

    raw = _focused_completion(
        "Return ONLY a JSON array of 2-3 independent web/literature search "
        "queries that jointly answer the research question. Cover competing "
        "views, primary evidence, and current facts where relevant. Do not "
        f"answer the question itself.\n\nResearch question: {question}",
        role="research",
        task_hint="deep research query decomposition",
        max_tokens=700,
    )
    return _parse_query_plan(raw)


def collect_deep_evidence(
    question: str,
    *,
    planner_fn: Callable[[str], list[str]] | None = None,
    search_fn: Callable[[str], list] | None = None,
) -> list:
    """Decompose, search several source classes, fetch pages, and de-duplicate.

    The original question is always searched, even if decomposition fails. At
    most two generated subqueries are added, keeping network and token costs
    bounded while avoiding single-query tunnel vision.
    """
    planner = planner_fn or _plan_search_queries
    try:
        planned = list(planner(question) or [])
    except Exception:
        logger.debug("deep research query planning failed", exc_info=True)
        planned = []

    queries = [question]
    normalized = {re.sub(r"\s+", " ", question).casefold()}
    for query in planned:
        key = re.sub(r"\s+", " ", str(query)).casefold()
        if key and key not in normalized:
            normalized.add(key)
            queries.append(str(query))
        if len(queries) >= 3:
            break

    if search_fn is None:
        from app.research.literature import search_deep_sources

        def search_fn(query: str) -> list:
            return search_deep_sources(
                query, kb_n=3, arxiv_n=3, web_n=4, fetch_n=2,
            )

    merged: list = []
    seen: set[str] = set()
    for query in queries:
        try:
            hits = search_fn(query) or []
        except Exception:
            logger.debug("deep evidence search failed for %r", query, exc_info=True)
            continue
        for hit in hits:
            identifier = str(
                getattr(hit, "id", "")
                or getattr(hit, "title", "")
                or hit
            ).strip()
            key = identifier.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(hit)
            if len(merged) >= 18:
                return merged
    return merged


def _deep_evidence_gate_for(run) -> Callable[..., tuple[str | None, str]]:
    """Build the mandatory provenance gate for one synchronous deep run.

    The legacy research-evidence evaluator is globally soak-gated and defaults
    off. Deep research cannot claim to be evidence-backed while depending on
    that operator setting, so this narrower gate always enforces two facts:
    evidence was actually retrieved, and the final synthesis cites at least
    one identifier from that exact evidence set. The existing claim detector
    adds an uncited-empirical-claim check.
    """
    def gate(*, proposal_text: str, task_id: str, verdict=None):
        from app.epistemic.gate_research_evidence import _detect_evidence_gap
        from app.research.run import HINT_LITERATURE, _decode_list

        text = str(proposal_text or "")
        rows = _decode_list(run, HINT_LITERATURE)
        if not rows:
            return "verify", "deep research retrieved no evidence sources"

        identifiers: list[str] = []
        for row in rows:
            metadata = row.get("metadata") if isinstance(row, dict) else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            identifier = str(
                metadata.get("url")
                or (row.get("id") if isinstance(row, dict) else "")
                or metadata.get("doi")
                or ""
            ).strip()
            if identifier:
                identifiers.append(identifier)

        cited = [identifier for identifier in identifiers if identifier in text]
        if not cited:
            return (
                "verify",
                "final synthesis cites no identifier retrieved by this run",
            )

        has_gap, samples = _detect_evidence_gap(text)
        if has_gap:
            detail = "; ".join(samples) or "empirical claim"
            return "verify", f"uncited empirical claim(s): {detail}"

        return (
            None,
            f"deep evidence gate clear ({len(rows)} sources; "
            f"{len(cited)} traced citation(s))",
        )

    return gate


def execute_deep_research(
    question: str,
    *,
    parent_task_id: str | None = None,
) -> str:
    """Run the evidence→draft→panel-critique→gate chain synchronously."""
    from app.autonomous_executor.models import Budget, ExecutorStatus
    from app.research.run import (
        HINT_CRITIQUE,
        HINT_DRAFT,
        HINT_INVESTIGATE,
        _text_for,
        build_research_run,
        make_research_adapter,
        run_to_completion,
        summarise_run,
    )
    from app.runtime_settings import (
        get_deep_research_fusion_enabled,
        get_deep_research_max_panel,
        get_deep_research_wall_clock_s,
        get_executor_default_budget_tokens,
        get_executor_default_budget_usd,
    )

    run = build_research_run(
        question,
        requestor=f"request:{parent_task_id or 'interactive'}",
        zone="autonomous",
        budget=Budget(
            cap_usd=get_executor_default_budget_usd(),
            cap_tokens=get_executor_default_budget_tokens(),
            cap_wall_clock_s=get_deep_research_wall_clock_s(),
        ),
        verify=True,
        critique=True,
    )
    try:
        from app.epistemic.verification_extension import register_zone_for_task

        register_zone_for_task(run.run_id, "autonomous")
    except Exception:
        logger.debug("deep research zone registration failed", exc_info=True)
    adapter = make_research_adapter(
        search_fn=collect_deep_evidence,
        gate_fn=_deep_evidence_gate_for(run),
    )

    def progress(current) -> None:
        try:
            from app.observability.task_progress import record_output_progress

            completed = sum(1 for step in current.plan if step.status.value == "completed")
            record_output_progress(
                note=f"deep research progress: {completed}/{len(current.plan)} steps",
            )
        except Exception:
            logger.debug("deep research progress marker failed", exc_info=True)

    fusion_scope = nullcontext()
    if get_deep_research_fusion_enabled():
        try:
            from app.fusion.config import force_for_roles

            # Only the final evidence critic becomes a panel. Collection and
            # drafting remain single-model, controlling cost and correlation.
            fusion_scope = force_for_roles(
                {"vetting"}, max_panel=get_deep_research_max_panel(),
            )
        except Exception:
            logger.debug("deep research Fusion scope unavailable", exc_info=True)

    with fusion_scope:
        run_to_completion(
            run,
            adapter=adapter,
            max_iterations=max(12, len(run.plan) + 2),
            on_advance=progress,
        )

    try:
        from app.autonomous_executor import store

        store.save(run)
    except Exception:
        logger.debug("deep research audit persistence failed", exc_info=True)

    outcome = summarise_run(run)
    final_text = (
        _text_for(run, HINT_CRITIQUE)
        or _text_for(run, HINT_DRAFT)
        or _text_for(run, HINT_INVESTIGATE)
    ).strip()

    if run.status is ExecutorStatus.COMPLETED and final_text:
        return final_text
    if run.status is ExecutorStatus.BLOCKED:
        return (
            "I completed the research passes, but the evidence gate did not "
            "clear the draft, so I won't present unsupported claims as a "
            f"finished answer. Gate detail: {outcome.gate_note or run.blocked_reason}"
        )
    if final_text:
        return (
            "[Unverified partial research — the full run did not complete.]\n\n"
            + final_text
            + f"\n\nRun status: {run.status.value}."
        )
    return (
        "Deep research could not produce a substantive draft. "
        f"Run status: {run.status.value}; reason: "
        f"{run.failure_reason or run.abort_reason or 'no usable evidence returned'}."
    )


__all__ = [
    "DeepResearchAssessment",
    "assess_deep_research",
    "promote_research_decisions",
    "collect_deep_evidence",
    "execute_deep_research",
]

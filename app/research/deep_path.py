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
# 2026-07-24: "make me a report on X" / "report on X ... over the years"
# scored ZERO points before this — _REVIEW_SHAPE only matched the literal
# phrase "research report". A plain "please make me a report on Estonia
# forest health ... over the years" is one of the clearest possible
# signals of wanting a long-form, evidence-backed product, yet it fell
# through to the plain ``research`` crew (see
# reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md).
_REPORT_SHAPE = re.compile(
    r"\b(?:make|write|draft|prepare|compile|produce|create)\s+(?:me\s+)?"
    r"(?:an?\s+)?report\b"
    r"|\breport\s+on\b"
    r"|\bover\s+the\s+years\b",
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
_MIN_EVIDENCE_CHARS = 120
_CITED_URL_RE = re.compile(r"https?://[^\s<>()\]]+", re.IGNORECASE)
_CITED_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
_CITED_ARXIV_RE = re.compile(
    r"\b(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?\b",
    re.IGNORECASE,
)


def _usable_deep_evidence(hit) -> bool:
    """Return whether a hit is strong enough to enter a deep-research run.

    Search-result snippets are discovery metadata, not evidence.  Web rows must
    therefore have fetched page content.  Academic/KB rows need a substantive
    excerpt and stable identifier.  This is deterministic and intentionally
    conservative: an empty evidence set blocks the run instead of inviting the
    drafting model to fill gaps from memory.
    """
    source = str(getattr(hit, "source", "") or "").strip().lower()
    identifier = str(getattr(hit, "id", "") or "").strip()
    text = str(getattr(hit, "text", "") or "").strip()
    metadata = getattr(hit, "metadata", {}) or {}
    if not identifier or len(text) < _MIN_EVIDENCE_CHARS:
        return False
    if source == "web":
        return bool(
            identifier.startswith("https://")
            and metadata.get("content_fetched") is True
        )
    if source == "kb":
        score = getattr(hit, "score", None)
        if score is None:
            return True
        try:
            return float(score) >= 0.35
        except (TypeError, ValueError):
            return False
    return source == "arxiv"


def _cited_identifiers(text: str) -> set[str]:
    """Machine-checkable identifiers asserted by a final synthesis."""
    out = {
        match.group(0).rstrip(".,;:")
        for match in _CITED_URL_RE.finditer(text or "")
    }
    out.update(
        match.group(0).rstrip(".,;:)]}")
        for match in _CITED_DOI_RE.finditer(text or "")
    )
    out.update(match.group(1) for match in _CITED_ARXIV_RE.finditer(text or ""))
    return {item for item in out if item}


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
    if _REPORT_SHAPE.search(text):
        add(2, "explicit report request")
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


def drop_writing_after_deep_research(decisions: list[dict]) -> list[dict]:
    """Drop a co-dispatched ``writing`` decision when ``deep_research`` is present.

    ``execute_deep_research`` already runs a draft→critique chain and
    returns the composed write-up itself (see ``HINT_DRAFT``/
    ``HINT_CRITIQUE`` in ``app/research/run.py``). Dispatching a separate
    ``writing`` crew alongside it in parallel is pure redundancy: the
    writer never sees the research findings (each parallel crew gets only
    its own router-authored task), so it either duplicates the report
    from model weights or gets discarded — and the extra crew is exactly
    what pushes a report-class request onto the timeout-capped multi-crew
    dispatch path instead of the uncapped single-crew path (see
    reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md). Any other co-dispatched
    crew (e.g. a genuinely independent "coding" decision) is left alone.
    """
    if not any(d.get("crew") == "deep_research" for d in decisions):
        return decisions
    kept = [d for d in decisions if d.get("crew") != "writing"]
    if len(kept) != len(decisions):
        logger.info(
            "dropped redundant 'writing' decision — deep_research already "
            "drafts and critiques its own write-up",
        )
    return kept


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

    search = search_fn
    if search is None:
        from app.research.literature import search_deep_sources

        def _default_search(query: str) -> list:
            return search_deep_sources(
                query, kb_n=3, arxiv_n=3, web_n=4, fetch_n=2,
            )
        search = _default_search

    merged: list = []
    seen: set[str] = set()
    for query in queries:
        try:
            hits = search(query) or []
        except Exception:
            logger.debug("deep evidence search failed for %r", query, exc_info=True)
            continue
        for hit in hits:
            if not _usable_deep_evidence(hit):
                logger.warning(
                    "deep research rejected non-evidentiary hit for %r: %s",
                    query[:80],
                    str(getattr(hit, "id", "") or getattr(hit, "title", ""))[:160],
                )
                continue
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
        usable_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metadata_value = row.get("metadata")
            metadata: dict = metadata_value if isinstance(metadata_value, dict) else {}
            source = str(row.get("source") or "").lower()
            identifier = str(metadata.get("url") or row.get("id") or "").strip()
            excerpt = str(row.get("text") or "").strip()
            if (
                source not in {"web", "kb", "arxiv"}
                or not identifier
                or len(excerpt) < _MIN_EVIDENCE_CHARS
            ):
                continue
            if source == "web" and not (
                identifier.startswith("https://")
                and metadata.get("content_fetched") is True
            ):
                continue
            score_value = row.get("score")
            if source == "kb" and score_value is not None:
                try:
                    if float(score_value) < 0.35:
                        continue
                except (TypeError, ValueError):
                    continue
            usable_rows.append(row)
        if not usable_rows:
            return "verify", "deep research retrieved no evidence sources"

        identifiers: list[str] = []
        for row in usable_rows:
            metadata_value = row.get("metadata")
            metadata: dict = (
                metadata_value if isinstance(metadata_value, dict) else {}
            )
            identifier = str(
                metadata.get("url")
                or row.get("id")
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

        # A single valid source must not launder additional invented URLs,
        # DOIs, or arXiv ids. Every machine-checkable citation in the answer
        # must resolve to an identifier in this run's evidence set.
        asserted = _cited_identifiers(text)
        untraced = sorted(
            token for token in asserted
            if not any(token == identifier or token in identifier for identifier in identifiers)
        )
        if untraced:
            return (
                "verify",
                "final synthesis contains citation(s) not retrieved by this run: "
                + ", ".join(untraced[:3]),
            )

        # Check empirical blocks independently. A URL in the references section
        # must not make an unrelated numeric paragraph look grounded.
        gap_samples: list[str] = []
        blocks = [
            block.strip()
            for block in re.split(
                r"\n\s*\n|^\s*[-*]\s+", text, flags=re.MULTILINE,
            )
            if block.strip()
        ]
        for block in blocks:
            traces_identifier = any(
                identifier in block for identifier in identifiers
            )
            source_labels = {
                int(match.group(1))
                for match in re.finditer(r"\[S(\d+)\]", block, re.IGNORECASE)
            }
            traces_source_label = any(
                1 <= source_number <= len(usable_rows)
                for source_number in source_labels
            )
            if traces_identifier or traces_source_label:
                continue

            # The general-purpose detector accepts weak citation-shaped text
            # such as ``Source: unknown`` or ``according to``.  Deep research
            # has the retrieved source set available, so remove those generic
            # markers and detect the underlying empirical claim instead.  A
            # claim clears this stricter gate only when its own block contains
            # an exact retrieved identifier or a valid [S<n>] evidence label.
            untraced_claim = re.sub(
                r"https?://[^\s<>()\]]+|\b10\.\d{4,9}/\S+|"
                r"\barxiv\s*:?\s*\d{4}\.\d{4,5}(?:v\d+)?\b|"
                r"\[S?\d+\]|\baccording to\b|\bsource\s*:|"
                r"\bet al\.?|\([A-Z][A-Za-z]+,?\s+\d{4}\)",
                " ",
                block,
                flags=re.IGNORECASE,
            )
            has_gap, samples = _detect_evidence_gap(untraced_claim)
            if has_gap:
                gap_samples.extend(samples or [block[:80]])
        if gap_samples:
            detail = "; ".join(gap_samples[:3]) or "empirical claim"
            return "verify", f"uncited empirical claim(s): {detail}"

        return (
            None,
            f"deep evidence gate clear ({len(usable_rows)} sources; "
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
        # Automatic deep research is never allowed to inherit the legacy
        # default-OFF verification switch.  It advertises a verified answer,
        # so citation/identifier verification is part of the contract.
        citation_verification_enabled_fn=lambda: True,
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

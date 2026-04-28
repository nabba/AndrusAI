"""Nightly compile pipeline for the Transfer Insight Layer.

Public entry point: ``run_compile()`` — registered as a HEAVY idle-
scheduler job by ``app.idle_scheduler._default_jobs()``.

Workflow:
  1. Cadence guard: skip unless ≥24h since last successful run.
  2. Drain the event queue and the retry queue.
  3. Bound to ``_MAX_TOTAL_PER_RUN`` events for cost cap; overflow stays
     queued for the next idle slot.
  4. Pin ``llm_mode="free"`` for the duration of the batch.
  5. Compile each event in parallel via a small ThreadPoolExecutor.
     Per event:
       a. Build prompt from the source-specific template.
       b. Invoke the Learner LLM (free-tier cascade).
       c. Run sanitiser → verdict (hard_rejected → drop with audit log).
       d. Run scorer → abstraction_score, concrete/abstract hits.
       e. Construct a SkillDraft via the shared helper.
       f. Append the draft record to ``shadow_drafts.jsonl``.
  6. Failed events go to the retry queue with attempts++.
  7. Restore llm_mode and write last_compile_at on the way out.

Phase 17a deliberately does NOT call ``integrator.integrate()`` — drafts
land in the shadow log for operator review and effectiveness measurement
before promotion to live KBs in Phase 17c.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from app.transfer_memory import queue as _queue
from app.transfer_memory.llm_scope import force_llm_mode
from app.transfer_memory.prompts import build_prompt
from app.transfer_memory.sanitizer import check as sanitize_check
from app.transfer_memory.scorer import score_abstraction
from app.transfer_memory.types import (
    TransferEvent, TransferKind, TransferScope, domain_for_kind,
)

logger = logging.getLogger(__name__)


# Cadence guard — never run more than once per 24h regardless of how
# often idle_scheduler invokes the job.
_MIN_INTERVAL_SECONDS = 24 * 60 * 60

# Cost / parallelism caps — gentle on free-tier rate limits.
_MAX_TOTAL_PER_RUN = 50
_MAX_CONCURRENT = 2

# Sanity bound on Learner output.
_MIN_CONTENT_LEN = 80
_LEARNER_MAX_TOKENS = 600
_LEARNER_TIMEOUT_SECONDS = 60

# Title extraction (the prompt asks for "# [<kind>] <title>" as line 1).
_TITLE_RE = re.compile(r"^\s*#\s+(.+)$", re.MULTILINE)


@dataclass
class _CompileOutcome:
    event: TransferEvent
    draft: Any | None = None                  # SkillDraft | None
    sanitizer_findings: list = field(default_factory=list)
    abstraction_score: float = 0.0
    concrete_hits: int = 0
    abstract_hits: int = 0
    leakage_risk: float = 0.0
    sanitizer_max_scope: str = ""
    error: str = ""


def run_compile() -> dict:
    """Idle-scheduler entry point. Returns a summary dict.

    Idempotent — multiple invocations within ``_MIN_INTERVAL_SECONDS`` no-op
    (cadence guard). Cooperative — checks ``idle_scheduler.should_yield()``
    between events. Bounded — at most ``_MAX_TOTAL_PER_RUN`` events per run.
    """
    summary: dict = {
        "ran": False, "compiled": 0, "rejected": 0, "errors": 0,
        "skipped_cadence": False, "queue_depth": 0,
    }

    now = time.time()
    last = _queue.read_last_compile_at()
    if last and (now - last) < _MIN_INTERVAL_SECONDS:
        summary["skipped_cadence"] = True
        logger.debug(
            "transfer_memory.compiler: cadence guard — %.1fh since last run",
            (now - last) / 3600,
        )
        return summary

    events = _queue.drain()
    events.extend(_queue.drain_retries())
    summary["queue_depth"] = len(events)

    if not events:
        # Record the timestamp anyway so cadence guard doesn't loop on
        # empty queues during bursty idle activity.
        _queue.write_last_compile_at(now)
        return summary

    if len(events) > _MAX_TOTAL_PER_RUN:
        overflow = events[_MAX_TOTAL_PER_RUN:]
        events = events[:_MAX_TOTAL_PER_RUN]
        _queue.push_retry(overflow)
        logger.info(
            "transfer_memory.compiler: bounded to %d events (%d overflowed)",
            len(events), len(overflow),
        )

    summary["ran"] = True
    failed: list[TransferEvent] = []

    with force_llm_mode("free"):
        with ThreadPoolExecutor(
            max_workers=_MAX_CONCURRENT,
            thread_name_prefix="xfer_compile",
        ) as pool:
            futures = {pool.submit(_compile_one, evt): evt for evt in events}
            for fut in as_completed(futures, timeout=540):
                evt = futures[fut]
                try:
                    outcome = fut.result(timeout=_LEARNER_TIMEOUT_SECONDS)
                except Exception as exc:
                    logger.debug("compile_one raised", exc_info=True)
                    outcome = _CompileOutcome(
                        event=evt, error=f"future_failed: {str(exc)[:160]}",
                    )

                _record_outcome(outcome, summary)
                if outcome.draft is None and outcome.error:
                    failed.append(evt)

                if _should_yield_safe():
                    logger.info(
                        "transfer_memory.compiler: yielding to user task "
                        "(compiled=%d so far)",
                        summary["compiled"],
                    )
                    break

    if failed:
        _queue.push_retry(failed)

    _queue.write_last_compile_at(time.time())

    logger.info(
        "transfer_memory.compiler: ran (compiled=%d rejected=%d errors=%d depth=%d)",
        summary["compiled"], summary["rejected"], summary["errors"],
        summary["queue_depth"],
    )
    return summary


def _compile_one(event: TransferEvent) -> _CompileOutcome:
    """Compile a single event into a SkillDraft (or rejection)."""
    try:
        prompt = build_prompt(event)
    except Exception as exc:
        return _CompileOutcome(
            event=event, error=f"prompt_build_failed: {str(exc)[:120]}",
        )

    try:
        # Deferred import keeps the module side-effect-free at import time.
        from app.llm_factory import create_specialist_llm
        llm = create_specialist_llm(max_tokens=_LEARNER_MAX_TOKENS, role="learner")
        content = str(llm.call(prompt)).strip()
    except Exception as exc:
        return _CompileOutcome(
            event=event, error=f"llm_call_failed: {str(exc)[:120]}",
        )

    if len(content) < _MIN_CONTENT_LEN:
        return _CompileOutcome(
            event=event,
            sanitizer_findings=[("too_short", str(len(content)))],
        )

    verdict = sanitize_check(content)
    score = score_abstraction(content)

    if verdict.hard_rejected:
        return _CompileOutcome(
            event=event,
            sanitizer_findings=verdict.findings,
            abstraction_score=score.score,
            concrete_hits=score.concrete_hits,
            abstract_hits=score.abstract_hits,
            leakage_risk=verdict.leakage_risk,
            sanitizer_max_scope=verdict.allowed_scope.value,
        )

    # Phase 17a: every transfer-memory draft starts at SHADOW regardless
    # of what the sanitiser allows. Promotion to live retrieval happens
    # in 17c after operator review and effectiveness measurement.
    transfer_scope = TransferScope.SHADOW.value
    domain = domain_for_kind(event.kind)
    topic = _extract_topic(content, event)

    try:
        # Deferred import — types module is light, but avoids any circular
        # import surprises if construct_skill_draft grows new deps.
        from app.self_improvement.types import construct_skill_draft
        draft = construct_skill_draft(
            topic=topic,
            rationale=(
                f"Transfer-memory insight from {event.kind.value} event "
                f"{event.source_id} (sanitiser_max="
                f"{verdict.allowed_scope.value}, abstraction={score.score:.2f})."
            ),
            content_markdown=content,
            proposed_kb="",                     # let classify_kb decide on promotion
            id_prefix="xfer",
            source_kind=event.kind.value,
            source_domain=domain,
            transfer_scope=transfer_scope,
            project_origin=event.project_origin,
            abstraction_score=score.score,
            leakage_risk=verdict.leakage_risk,
            evidence_refs=f"{event.kind.value}:{event.source_id}",
        )
    except Exception as exc:
        return _CompileOutcome(
            event=event,
            sanitizer_findings=verdict.findings,
            abstraction_score=score.score,
            concrete_hits=score.concrete_hits,
            abstract_hits=score.abstract_hits,
            leakage_risk=verdict.leakage_risk,
            sanitizer_max_scope=verdict.allowed_scope.value,
            error=f"draft_construction_failed: {str(exc)[:120]}",
        )

    return _CompileOutcome(
        event=event, draft=draft,
        sanitizer_findings=verdict.findings,
        abstraction_score=score.score,
        concrete_hits=score.concrete_hits,
        abstract_hits=score.abstract_hits,
        leakage_risk=verdict.leakage_risk,
        sanitizer_max_scope=verdict.allowed_scope.value,
    )


def _record_outcome(outcome: _CompileOutcome, summary: dict) -> None:
    """Append the outcome to the shadow log, persist to KBs, update summary.

    Phase 17b: every successful draft is also written to the KB system
    via ``integrator.integrate(draft, initial_status="shadow")``. The
    shadow status keeps the record invisible to the existing retrieval
    path (which filters ``status="active"``) — Phase 17c promotion is
    what flips the status to active.

    The shadow log (``shadow_drafts.jsonl``) remains the operator's
    audit surface; it captures rejections + errors that integrator
    never sees, and gives a faster path for review than reading Chroma.
    """
    if outcome.draft is not None:
        _queue.append_shadow_draft(_serialise_outcome(outcome))
        summary["compiled"] += 1
        _persist_to_kb_safe(outcome, summary)
    elif outcome.error:
        summary["errors"] += 1
        # Errors get an audit row too — useful for diagnosing free-tier
        # outages or repeated prompt-build failures.
        _queue.append_shadow_draft(_serialise_outcome(outcome))
    else:
        summary["rejected"] += 1
        _queue.append_shadow_draft(_serialise_outcome(outcome))


def _persist_to_kb_safe(outcome: _CompileOutcome, summary: dict) -> None:
    """Best-effort KB persistence with status=shadow.

    Failures are swallowed — the shadow_drafts.jsonl audit row already
    exists, so an integrator outage cannot lose work. The summary
    counter ``persisted`` (default 0) tracks successful KB writes.
    """
    summary.setdefault("persisted", 0)
    summary.setdefault("persist_errors", 0)
    try:
        from app.self_improvement.integrator import integrate
        record = integrate(outcome.draft, initial_status="shadow")
        if record is not None:
            summary["persisted"] += 1
        else:
            # Novelty-COVERED rejection or KB write failure — count as
            # persist error so the dashboard can flag novelty saturation.
            summary["persist_errors"] += 1
    except Exception:
        logger.debug("transfer_memory.compiler: KB persist failed", exc_info=True)
        summary["persist_errors"] += 1


def _extract_topic(content: str, event: TransferEvent) -> str:
    """Pull a topic from the H1 title; fall back to a kind-derived label."""
    m = _TITLE_RE.search(content)
    if m:
        return m.group(1).strip()[:200]
    return f"transfer-{event.kind.value}: {(event.summary or event.source_id)[:160]}"[:200]


def _serialise_outcome(outcome: _CompileOutcome) -> dict:
    """Build the JSONL row for the shadow log.

    Includes both the event reference (for traceability) and any
    constructed SkillDraft. Rejections / errors carry sanitiser findings
    so the operator can diagnose without re-running.
    """
    row: dict = {
        "event_id": outcome.event.event_id,
        "kind": outcome.event.kind.value,
        "source_id": outcome.event.source_id,
        "project_origin": outcome.event.project_origin,
        "captured_at": outcome.event.captured_at,
        "compiled_at": time.time(),
        "abstraction_score": outcome.abstraction_score,
        "abstract_hits": outcome.abstract_hits,
        "concrete_hits": outcome.concrete_hits,
        "leakage_risk": outcome.leakage_risk,
        "sanitizer_max_scope": outcome.sanitizer_max_scope,
        "sanitizer_findings": outcome.sanitizer_findings,
        "error": outcome.error,
    }
    d = outcome.draft
    if d is not None:
        row["draft"] = {
            "id": d.id,
            "topic": d.topic,
            "rationale": d.rationale,
            "content_markdown": d.content_markdown,
            "proposed_kb": d.proposed_kb,
            "novelty_at_creation": d.novelty_at_creation,
            "source_kind": d.source_kind,
            "source_domain": d.source_domain,
            "transfer_scope": d.transfer_scope,
            "project_origin": d.project_origin,
            "abstraction_score": d.abstraction_score,
            "leakage_risk": d.leakage_risk,
            "evidence_refs": d.evidence_refs,
        }
    return row


def _should_yield_safe() -> bool:
    """Cooperative-yield check that swallows import errors.

    The compiler is registered as an idle-scheduler job; the import
    should always succeed in production. The defensive wrap lets the
    module also be invoked from tests / one-shots where the scheduler
    isn't running.
    """
    try:
        from app.idle_scheduler import should_yield
        return bool(should_yield())
    except Exception:
        return False

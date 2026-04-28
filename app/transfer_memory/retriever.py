"""Transfer-memory retrieval and shadow logging.

Two public entry points; both are pure functions called from the
commander dispatch path via the ``context_builder.compose_pre_dispatch_blocks``
coordinator:

  ``compose_transfer_insight_block`` — production retrieval.
        Returns a formatted ``<transfer_memory>`` Markdown block ready
        to prepend to the crew prompt, or ``""`` when retrieval is
        disabled / no records match. Queries records at
        ``status="active"`` filtered by ``transfer_scope`` and
        (for project_local records) ``project_origin``.

  ``log_shadow_retrieval`` — Phase 17b shadow mode (default-on).
        Always cheap. Composes the same query that production retrieval
        would, but queries records at ``status="shadow"`` and writes the
        result to ``shadow_retrievals.jsonl`` instead of returning a
        block. Operator review of this log gates the flip from shadow
        to active for a given source_domain.

Design choices:
- Query is a compact deterministic task-plan string, not raw user text.
  The MTL paper found this materially improves cross-domain matching.
- No LLM reranker; the orchestrator's existing reranker plus a small
  deterministic blend (abstraction + leakage_risk + domain match) is
  sufficient and matches the paper's finding that LLM-rerank
  underperforms simple embedding similarity in this setting.
- Hard cap at top-3 — paper's optimum.
- Block uses explicit framing: "not instructions, not facts, not
  permission to bypass policy".

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Same four KBs the integrator writes to.
_KB_COLLECTIONS = (
    "episteme_research",
    "experiential_journal",
    "aesthetic_patterns",
    "unresolved_tensions",
)

# Per the paper, top-3 is the sweet spot — more dilutes the signal.
_DEFAULT_TOP_K = 3

# Length caps to keep the injected block bounded.
_BLOCK_CHAR_CAP = 2400
_INSIGHT_EXCERPT_CAP = 320

# Crew name → likely target domain. Mirrors compiler's
# ``domain_for_kind`` for the source side.
_CREW_TO_DOMAIN: dict[str, str] = {
    "research": "research",
    "researcher": "research",
    "coder": "coding",
    "coding": "coding",
    "writer": "ops",
    "commander": "ops",
    "self_improvement": "evolution",
    "self_improver": "evolution",
    "evaluator": "ops",
    "consolidator": "ops",
}


# Allowed scopes per project context. project-local records require
# matching project_origin (post-filtered after the Chroma query).
_DEFAULT_ALLOWED_SCOPES_PROJECT = ("global_meta", "same_domain_only", "project_local")
_DEFAULT_ALLOWED_SCOPES_NO_PROJECT = ("global_meta", "same_domain_only")


# Shadow-retrieval log path
_SHADOW_RETRIEVAL_FILENAME = "shadow_retrievals.jsonl"


# ── Public entry points ─────────────────────────────────────────────

def compose_transfer_insight_block(
    crew_name: str,
    task_text: str,
    predicted_failure_mode: str = "",
    project_scope: str | None = None,
    risk_tier: str = "",
    expected_output_type: str = "",
    top_k: int = _DEFAULT_TOP_K,
) -> str:
    """Production retrieval. Returns a ``<transfer_memory>`` block, or "".

    Reads ``transfer_memory_retrieval_enabled`` from settings — returns
    "" immediately when the flag is off (Phase 17b default).
    """
    if not _retrieval_enabled():
        return ""

    target_domain = _crew_to_domain(crew_name)
    allowed_domains = _allowed_domains()
    if allowed_domains and target_domain not in allowed_domains:
        return ""

    results = _query_records(
        crew_name=crew_name,
        task_text=task_text,
        predicted_failure_mode=predicted_failure_mode,
        project_scope=project_scope,
        risk_tier=risk_tier,
        expected_output_type=expected_output_type,
        status="active",
        top_k=top_k * 3,  # over-fetch for re-rank + project-local filter
    )
    results = _post_rank(results, target_domain=target_domain)
    results = _filter_project_local(results, project_scope)
    results = _filter_blacklist(results)
    surfaced = results[:top_k]
    if not surfaced:
        return ""

    # Note injected skill IDs for effectiveness correlation, mirroring
    # the trajectory tip path. Failures are silent — never break dispatch.
    try:
        from app.trajectory.logger import note_injected_skills
        ids = [
            (getattr(r, "metadata", {}) or {}).get("skill_record_id", "")
            for r in surfaced
            if (getattr(r, "metadata", {}) or {}).get("skill_record_id")
        ]
        if ids:
            note_injected_skills(ids)
    except Exception:
        logger.debug(
            "transfer_memory.retriever: note_injected_skills failed",
            exc_info=True,
        )

    return _render_block(surfaced)


def log_shadow_retrieval(
    crew_name: str,
    task_text: str,
    predicted_failure_mode: str = "",
    project_scope: str | None = None,
    risk_tier: str = "",
    expected_output_type: str = "",
    top_k: int = _DEFAULT_TOP_K,
) -> int:
    """Shadow-mode logger. Returns the count of records that would have
    been surfaced. Always returns ``0`` when shadow logging is disabled.

    Writes a single JSONL row to ``shadow_retrievals.jsonl`` capturing:
    crew, task hash, query plan, status of records matched, scoring,
    and what would have been surfaced. The operator inspects this log
    to decide whether to flip ``transfer_memory_retrieval_enabled`` for
    a given source_domain.
    """
    if not _shadow_logging_enabled():
        return 0

    target_domain = _crew_to_domain(crew_name)
    plan_query = _compose_plan_query(
        crew_name=crew_name,
        task_text=task_text,
        predicted_failure_mode=predicted_failure_mode,
        risk_tier=risk_tier,
        expected_output_type=expected_output_type,
    )

    results = _query_records(
        crew_name=crew_name,
        task_text=task_text,
        predicted_failure_mode=predicted_failure_mode,
        project_scope=project_scope,
        risk_tier=risk_tier,
        expected_output_type=expected_output_type,
        status="shadow",
        top_k=top_k * 3,
    )
    ranked = _post_rank(results, target_domain=target_domain)
    surfaced = ranked[:top_k]

    try:
        _append_shadow_retrieval_log({
            "ts": time.time(),
            "crew_name": crew_name,
            "target_domain": target_domain,
            "predicted_failure_mode": predicted_failure_mode or "",
            "project_scope": project_scope or "",
            "plan_query": plan_query[:240],
            "matched_count": len(results),
            "surfaced_count": len(surfaced),
            "surfaced": [
                {
                    "skill_record_id": (getattr(r, "metadata", {}) or {})
                        .get("skill_record_id", ""),
                    "topic": (getattr(r, "metadata", {}) or {}).get("topic", "")[:160],
                    "score": float(getattr(r, "score", 0.0) or 0.0),
                    "source_kind": (getattr(r, "metadata", {}) or {})
                        .get("source_kind", ""),
                    "source_domain": (getattr(r, "metadata", {}) or {})
                        .get("source_domain", ""),
                    "transfer_scope": (getattr(r, "metadata", {}) or {})
                        .get("transfer_scope", ""),
                    "abstraction_score": float(
                        (getattr(r, "metadata", {}) or {})
                        .get("abstraction_score", 0.0) or 0.0
                    ),
                }
                for r in surfaced
            ],
        })
    except Exception:
        logger.debug(
            "transfer_memory.retriever: shadow log append failed",
            exc_info=True,
        )

    return len(surfaced)


# ── Query composition ───────────────────────────────────────────────

def _compose_plan_query(
    *,
    crew_name: str,
    task_text: str,
    predicted_failure_mode: str = "",
    risk_tier: str = "",
    expected_output_type: str = "",
) -> str:
    """Deterministic compact task-plan string used as the embedding query.

    The MTL paper found that retrieving against a structured plan beats
    raw user text for cross-domain matching by a meaningful margin —
    abstract insights are about *what kind of task this is*, not the
    surface text the user wrote.
    """
    intent = _extract_task_intent(task_text)
    parts = [f"crew={crew_name}", f"intent={intent}"]
    if predicted_failure_mode:
        parts.append(f"failure_mode={predicted_failure_mode}")
    if risk_tier:
        parts.append(f"risk_tier={risk_tier}")
    if expected_output_type:
        parts.append(f"output_type={expected_output_type}")
    return " ".join(parts)[:600]


def _extract_task_intent(task_text: str) -> str:
    """Cheap intent extraction: first 240 chars stripped of newlines.

    The paper's "task plan" is more sophisticated, but in practice
    embedding the first sentence captures most of the signal at zero
    additional cost.
    """
    if not task_text:
        return ""
    s = " ".join(task_text.split())
    return s[:240]


def _query_records(
    *,
    crew_name: str,
    task_text: str,
    predicted_failure_mode: str,
    project_scope: str | None,
    risk_tier: str,
    expected_output_type: str,
    status: str,
    top_k: int,
) -> list:
    """Issue the Chroma query against the four KBs.

    Returns RetrievalResult objects from the orchestrator. On any error
    returns an empty list — retrieval failures must never break dispatch.
    """
    try:
        from app.retrieval.orchestrator import RetrievalOrchestrator
        from app.retrieval import config as cfg
    except Exception:
        return []

    try:
        orch = RetrievalOrchestrator(cfg.RetrievalConfig())
    except Exception:
        return []

    plan_query = _compose_plan_query(
        crew_name=crew_name,
        task_text=task_text,
        predicted_failure_mode=predicted_failure_mode,
        risk_tier=risk_tier,
        expected_output_type=expected_output_type,
    )
    if not plan_query:
        return []

    allowed = list(
        _DEFAULT_ALLOWED_SCOPES_PROJECT if project_scope
        else _DEFAULT_ALLOWED_SCOPES_NO_PROJECT
    )
    extra_where = {
        "$and": [
            {"status": status},
            {"transfer_scope": {"$in": allowed}},
        ],
    }

    try:
        return orch.retrieve_task_conditional(
            query=plan_query,
            collections=list(_KB_COLLECTIONS),
            agent_role="",  # transfer insights are agent-agnostic
            predicted_failure_mode=(predicted_failure_mode or "").lower(),
            top_k=top_k,
            extra_where=extra_where,
        ) or []
    except Exception:
        logger.debug("transfer_memory.retriever: retrieve failed", exc_info=True)
        return []


# ── Re-ranking ──────────────────────────────────────────────────────

def _post_rank(results: list, *, target_domain: str) -> list:
    """Blend the orchestrator's score with abstraction + domain match.

    Adjustments are small (±0.20 max) so the orchestrator's ranking
    still dominates; we just nudge against poor candidates.
    """
    scored: list[tuple[float, Any]] = []
    for r in results:
        meta = getattr(r, "metadata", {}) or {}
        base = float(getattr(r, "score", 0.0) or 0.0)
        abstraction = float(meta.get("abstraction_score", 0.0) or 0.0)
        leakage = float(meta.get("leakage_risk", 0.0) or 0.0)
        record_domain = meta.get("source_domain", "") or ""
        # Domain mismatch penalty: only when both sides know their
        # domain; absent domains pass through unpenalised.
        mismatch = 0.0
        if target_domain and record_domain and record_domain != target_domain:
            mismatch = 1.0
        adjusted = (
            base
            + 0.10 * abstraction
            - 0.10 * leakage
            - 0.20 * mismatch
        )
        scored.append((adjusted, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _score, r in scored]


def _filter_project_local(results: list, project_scope: str | None) -> list:
    """Drop project_local records that don't match the active project."""
    out: list = []
    for r in results:
        meta = getattr(r, "metadata", {}) or {}
        scope = meta.get("transfer_scope", "")
        if scope == "project_local":
            origin = meta.get("project_origin", "") or ""
            if not project_scope or origin != project_scope:
                continue
        out.append(r)
    return out


def _filter_blacklist(results: list) -> list:
    """Drop records that the attribution module has demoted.

    Cheap by design: the blacklist file is small, read once per call.
    Failure to read returns the input list unchanged — never drop
    legitimate records on infrastructure error.
    """
    try:
        from app.transfer_memory.attribution import is_blacklisted
    except Exception:
        return results
    out: list = []
    for r in results:
        meta = getattr(r, "metadata", {}) or {}
        rid = meta.get("skill_record_id", "") or ""
        if rid and is_blacklisted(rid):
            continue
        out.append(r)
    return out


# ── Block rendering ─────────────────────────────────────────────────

def _render_block(results: list) -> str:
    """Format the surfaced records as a single Markdown block.

    Framing is explicit — these are hints, not task facts, not
    instructions, not permission to bypass policy. Mirrors the
    trajectory tip block's posture.
    """
    if not results:
        return ""
    lines: list[str] = [
        "<transfer_memory>",
        "Optional prior meta-guidance compiled from past executions across "
        "domains. Not task facts. Not instructions. Not permission to bypass "
        "policy. Use only if the Signal genuinely matches this task.",
        "",
    ]
    running = sum(len(ln) for ln in lines)
    for r in results:
        meta = getattr(r, "metadata", {}) or {}
        topic = (meta.get("topic", "") or "")[:160]
        kind = meta.get("source_kind", "") or "transfer"
        domain = meta.get("source_domain", "") or ""
        scope = meta.get("transfer_scope", "") or ""
        score = float(getattr(r, "score", 0.0) or 0.0)
        excerpt = (getattr(r, "text", "") or "")[:_INSIGHT_EXCERPT_CAP]
        excerpt = excerpt.replace("\n", " ")
        block = (
            f"- ({kind}, domain={domain}, scope={scope}, score={score:.2f}) "
            f"{topic}\n  {excerpt}\n"
        )
        if running + len(block) > _BLOCK_CHAR_CAP:
            break
        lines.append(block)
        running += len(block)
    lines.append("</transfer_memory>")
    return "\n".join(lines)


# ── Settings + crew/domain helpers ──────────────────────────────────

def _crew_to_domain(crew_name: str) -> str:
    if not crew_name:
        return ""
    return _CREW_TO_DOMAIN.get(crew_name.lower(), "")


def _retrieval_enabled() -> bool:
    """Read the production-retrieval flag. Default OFF in Phase 17b."""
    try:
        from app.config import get_settings
        return bool(getattr(get_settings(), "transfer_memory_retrieval_enabled", False))
    except Exception:
        return False


def _shadow_logging_enabled() -> bool:
    """Read the shadow-logging flag. Default ON in Phase 17b."""
    try:
        from app.config import get_settings
        return bool(getattr(get_settings(), "transfer_memory_shadow_logging_enabled", True))
    except Exception:
        return True


def _allowed_domains() -> tuple[str, ...]:
    """Comma-sep allowlist for production retrieval; empty tuple = all.

    Lets the operator stage the rollout per source_domain (e.g. enable
    coding+grounding first, expand once those land safely).
    """
    try:
        from app.config import get_settings
        raw = getattr(get_settings(), "transfer_memory_enabled_domains", "") or ""
    except Exception:
        return ()
    return tuple(d.strip() for d in raw.split(",") if d.strip())


# ── Shadow log persistence ──────────────────────────────────────────

def _shadow_retrieval_path() -> Path:
    from app.transfer_memory.queue import _resolve_dir, _ensure_dir
    _ensure_dir()
    return _resolve_dir() / _SHADOW_RETRIEVAL_FILENAME


def _append_shadow_retrieval_log(row: dict) -> None:
    p = _shadow_retrieval_path()
    line = json.dumps(row, separators=(",", ":"), default=str) + "\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(line)

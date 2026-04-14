"""
failure_taxonomy.py — MAST agent-level failure classification.

Extends the infrastructure-level ErrorCategory (TRANSIENT/DATA/SYSTEM/LOGIC)
with 14 agent-level failure modes from the MAST taxonomy (Berkeley, NeurIPS 2025):
  - Specification failures: drift, misinterpretation, incomplete, overscope
  - Inter-agent failures: delegation mismatch, context loss, conflict deadlock,
    handoff corruption, role confusion
  - Verification failures: quality gate miss, hallucination, regression,
    incomplete output, safety boundary

Classification is pure pattern-matching (no LLM call) so it runs in <1ms
on every error. Results flow to healing_knowledge, fault_isolator, and
evolution context via ctx.metadata["_failure_classification"].

TIER_IMMUTABLE — safety-critical classification infrastructure.

Reference: arXiv:2503.13657 "Why Do Multi-Agent LLM Systems Fail?"
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


# ── MAST Taxonomy Enums ─────────────────────────────────────────────────────

class AgentFailureCategory(str, Enum):
    SPECIFICATION = "specification"
    INTER_AGENT = "inter_agent"
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


class AgentFailureMode(str, Enum):
    # Specification (4)
    SPEC_DRIFT = "spec_drift"
    SPEC_MISINTERPRET = "spec_misinterpret"
    SPEC_INCOMPLETE = "spec_incomplete"
    SPEC_OVERSCOPE = "spec_overscope"
    # Inter-agent (5)
    DELEGATION_MISMATCH = "delegation_mismatch"
    CONTEXT_LOSS = "context_loss"
    CONFLICT_DEADLOCK = "conflict_deadlock"
    HANDOFF_CORRUPTION = "handoff_corruption"
    ROLE_CONFUSION = "role_confusion"
    # Verification (5)
    QUALITY_GATE_MISS = "quality_gate_miss"
    HALLUCINATION = "hallucination"
    REGRESSION = "regression"
    INCOMPLETE_OUTPUT = "incomplete_output"
    SAFETY_BOUNDARY = "safety_boundary"
    # Fallback
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class FailureClassification:
    """Dual classification: infrastructure + agent level."""
    infrastructure_category: str  # ErrorCategory value
    agent_category: AgentFailureCategory
    agent_mode: AgentFailureMode
    confidence: float  # 0.0-1.0 classification confidence
    signals: tuple[str, ...]  # Which patterns matched


# ── Pattern detectors ────────────────────────────────────────────────────────

_SPEC_DRIFT_PATTERNS = re.compile(
    r"(unexpected|changed|no longer|deprecated|removed|breaking change|"
    r"api.*changed|schema.*mismatch|version.*incompatible)", re.I
)
_SPEC_MISINTERPRET_PATTERNS = re.compile(
    r"(misunderst|wrong interpretation|not what was asked|off.?topic|"
    r"irrelevant|did not follow|ignored instruction)", re.I
)
_SPEC_INCOMPLETE_PATTERNS = re.compile(
    r"(missing.*required|incomplete.*spec|undefined.*requirement|"
    r"ambiguous.*task|unclear.*instruction)", re.I
)
_SPEC_OVERSCOPE_PATTERNS = re.compile(
    r"(too.*complex|scope.*exceeded|overengineer|unnecessary.*feature|"
    r"beyond.*scope|more.*than.*asked)", re.I
)

_DELEGATION_PATTERNS = re.compile(
    r"(wrong.*crew|wrong.*agent|routing.*error|mismatch.*task|"
    r"delegat.*fail|invalid.*crew)", re.I
)
_CONTEXT_LOSS_PATTERNS = re.compile(
    r"(undefined.*variable|missing.*context|lost.*state|"
    r"not.*defined|context.*missing|no.*memory|forgot)", re.I
)
_CONFLICT_PATTERNS = re.compile(
    r"(deadlock|conflict|contradictory|opposing|infinite.*loop|"
    r"mutual.*exclusion|race.*condition)", re.I
)
_HANDOFF_PATTERNS = re.compile(
    r"(corrupt.*handoff|garbled|truncated.*output|malformed.*result|"
    r"serializ.*error|encoding.*error)", re.I
)
_ROLE_CONFUSION_PATTERNS = re.compile(
    r"(wrong.*role|acting.*as|identity.*confus|role.*mismatch|"
    r"not.*my.*responsibility|capability.*missing)", re.I
)

_QUALITY_GATE_PATTERNS = re.compile(
    r"(quality.*check.*fail|vetting.*reject|below.*threshold|"
    r"score.*too.*low|does.*not.*meet)", re.I
)
_HALLUCINATION_PATTERNS = re.compile(
    r"(hallucin|fabricat|made.*up|not.*factual|false.*claim|"
    r"invented|non.?existent|fictitious)", re.I
)
_REGRESSION_PATTERNS = re.compile(
    r"(regression|worse.*than.*before|degraded|performance.*drop|"
    r"score.*decreased|reverted)", re.I
)
_INCOMPLETE_OUTPUT_PATTERNS = re.compile(
    r"(incomplete.*output|partial.*result|truncated|cut.*off|"
    r"unfinished|missing.*section|empty.*response)", re.I
)
_SAFETY_BOUNDARY_PATTERNS = re.compile(
    r"(safety.*violat|unsafe|dangerous|prohibited|"
    r"constitutional|injection|jailbreak|exfiltrat)", re.I
)

_DETECTORS: list[tuple[AgentFailureCategory, AgentFailureMode, re.Pattern]] = [
    # Verification (check first — most actionable)
    (AgentFailureCategory.VERIFICATION, AgentFailureMode.SAFETY_BOUNDARY, _SAFETY_BOUNDARY_PATTERNS),
    (AgentFailureCategory.VERIFICATION, AgentFailureMode.HALLUCINATION, _HALLUCINATION_PATTERNS),
    (AgentFailureCategory.VERIFICATION, AgentFailureMode.QUALITY_GATE_MISS, _QUALITY_GATE_PATTERNS),
    (AgentFailureCategory.VERIFICATION, AgentFailureMode.REGRESSION, _REGRESSION_PATTERNS),
    (AgentFailureCategory.VERIFICATION, AgentFailureMode.INCOMPLETE_OUTPUT, _INCOMPLETE_OUTPUT_PATTERNS),
    # Inter-agent
    (AgentFailureCategory.INTER_AGENT, AgentFailureMode.DELEGATION_MISMATCH, _DELEGATION_PATTERNS),
    (AgentFailureCategory.INTER_AGENT, AgentFailureMode.CONTEXT_LOSS, _CONTEXT_LOSS_PATTERNS),
    (AgentFailureCategory.INTER_AGENT, AgentFailureMode.CONFLICT_DEADLOCK, _CONFLICT_PATTERNS),
    (AgentFailureCategory.INTER_AGENT, AgentFailureMode.HANDOFF_CORRUPTION, _HANDOFF_PATTERNS),
    (AgentFailureCategory.INTER_AGENT, AgentFailureMode.ROLE_CONFUSION, _ROLE_CONFUSION_PATTERNS),
    # Specification
    (AgentFailureCategory.SPECIFICATION, AgentFailureMode.SPEC_DRIFT, _SPEC_DRIFT_PATTERNS),
    (AgentFailureCategory.SPECIFICATION, AgentFailureMode.SPEC_MISINTERPRET, _SPEC_MISINTERPRET_PATTERNS),
    (AgentFailureCategory.SPECIFICATION, AgentFailureMode.SPEC_INCOMPLETE, _SPEC_INCOMPLETE_PATTERNS),
    (AgentFailureCategory.SPECIFICATION, AgentFailureMode.SPEC_OVERSCOPE, _SPEC_OVERSCOPE_PATTERNS),
]


# ── Classification engine ────────────────────────────────────────────────────

def classify_failure(
    error_text: str,
    agent_id: str = "",
    task_description: str = "",
    context: dict | None = None,
) -> FailureClassification:
    """Classify an error into the MAST taxonomy via pattern matching.

    Runs in <1ms — no LLM calls. Scans error text + task description
    against compiled regex patterns for all 14 failure modes.

    Args:
        error_text: Error message and/or traceback text.
        agent_id: Agent that produced the error.
        task_description: Original task that triggered the error.
        context: Additional context (crew name, metadata dict).

    Returns:
        FailureClassification with both infrastructure and agent-level categories.
    """
    # Combine all searchable text
    search_text = f"{error_text} {task_description} {agent_id}"
    if context:
        search_text += f" {context.get('crew', '')} {context.get('detail', '')}"

    # Scan all detectors, collect matches
    matches: list[tuple[AgentFailureCategory, AgentFailureMode, str]] = []
    for category, mode, pattern in _DETECTORS:
        m = pattern.search(search_text)
        if m:
            matches.append((category, mode, m.group()))

    if not matches:
        return FailureClassification(
            infrastructure_category=_infer_infra_category(error_text),
            agent_category=AgentFailureCategory.UNKNOWN,
            agent_mode=AgentFailureMode.UNCLASSIFIED,
            confidence=0.0,
            signals=(),
        )

    # Use the first (highest priority) match
    best_cat, best_mode, _ = matches[0]
    confidence = min(1.0, 0.5 + 0.15 * len(matches))  # More matches → higher confidence
    signals = tuple(m.group() for _, _, m_text in matches for m in [re.search(re.escape(m_text), search_text)] if m)

    return FailureClassification(
        infrastructure_category=_infer_infra_category(error_text),
        agent_category=best_cat,
        agent_mode=best_mode,
        confidence=round(confidence, 2),
        signals=signals[:5],
    )


def _infer_infra_category(error_text: str) -> str:
    """Infer the infrastructure ErrorCategory from error text."""
    lower = error_text.lower()
    if any(kw in lower for kw in ("timeout", "connection", "rate limit", "503", "429", "retry")):
        return "transient"
    if any(kw in lower for kw in ("parse", "json", "decode", "corrupt", "format")):
        return "data"
    if any(kw in lower for kw in ("oom", "memory", "disk", "process", "killed")):
        return "system"
    return "logic"


# ── SUBIA homeostasis integration ────────────────────────────────────────────

_HOMEOSTASIS_DELTAS: dict[AgentFailureCategory, tuple[str, float]] = {
    AgentFailureCategory.VERIFICATION: ("safety", -0.05),
    AgentFailureCategory.INTER_AGENT: ("coherence", -0.04),
    AgentFailureCategory.SPECIFICATION: ("coherence", -0.03),
}


def _apply_subia_delta(classification: FailureClassification) -> None:
    """Perturb SUBIA homeostatic variables based on failure category."""
    if classification.agent_category not in _HOMEOSTASIS_DELTAS:
        return
    var_name, delta = _HOMEOSTASIS_DELTAS[classification.agent_category]
    try:
        from app.subia.kernel import get_active_kernel
        kernel = get_active_kernel()
        if kernel and hasattr(kernel, "homeostasis"):
            current = kernel.homeostasis.variables.get(var_name, 0.5)
            kernel.homeostasis.variables[var_name] = max(0.0, min(1.0, current + delta))
    except Exception:
        pass


# ── Lifecycle hook ───────────────────────────────────────────────────────────

def create_failure_classifier_hook():
    """Create the ON_ERROR hook that classifies failures with MAST taxonomy."""
    def _hook(ctx):
        if not ctx.errors:
            return ctx
        try:
            error_text = ctx.errors[0] if ctx.errors else ""
            classification = classify_failure(
                error_text=error_text,
                agent_id=ctx.agent_id,
                task_description=ctx.task_description,
                context=ctx.metadata,
            )
            ctx.metadata["_failure_classification"] = {
                "infra": classification.infrastructure_category,
                "agent_category": classification.agent_category.value,
                "agent_mode": classification.agent_mode.value,
                "confidence": classification.confidence,
                "signals": list(classification.signals),
            }
            _apply_subia_delta(classification)
        except Exception:
            pass
        return ctx
    return _hook

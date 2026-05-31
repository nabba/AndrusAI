"""gate_research_evidence — evidence-enforcement evaluator for research drafts.

Phase 1 of the auto-research initiative. A research run delivers drafts
that assert empirical findings — percentages, p-values, named metrics,
sample sizes, monetary figures. When such a draft makes those claims but
cites *nothing*, the finding is ungrounded and should not auto-ship.

This evaluator plugs into the existing ``verification_extension`` chain
(after gate_philosophy) and follows the same escalation-only contract as
every other evaluator there:

  * It can only ADD escalation, never weaken another evaluator's verdict.
  * It activates only on the ``autonomous`` / ``financial`` zones — the
    chat zone keeps its latency-sensitive baseline chain.
  * It self-gates on a three-mode setting so the operator can soak it in
    *advisory* (observe-only) before flipping to *enforcing*.

Three modes (``runtime_settings.research_evidence_gate_mode``):

  * ``off``       — inert. ``evaluate`` returns ``(None, "")``.
  * ``advisory``  — on an evidence gap, returns ``(None, note)``: the note
                    records the action it *would* have taken, but the
                    verdict is unchanged (zero behavior change in prod).
  * ``enforcing`` — on an evidence gap, escalates to ``verify`` (autonomous
                    zone) or ``peer_review`` (financial zone, money on the
                    line) and returns ``(action, note)``.

Detection is deliberately high-precision (favour under-flagging): a gap
is reported only when the draft contains at least one empirical claim AND
carries *no* citation marker at all. A draft that cites anything is
treated as plausibly grounded. The empirical-claim detector COMPOSES the
existing ``app.subia.grounding.claims.extract_claims`` (currency / price
dimension) with a small set of research-aware regexes (percentages,
p-values, named metrics, sample sizes, multipliers) that the currency
extractor does not cover.

Master switch: ``research_evidence_gate_mode`` (default ``off``).
Activation gate: zone ∈ {"autonomous", "financial"} AND
                 draft length >= _MIN_DRAFT_CHARS.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from app.epistemic.calibration import CalibrationVerdict

logger = logging.getLogger(__name__)


# ── Activation tunables ──────────────────────────────────────────────────

# Zones where the gate participates. Mirrors gate_philosophy / the
# verification_extension zone schema. The chat zone is never gated here.
_ACTIVE_ZONES = {"autonomous", "financial"}

# Below this many characters the draft is too short to be a research
# finding worth grounding. Most chat replies fall here.
_MIN_DRAFT_CHARS = 80

# How many sample claim fragments to surface in the diagnostic note.
_MAX_SAMPLES = 3

# Truncate each surfaced sample so the note stays compact.
_SAMPLE_MAX_CHARS = 60


# ── Empirical-claim detection (research-aware) ────────────────────────────

# High-precision patterns for empirical assertions. Deliberately narrow:
# percentages, p-values, named ML/stat metrics with a number, sample
# sizes, and multipliers. Bare integers and years are excluded (too noisy).
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
_P_VALUE = re.compile(r"\bp\s*[<>=]\s*0?\.\d+", re.IGNORECASE)
_SAMPLE_N = re.compile(r"\bn\s*=\s*\d+", re.IGNORECASE)
_MULTIPLIER = re.compile(r"\b\d+(?:\.\d+)?\s?[x×]\b")
_NAMED_METRIC = re.compile(
    r"\b(?:accuracy|precision|recall|f1|f-?score|au[cr]oc?|bleu|rouge|"
    r"rmse|mae|mse|perplexity|speedup|latency|throughput)\b"
    r"\W{0,15}\d+(?:\.\d+)?",
    re.IGNORECASE,
)

_EMPIRICAL_PATTERNS = (_PERCENT, _P_VALUE, _SAMPLE_N, _MULTIPLIER, _NAMED_METRIC)


# Citation markers. Any one of these present → the draft cites *something*
# and we treat it as plausibly grounded (no gap). High-precision so a
# stray token doesn't suppress a genuine gap.
_CITATION_MARKERS = (
    re.compile(r"https?://", re.IGNORECASE),                 # URL
    re.compile(r"\barxiv\b", re.IGNORECASE),                 # arXiv mention
    re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b"),              # arXiv id 2401.01234
    re.compile(r"\b10\.\d{4,}/\S+"),                         # DOI
    re.compile(r"\bdoi:", re.IGNORECASE),                    # doi: prefix
    re.compile(r"\[\d+\]"),                                   # [1] numeric cite
    re.compile(r"\bet al\.?", re.IGNORECASE),                # et al.
    re.compile(r"\baccording to\b", re.IGNORECASE),          # according to X
    re.compile(r"\bsource\s*:", re.IGNORECASE),              # source: / (source:
    re.compile(r"\([A-Z][A-Za-z]+,?\s+\d{4}\)"),            # (Smith, 2021)
)


def _has_citation_marker(text: str) -> bool:
    """True if the draft carries any recognised citation signal."""
    if not text:
        return False
    return any(p.search(text) for p in _CITATION_MARKERS)


def _currency_samples(text: str) -> list[str]:
    """Currency/price fragments via the existing claims extractor.

    Lazy-imported and failure-isolated: if the extractor is unavailable
    or raises, we degrade to research-regex-only detection.
    """
    try:
        from app.subia.grounding.claims import extract_claims

        return [str(getattr(c, "text", "") or "") for c in (extract_claims(text) or [])]
    except Exception:
        logger.debug("gate_research_evidence: extract_claims unavailable", exc_info=True)
        return []


def _detect_evidence_gap(text: str) -> tuple[bool, list[str]]:
    """Return ``(has_gap, sample_fragments)``.

    A gap = the draft makes at least one empirical claim AND carries no
    citation marker. Composes the currency extractor with research-aware
    regexes. Pure / deterministic / never raises.
    """
    if not text:
        return False, []

    samples: list[str] = []
    seen: set[str] = set()

    def _add(frag: str) -> None:
        frag = (frag or "").strip()[:_SAMPLE_MAX_CHARS]
        key = frag.lower()
        if frag and key not in seen:
            seen.add(key)
            samples.append(frag)

    for pat in _EMPIRICAL_PATTERNS:
        for m in pat.finditer(text):
            _add(m.group(0))

    for frag in _currency_samples(text):
        _add(frag)

    if not samples:
        return False, []
    if _has_citation_marker(text):
        return False, []
    return True, samples[:_MAX_SAMPLES]


# ── Self-gating helpers ────────────────────────────────────────────────────


def _mode() -> str:
    """Three-mode switch read. Defaults 'off' on any failure (fail-safe)."""
    try:
        from app import runtime_settings

        return str(runtime_settings.get_research_evidence_gate_mode() or "off")
    except Exception:
        return "off"


def _zone_for_task(task_id: str) -> str:
    """Resolve the verification zone via the existing zone-hints map.
    Defaults to 'chat' when no caller pre-registered the task."""
    try:
        from app.epistemic.verification_extension import _resolve_zone

        return _resolve_zone(task_id)
    except Exception:
        return "chat"


# ── Public API ─────────────────────────────────────────────────────────────


def evaluate(
    *,
    proposal_text: str,
    task_id: str,
    verdict: CalibrationVerdict,
    detect_fn: Optional[Callable[[str], tuple[bool, list[str]]]] = None,
) -> tuple[Optional[str], str]:
    """Run the evidence-enforcement evaluator. Returns
    ``(suggested_action_or_None, note)``.

    Contract matches the verification_extension evaluator contract:

      * ``(None, "")`` — no opinion (mode off, gate inactive, or no gap).
      * ``("verify"|"peer_review", note)`` — enforcing mode found an
        evidence gap; escalate.
      * ``(None, note)`` — advisory mode found a gap; record the
        would-be escalation but leave the verdict unchanged.

    ``detect_fn`` is injectable for host tests (defaults to
    ``_detect_evidence_gap``). Never raises — internal failures fall
    through to ``(None, "")``.
    """
    mode = _mode()
    if mode == "off":
        return None, ""
    if not proposal_text or len(proposal_text) < _MIN_DRAFT_CHARS:
        return None, ""
    zone = _zone_for_task(task_id)
    if zone not in _ACTIVE_ZONES:
        return None, ""

    try:
        has_gap, samples = (detect_fn or _detect_evidence_gap)(proposal_text)
    except Exception as exc:
        logger.debug("gate_research_evidence: detector raised: %s", exc, exc_info=True)
        return None, ""

    if not has_gap:
        return None, ""

    # Proportionate escalation: verify for autonomous, peer_review when
    # money is on the line (financial zone).
    action = "peer_review" if zone == "financial" else "verify"
    sample_str = "; ".join(samples) if samples else "—"
    core = f"{len(samples)} uncited empirical claim(s): {sample_str}"

    if mode == "advisory":
        return None, f"advisory: would escalate ({action}); {core}"
    return action, core


__all__ = ["evaluate"]

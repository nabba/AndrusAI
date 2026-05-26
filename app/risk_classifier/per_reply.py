"""Per-reply zone classifier (Stage D, 2026-05-26).

The existing :mod:`app.risk_classifier` package classifies code-change
*paths* (used by the autonomous executor + change-request gate). This
sibling module classifies *replies* — mapping each user-facing answer to
one of the verification zones the epistemic gate uses for its zone-aware
thresholds (``chat`` / ``autonomous`` / ``financial``).

Without a classifier, every reply defaults to ``chat`` (per
``verification_extension._resolve_zone``), which means the strictest
thresholds (``autonomous=0.90``, ``financial=0.95``) never engage and
Stage D is functionally a no-op. This module is the missing piece.

Design:

* **Heuristic over LLM.** Zone classification is on the hot path — adding
  an LLM call per reply would cost ~$0.001 × 200/day = $6/month for a
  signal we can derive deterministically from sender + lightweight
  keyword + topic matching. Stay regex-cheap.

* **Three zones, picked by sender first, content second.**

    1. ``autonomous`` — reply is being generated for an unattended
       caller (idle scheduler, QoS regression test, autonomous executor,
       schedule_manager). Sender prefix gives this away cheaply.

    2. ``financial`` — content involves money / accounts / transfers /
       currency conversion. Default-strict; better to over-classify a
       chat about exchange rates than to under-classify an investment
       directive. Keyword-driven with a curated stoplist of common
       false-positive phrases ("the cost of doing X"…).

    3. ``chat`` — everything else. Default zone.

* **Idempotent + observable.** The classifier registers the zone via
  :func:`verification_extension.register_zone_for_task` and returns the
  zone string so the verdict telemetry can also record it. Pure
  function — same inputs → same output.

* **Failure-isolated.** Any internal exception falls through to
  ``"chat"`` (the safe default).

* **Master switch.** ``epistemic_per_reply_zone_enabled`` defaults ON
  because it can only *strengthen* the existing default. With it off
  the verification extension still defaults to ``chat`` everywhere —
  same behaviour as today.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── Sender-based classification ──────────────────────────────────────
# Senders whose tasks are unattended-autonomous. Prefix match — these
# come from the scheduler, QoS, autonomous_executor, etc.
_AUTONOMOUS_SENDER_PREFIXES: tuple[str, ...] = (
    "scheduler",
    "qos:",
    "autonomous_executor",
    "qos:answer_regression",
    "internal:idle",
    "self_improver",
)


# ── Content-based classification ─────────────────────────────────────
# Financial-zone signals. The set is deliberately narrow — these are
# words/phrases where misclassifying as chat could cost real money.
# Each entry is a compiled regex with word boundaries to avoid spurious
# substring matches ("payment" in "deployment").
_FINANCIAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"\b" + p + r"\b", re.IGNORECASE)
    for p in (
        # money + accounting
        r"(invoice|invoices)",
        r"(payment|payments)",
        r"(transfer|transfers)\s+(money|funds|amount|usd|eur|gbp)",
        r"(wire|swift)\s+transfer",
        r"(deposit|withdrawal|withdraw)",
        r"(account\s+balance|bank\s+account)",
        r"(credit\s+card|debit\s+card)",
        r"(iban|bic|swift\s+code)",
        # trading + investment
        r"(buy|sell|short|long)\s+\d+\s+(shares?|stocks?|contracts?)",
        r"(stop[\s-]?loss|take[\s-]?profit)",
        r"(execute|place)\s+(an?\s+)?(order|trade)",
        # crypto
        r"(send|transfer)\s+(btc|eth|usdc|usdt|sol)",
        r"(wallet\s+address|seed\s+phrase|private\s+key)",
        # tax + payroll
        r"(payroll|salary\s+payment)",
        r"(tax\s+filing|tax\s+payment)",
    )
)

# Stoplist phrases — when present, suppress a financial match (these
# are common in non-financial chat where the words appear casually).
_FINANCIAL_STOPLIST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"\b" + p + r"\b", re.IGNORECASE)
    for p in (
        r"(at no\s+cost)",
        r"(without\s+cost)",
        r"(the\s+cost\s+of\s+doing)",
        r"(emotional\s+cost)",
    )
)


_VALID_ZONES = frozenset({"chat", "autonomous", "financial"})


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_epistemic_per_reply_zone_enabled
        return bool(get_epistemic_per_reply_zone_enabled())
    except Exception:
        return True  # default ON; observational + safer-default behaviour


def _matches_financial(text: str) -> bool:
    """True if any financial pattern fires AND no stoplist phrase appears.

    Order matters: stoplist check is purely a suppression — if the
    operator says "we're not discussing payments here" the financial
    keyword match upstream should still fire. This logic is deliberately
    conservative — we filter only narrow phrasings that mean the
    financial words are about something else."""
    if not text:
        return False
    if not any(p.search(text) for p in _FINANCIAL_PATTERNS):
        return False
    if any(s.search(text) for s in _FINANCIAL_STOPLIST):
        # Only suppress when the only financial match was weak. A reply
        # mentioning both "transfer money" AND "the cost of doing X" is
        # still financial.
        strong_matches = sum(
            1 for p in _FINANCIAL_PATTERNS if p.search(text)
        )
        if strong_matches <= 1:
            return False
    return True


def classify_reply_zone(
    *,
    user_input: str = "",
    final_reply: str = "",
    sender: str = "",
    task_id: Optional[str] = None,
) -> str:
    """Classify a reply into one of {chat, autonomous, financial}.

    Sender-based classification dominates content. A reply produced for
    the scheduler/QoS/executor is autonomous regardless of what it says —
    those classes already have their own approval gates upstream, and
    the epistemic gate's role for them is the verification floor.

    Args:
      user_input:  The original user request. Searched for financial
                   intent (the *ask* is the strongest signal — sometimes
                   stronger than the agent's reply, e.g. when the agent
                   politely refuses).
      final_reply: The agent's generated reply. Also searched, so an
                   agent that volunteers financial recommendations on a
                   neutral prompt still escalates.
      sender:      The Signal/Discord/CLI/scheduler sender id. Routed
                   to autonomous via prefix match.
      task_id:     If supplied, the resolved zone is registered with
                   :func:`verification_extension.register_zone_for_task`
                   so the gate's :func:`_resolve_zone` finds it.

    Returns the zone string ('chat' default). Never raises."""
    if not _enabled():
        return "chat"
    try:
        # Sender-based shortcut.
        s = (sender or "").lower()
        for prefix in _AUTONOMOUS_SENDER_PREFIXES:
            if s.startswith(prefix):
                zone = "autonomous"
                _register(task_id, zone)
                return zone

        # Content-based: check both user input and reply.
        if _matches_financial(user_input) or _matches_financial(final_reply):
            zone = "financial"
            _register(task_id, zone)
            return zone

        zone = "chat"
        _register(task_id, zone)
        return zone
    except Exception:
        logger.debug("classify_reply_zone: failed", exc_info=True)
        return "chat"


def _register(task_id: Optional[str], zone: str) -> None:
    """Best-effort registration. Failure-isolated and silent."""
    if not task_id or zone not in _VALID_ZONES:
        return
    try:
        from app.epistemic.verification_extension import register_zone_for_task
        register_zone_for_task(task_id, zone)
    except Exception:
        logger.debug("classify_reply_zone: register failed", exc_info=True)

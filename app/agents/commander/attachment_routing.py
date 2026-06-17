"""attachment_routing.py — pure helpers for delivering uploaded attachments
through the Commander's route→dispatch path.

Extracted 2026-06-17 (incident: a 124 kB ``document.md`` over Signal returned
the generic "Sorry, I had trouble understanding that request" error). Two
concerns that MUST stay decoupled:

  * ROUTING is a lightweight LLM classification capped at ``max_tokens=1024``.
    It needs only enough of an attachment to pick the right crew — a head
    excerpt — never the whole body. Forcing the classifier to ingest/echo a
    large document truncated its reply (``finish_reason="length"`` →
    ``CompletionTruncated``) and surfaced the generic error for ANY attachment
    whose extracted text exceeded ~1024 output tokens (~3-4k chars).

  * DELIVERY is getting the FULL extracted document to the worker crew. The
    crew only ever sees ``decision["task"]``, so the full attachment is
    prepended there out-of-band — delivery no longer depends on the router
    reproducing the document inside its bounded JSON reply.

Pure stdlib, no app imports, so the behaviour is unit-testable without the
heavy orchestrator dependency tree. See tests/test_signal_attachment_routing.py.
"""
from __future__ import annotations

# Head-excerpt budget shown to the ROUTER (classification only). The full
# document is delivered to the crew separately, so this only needs to be enough
# to classify the task — not reproduce the file.
ROUTING_ATTACHMENT_PREVIEW_CHARS = 2000

_ROUTING_TRUNCATION_MARKER = (
    "\n…[attachment truncated for routing only — the FULL text is delivered "
    "to the chosen crew automatically. Do NOT copy the document into the task "
    "field; write a short self-contained instruction such as 'Analyse the "
    "attached document and …'.]\n"
)


def digest_attachment_for_routing(
    attachment_context: str,
    max_chars: int = ROUTING_ATTACHMENT_PREVIEW_CHARS,
) -> str:
    """Bounded view of the attachment block for the routing prompt.

    Returns ``""`` for no attachment, the block verbatim when it already fits
    within ``max_chars``, or a head excerpt + marker when it would otherwise
    overflow the router's output budget.
    """
    if not attachment_context:
        return ""
    if len(attachment_context) <= max_chars:
        return attachment_context
    return attachment_context[:max_chars] + _ROUTING_TRUNCATION_MARKER


def deliver_attachment_to_decisions(
    decisions: list,
    attachment_context: str,
    user_input: str,
) -> None:
    """Prepend the FULL attachment to each non-"direct" decision's task.

    Mutates ``decisions`` in place. The worker crew only sees
    ``decision["task"]``; this is the out-of-band channel that makes attachment
    delivery independent of whatever the router echoed. ``direct`` decisions
    are skipped — their task is the verbatim user-facing answer, not a crew
    brief. Decisions whose task already carries an ``<attachment`` block are
    left untouched (no double-prepend).
    """
    if not attachment_context:
        return
    for d in decisions:
        if not isinstance(d, dict) or d.get("crew") == "direct":
            continue
        base = (d.get("task") or "").strip() or user_input
        if "<attachment" not in base:
            d["task"] = f"{attachment_context}\n\n{base}"

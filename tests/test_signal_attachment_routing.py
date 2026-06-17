"""Regression pins for the 2026-06-17 large-attachment routing failure.

Incident: a 124 kB ``document.md`` sent over Signal ("analyse and give verdict")
returned the generic *"Sorry, I had trouble understanding that request. Please
try again."* error. Root cause (verified from gateway logs): the router LLM
(``max_tokens=1024``) was the ONLY channel delivering an attachment to a worker
crew — the routing prompt embedded the full document and the crew only ever
sees ``decision["task"]``. The router could not echo a 30k-char doc within its
output budget, truncated (``finish_reason="length"``), and the completion
guard's ``CompletionTruncated`` propagated to the generic catch-all in
``_route``.

Fix: decouple delivery from routing —
  * routing sees a bounded *digest* (head excerpt only), and
  * the FULL document is delivered to the crew OUT-OF-BAND by prepending it to
    each non-"direct" decision's task.

See app/agents/commander/attachment_routing.py and memory
project_signal_attachment_routing_fix.
"""
import pathlib

from app.agents.commander.attachment_routing import (
    digest_attachment_for_routing,
    deliver_attachment_to_decisions,
    ROUTING_ATTACHMENT_PREVIEW_CHARS,
)

_FAIL = "SIGNAL ATTACHMENT ROUTING 2026-06-17 REGRESSION: "


def _big_attachment(n: int = 30000) -> str:
    """An attachment_context block like _process_attachments produces, but huge
    (the live extractor caps extraction at 30_000 chars)."""
    return (
        '<attachment name="document.md" type="text/markdown">\n'
        + ("X" * n)
        + "\n</attachment>"
    )


# ── digest_attachment_for_routing ────────────────────────────────────────
def test_digest_empty_is_empty():
    assert digest_attachment_for_routing("") == ""


def test_digest_small_block_passthrough():
    s = '<attachment name="n.md" type="text/markdown">\nhi\n</attachment>'
    assert digest_attachment_for_routing(s) == s


def test_digest_large_is_bounded_under_router_budget():
    doc = _big_attachment()
    d = digest_attachment_for_routing(doc)
    # Must stay well under the router's ~1024-token (~3-4k char) OUTPUT budget
    # so the classifier can never truncate on attachment size again.
    assert len(d) <= ROUTING_ATTACHMENT_PREVIEW_CHARS + 400, (
        _FAIL + f"routing digest is {len(d)} chars — not bounded"
    )
    assert len(d) < len(doc)


def test_digest_keeps_classifiable_head_drops_body_tail():
    doc = (
        '<attachment name="document.md" type="text/markdown">\n'
        + ("A" * 100)
        + ("Z" * 30000)
    )
    d = digest_attachment_for_routing(doc)
    assert "document.md" in d                  # enough to classify the task
    assert "Z" * 5000 not in d, _FAIL + "full body tail leaked into routing"


# ── deliver_attachment_to_decisions ──────────────────────────────────────
def test_delivery_prepends_full_doc_to_crew_task():
    doc = _big_attachment()
    decisions = [{"crew": "writing", "task": "Analyse it.", "difficulty": 7}]
    deliver_attachment_to_decisions(decisions, doc, "analyse this")
    assert doc[:60] in decisions[0]["task"], (
        _FAIL + "worker crew did not receive the full document out-of-band"
    )
    assert "Analyse it." in decisions[0]["task"]


def test_delivery_skips_direct_decisions():
    doc = _big_attachment()
    decisions = [{"crew": "direct", "task": "Here is the answer."}]
    deliver_attachment_to_decisions(decisions, doc, "q")
    assert decisions[0]["task"] == "Here is the answer.", (
        _FAIL + "'direct' task (verbatim user-facing answer) was polluted"
    )


def test_delivery_no_double_prepend_when_router_already_echoed():
    doc = _big_attachment()
    pre = '<attachment name="x">...</attachment> do X'
    decisions = [{"crew": "writing", "task": pre}]
    deliver_attachment_to_decisions(decisions, doc, "q")
    assert decisions[0]["task"] == pre


def test_delivery_empty_task_falls_back_to_user_input():
    doc = _big_attachment()
    decisions = [{"crew": "research", "task": ""}]
    deliver_attachment_to_decisions(decisions, doc, "please analyse")
    assert "please analyse" in decisions[0]["task"]
    assert doc[:60] in decisions[0]["task"]


def test_delivery_no_attachment_is_noop():
    decisions = [{"crew": "writing", "task": "t"}]
    deliver_attachment_to_decisions(decisions, "", "q")
    assert decisions[0]["task"] == "t"


def test_delivery_handles_multiple_decisions():
    doc = _big_attachment()
    decisions = [
        {"crew": "research", "task": "a"},
        {"crew": "direct", "task": "b"},
        {"crew": "writing", "task": "c"},
    ]
    deliver_attachment_to_decisions(decisions, doc, "q")
    assert doc[:60] in decisions[0]["task"]
    assert decisions[1]["task"] == "b"          # direct untouched
    assert doc[:60] in decisions[2]["task"]


# ── source-grep pins on the orchestrator wiring ──────────────────────────
_ORCH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "app" / "agents" / "commander" / "orchestrator.py"
)


def test_routing_prompt_uses_digest_not_raw_attachment():
    src = _ORCH.read_text(encoding="utf-8")
    assert "routing_attachment_view = digest_attachment_for_routing(" in src, (
        _FAIL + "routing no longer digests the attachment"
    )
    assert 'f"{routing_attachment_view}"' in src, (
        _FAIL + "routing prompt is not interpolating the bounded digest"
    )
    # The raw full block must NOT be embedded in the routing prompt again.
    assert 'f"{attachment_context}"' not in src, (
        _FAIL + "routing prompt re-embeds the full attachment_context"
    )


def test_orchestrator_delivers_attachment_out_of_band():
    src = _ORCH.read_text(encoding="utf-8")
    assert (
        "deliver_attachment_to_decisions(decisions, attachment_context, user_input)"
        in src
    ), _FAIL + "out-of-band delivery call is missing"


def test_routing_retry_loop_handles_completion_truncated():
    src = _ORCH.read_text(encoding="utf-8")
    assert "CompletionTruncated" in src, (
        _FAIL + "routing retry loop no longer recovers from CompletionTruncated"
    )

"""output_integrity.py — a crew must not deliver its own scaffolding as an answer.

One definition, two consumers
-----------------------------
These patterns are the canonical list, imported by BOTH the serving path (the
post-crew check in ``orchestrator._run_crew_inner``) and the eval scorer
(``evals/score.py``). That is deliberate: on 2026-07-25 every gate fix in this
effort protected exactly ONE crew fork, and the plain ``research`` crew — which
has no evidence gate at all — produced three of the four leakage failures. A
check that lives in one place and is applied to every crew is the point.

What this catches, all observed being DELIVERED to the user on 2026-07-25
--------------------------------------------------------------------------
* ``call:web_search{query:Estonian forest cover changes historical data}`` —
  raw tool-call syntax, returned as a 79-character "report"
* `````\\nThought: The user wants a detailed research report…`` — the
  ReAct scratchpad
* a reply that is nothing but internal JSON
* ``Dossier build failed: OSError: [Errno 36] File name too long: …``
* ``1 validation error for TaskOutput / raw / Input should be a valid string`` —
  the CrewAI validation failure whose raw buffer leaked as the reply
* ``--- SubIA Context ---`` scaffolding (the injection fixed in ``4c11f769``;
  kept here as a backstop)

Why this is NOT the deep-research evidence gate
-----------------------------------------------
It deliberately is not. That gate checks *groundedness* — that claims trace to
retrieved sources — and it needs a structured evidence set. ``ResearchCrew`` is a
plain ``crew.kickoff()`` returning a string and captures no such set, so giving it
the real evidence gate requires building per-request evidence capture first. This
is the narrower, feasible half: it establishes that a reply is an ANSWER rather
than internal machinery. Groundedness for the non-deep path remains open.

Conservatism, because a false positive here destroys a good answer
-----------------------------------------------------------------
In scoring, a false positive costs one mislabelled row. In serving it would throw
away a real answer. So ambiguous markers (``Thought:``, fenced JSON) only count
when they dominate the reply — present in the opening span, or the reply is short.
An unambiguous marker (a traceback, a validation error, SubIA scaffolding, a bare
tool call) counts anywhere.
"""

from __future__ import annotations

import json
import re

# How much of the opening counts as "dominates" for ambiguous markers.
_OPENING_CHARS = 400

# A reply at or under this length is treated as dominated by any marker in it.
_SHORT_REPLY_CHARS = 600

# ── Unambiguous: internal machinery, never part of a real answer ─────────
_UNAMBIGUOUS: tuple[tuple[str, re.Pattern], ...] = (
    ("leakage:tool_call_syntax", re.compile(r"(?:^|\n)\s*call:[a-z_]+\s*\{", re.I)),
    ("leakage:validation_error", re.compile(r"\d+ validation error(?:s)? for \w+", re.I)),
    ("leakage:subia_scaffolding", re.compile(r"---\s*(?:End\s+)?SubIA Context\s*---", re.I)),
    ("leakage:traceback", re.compile(
        r"Traceback \(most recent call last\)|\[Errno \d+\]"
        r"|(?:^|\n)\s*(?:build failed|Task execution failed)\s*:", re.I)),
)

# ── Ambiguous: legitimate prose can contain these, so require dominance ──
_AMBIGUOUS: tuple[tuple[str, re.Pattern], ...] = (
    ("leakage:react_scratchpad",
     re.compile(r"(?:^|\n)\s*(?:Thought|Action|Observation)\s*:", re.M)),
    ("leakage:phase_transcript",
     re.compile(r"\[\s*(?:researcher|writer|coder|critic)\s*\]", re.I)),
)


def _strip_fences(text: str) -> str:
    lines = (text or "").strip().splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def is_whole_reply_json(text: str) -> bool:
    """Whether the entire reply is a JSON document rather than an answer."""
    body = _strip_fences(text).strip()
    if not body or body[0] not in "{[":
        return False
    try:
        json.loads(body)
        return True
    except Exception:
        return False


def find_artifacts(text: str, *, strict: bool = False) -> list[str]:
    """Return the artifact clauses present in ``text``.

    ``strict=True`` (the eval scorer) counts ambiguous markers anywhere.
    ``strict=False`` (the serving path) requires them to dominate the reply, so a
    long legitimate report that happens to contain "Thought:" is not destroyed.
    """
    reply = text or ""
    clauses: list[str] = []

    for clause, pattern in _UNAMBIGUOUS:
        if pattern.search(reply):
            clauses.append(clause)

    dominant = strict or len(reply) <= _SHORT_REPLY_CHARS
    opening = reply[:_OPENING_CHARS]
    for clause, pattern in _AMBIGUOUS:
        if pattern.search(reply if dominant else opening):
            clauses.append(clause)

    if is_whole_reply_json(reply):
        clauses.append("leakage:raw_json")

    return clauses


def describe(clauses: list[str]) -> str:
    """Operator-facing cause for a no-answer signal."""
    pretty = {
        "leakage:tool_call_syntax": "returned raw tool-call syntax instead of an answer",
        "leakage:react_scratchpad": "returned its reasoning scratchpad instead of an answer",
        "leakage:raw_json": "returned internal JSON instead of an answer",
        "leakage:traceback": "returned an error trace instead of an answer",
        "leakage:validation_error": "its output failed schema validation and the raw buffer leaked",
        "leakage:subia_scaffolding": "returned internal context scaffolding instead of an answer",
        "leakage:phase_transcript": "returned a multi-agent transcript instead of an answer",
    }
    return "; ".join(pretty.get(c, c) for c in clauses)

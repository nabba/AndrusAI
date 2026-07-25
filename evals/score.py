#!/usr/bin/env python3
"""Deterministic positive scorer for eval replies.

Phase 2 of docs/EVAL_HARNESS_V2_PLAN.md. Replaces the substring blocklist, which
has been wrong three times and always overstated success. This asks whether a
reply satisfies its question's `contract` in golden_set.jsonl, and returns one of
three outcomes:

    pass                    satisfies the contract
    fail                    does not — including content presented as an answer
                            that isn't grounded when grounding is required
    blocked_infrastructure  honestly names an external cause AND withholds
                            invented content; excluded from the quality denominator

What this scorer does NOT decide
--------------------------------
`must_address` coverage is semantic and is deliberately left to Phase 3's rubric
judge. A Phase 2 `pass` therefore means "not obviously failing on shape,
substance, artifacts or citation count" — it is a floor, not a completeness
verdict. Reported as `coverage_checked: false` so a pass is never over-read.

Citation *resolution* is also unchecked: `must_resolve_to_run_evidence` needs the
run's evidence set, which is not stored. Counts only, flagged in the output.

A correction to the plan
------------------------
EVAL_HARNESS_V2_PLAN §Phase 2 claimed one substance check would subsume all four
leakage shapes. That is false, and testing found it: the ReAct-scratchpad reply
was 1903 chars — enough words to clear `gs_ambiguous_short_report`'s 250-word
bar. Artifact detection is a separate, necessary layer.

    python evals/score.py evals/results/<report>.json [--labels evals/labels/<x>.json]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_GOLDEN = Path(__file__).parent / "golden_set.jsonl"

PASS = "pass"
FAIL = "fail"
BLOCKED = "blocked_infrastructure"

# A fourth, non-quality outcome (added 2026-07-25 while running the Phase 2 gate).
#
# The gate produced two disagreements with the human labels, and the plan's
# falsifier said that means the contracts or checks are wrong. Diagnosis found a
# THIRD possibility the plan had not enumerated: the input was insufficient.
# Reports predating full-reply capture store only `reply[:200]`, so
#   * gs_report_forest  — 31 stored words vs 6177 actual chars — fell below the
#     "delivered a body anyway" threshold and was scored `blocked_infrastructure`
#     instead of `fail`;
#   * gs_coding — truncated before its asserts — failed the code-shape check;
#   * gs_report_industry — truncated JSON no longer parses, so the raw-JSON check
#     missed and it failed on substance instead (right verdict, wrong reason).
#
# Guessing in that state produces exactly the false confidence this rebuild
# exists to remove, so length- and shape-dependent verdicts are refused rather
# than approximated. Artifact detections still stand: they match at the start of
# a reply and are therefore visible in a preview.
UNSCORABLE = "unscorable_truncated"

_URL_RE = re.compile(r"https?://[^\s<>()\[\]|]+", re.IGNORECASE)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*|\s*```\s*$")

# ── Internal-artifact detectors ──────────────────────────────────────────
#
# Every one of these was observed being scored a SUCCESS by the old blocklist on
# 2026-07-25. They are checked before anything else because they are the most
# certain signal available: an answer that is a tool call, a scratchpad, a
# traceback or internal scaffolding is not an answer at all, whatever its length.
_ARTIFACTS: tuple[tuple[str, re.Pattern], ...] = (
    ("leakage:tool_call_syntax", re.compile(r"(?:^|\n)\s*call:[a-z_]+\s*\{", re.I)),
    ("leakage:react_scratchpad", re.compile(r"(?:^|\n)\s*(?:Thought|Action|Observation)\s*:", re.M)),
    ("leakage:traceback", re.compile(
        r"Traceback \(most recent call last\)|\b(?:OSError|ValueError|TypeError|KeyError)\b\s*:"
        r"|\[Errno \d+\]|build failed:|Task execution failed:", re.I)),
    ("leakage:subia_scaffolding", re.compile(r"---\s*(?:End\s+)?SubIA Context\s*---", re.I)),
    ("leakage:phase_transcript", re.compile(r"\[\s*(?:researcher|writer|coder|critic)\s*\]", re.I)),
    ("leakage:validation_error", re.compile(r"\d+ validation error(?:s)? for \w+", re.I)),
)

# Asserting a capability the system demonstrably has (it holds web search) is a
# `fail`, not `blocked_infrastructure`. Observed on 2026-07-25 in gs_research_deep.
_CAPABILITY_CLAIM = re.compile(
    r"I (?:do not|don't) have (?:live |direct )?access"
    r"|I cannot (?:complete|fulfil|fulfill) this request as given"
    r"|I(?:'m| am) (?:unable|not able) to (?:browse|search the web|access the internet)",
    re.I,
)

# An EXTERNAL cause, named honestly. Distinguished from a capability claim: these
# describe a transient failure of a tool the system does have.
_INFRA_CAUSE = re.compile(
    r"cannot be answered from the retrieved evidence"
    r"|(?:retrieved|available) (?:sources|evidence) (?:are|is|contains?) (?:all )?off[- ]topic"
    r"|no (?:web )?(?:search )?results (?:were )?(?:returned|available|found)"
    r"|search (?:is )?unavailable|quota (?:exhausted|exceeded)"
    r"|insufficient credits|calendar (?:is )?unavailable",
    re.I,
)

# Prose that admits it is ungrounded. Per the plan's hard rule this is `fail`
# when the contract requires grounding AND the reply still delivers a body of
# content — disclosure is not a substitute for evidence.
_UNGROUNDED_DISCLOSURE = re.compile(
    r"drawn from general knowledge"
    r"|(?:do not|don't) (?:currently )?have a verified retrieval set"
    r"|rather than (?:from )?retrieved sources"
    r"|not based on retrieved (?:sources|evidence)",
    re.I,
)


def _canonical_artifacts():
    """The serving path's artifact definitions, so the two cannot drift.

    ``app/crews/output_integrity.py`` is the single source of truth, imported by
    both the post-crew check in the orchestrator and this scorer. Falls back to
    the local patterns when ``app`` is not importable (scoring a report on a host
    without the gateway installed).
    """
    import sys
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from app.crews.output_integrity import find_artifacts, is_whole_reply_json
        return find_artifacts, is_whole_reply_json
    except Exception:
        return None, None


def _strip_fences(text: str) -> str:
    lines = (text or "").strip().splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _is_whole_reply_json(text: str) -> bool:
    _, canonical = _canonical_artifacts()
    if canonical is not None:
        return canonical(text)
    body = _strip_fences(text).strip()
    if not body or body[0] not in "{[":
        return False
    try:
        json.loads(body)
        return True
    except Exception:
        return False


def _word_count(text: str) -> int:
    return len((text or "").split())


def _line_count(text: str) -> int:
    return len([ln for ln in (text or "").splitlines() if ln.strip()])


def _citations(text: str) -> set[str]:
    found = {m.group(0).rstrip(".,;:)]}") for m in _URL_RE.finditer(text or "")}
    found |= {m.group(0).rstrip(".,;:)]}") for m in _DOI_RE.finditer(text or "")}
    return {c for c in found if c}


def _looks_like_code(text: str) -> bool:
    body = _strip_fences(text)
    return "def " in body and ("assert" in body or "test" in body.lower())


def _code_parses(text: str) -> bool:
    """Whether the code block is syntactically valid Python.

    Parse only — deliberately NOT execution. The contracts carry an
    `executable_check` (run it, compare the sequence) which is the strongest
    available signal, but running model-generated code belongs in the repo's
    sandbox (app/eval_sandbox.py, app/sandbox_runner.py), not in a scorer
    process. Left for a sandboxed follow-up rather than silently skipped.
    """
    import ast
    try:
        ast.parse(_strip_fences(text))
        return True
    except SyntaxError:
        return False


def score_result(contract: dict, result: dict) -> dict:
    """Score one reply against its contract. Returns verdict + clauses + checks."""
    reply = result.get("reply") or result.get("reply_preview") or ""
    # "Truncated" means text was actually CUT, not merely that the full-reply
    # field is absent: a short reply's 200-char preview IS the whole reply, and
    # marking those unscorable would discard perfectly good evidence.
    truncated = (
        not result.get("reply")
        and int(result.get("reply_chars") or 0) > len(reply)
    )
    clauses: list[str] = []
    checks: dict = {}

    # 0. Transport. A gateway that closed the connection is OUR defect, so this
    #    is `fail` — consistent with the dossier crash ruling in the contracts.
    if result.get("error"):
        return {
            "verdict": FAIL, "clauses": [f"transport_error:{result['error'][:60]}"],
            "checks": {}, "coverage_checked": False, "reply_truncated": truncated,
        }

    shape = contract.get("answer_shape", "")
    substance = contract.get("min_substance") or {}
    citation = contract.get("citation") or {}
    needs_citations = int(citation.get("min_distinct_sources") or 0)
    grounding_required = bool(citation.get("must_resolve_to_run_evidence"))

    words, lines = _word_count(reply), _line_count(reply)
    cites = _citations(reply)
    checks.update({
        "words": words, "lines": lines, "distinct_citations": len(cites),
        "min_words": substance.get("words"), "min_lines": substance.get("lines"),
        "min_citations": needs_citations,
        "citation_resolution": "unchecked — evidence set not stored (Phase 3)",
    })

    # 1. Internal artifacts. Most certain signal; checked first. Prefer the
    #    serving path's canonical definitions (app/crews/output_integrity.py) so
    #    a shape the gate suppresses and a shape the scorer fails can never
    #    diverge; strict=True because in scoring an ambiguous marker anywhere is
    #    worth flagging, whereas serving requires it to dominate.
    canonical_find, _ = _canonical_artifacts()
    if canonical_find is not None:
        clauses.extend(
            c for c in canonical_find(reply, strict=True)
            if not (c == "leakage:raw_json" and shape == "structured_dossier")
        )
    else:
        for clause, pattern in _ARTIFACTS:
            if pattern.search(reply):
                clauses.append(clause)
        if _is_whole_reply_json(reply) and shape != "structured_dossier":
            clauses.append("leakage:raw_json")
    if clauses:
        return {"verdict": FAIL, "clauses": clauses, "checks": checks,
                "coverage_checked": False, "reply_truncated": truncated}

    # 2. A false capability claim outranks the infrastructure exemption. Safe on
    #    a preview: the claim is made up front.
    if _CAPABILITY_CLAIM.search(reply):
        return {"verdict": FAIL, "clauses": ["false_capability_claim"],
                "checks": checks, "coverage_checked": False,
                "reply_truncated": truncated}

    # 2b. Everything below needs the WHOLE reply — the fail-vs-blocked
    #     discriminator, shape, substance and citation counts. Refuse rather
    #     than approximate. See UNSCORABLE.
    if truncated:
        return {
            "verdict": UNSCORABLE,
            "clauses": [
                f"only {len(reply)} of {result.get('reply_chars', '?')} chars stored — "
                "no artifact matched, and every remaining check needs the full reply"
            ],
            "checks": checks, "coverage_checked": False, "reply_truncated": True,
        }

    # 3. Ungrounded-by-disclosure, when grounding is required AND a body of
    #    content was still delivered. This is the plan's hard rule.
    if grounding_required and _UNGROUNDED_DISCLOSURE.search(reply):
        min_words = int(substance.get("words") or 0)
        delivered_body = words >= max(120, min_words // 3)
        if delivered_body:
            return {"verdict": FAIL, "clauses": ["ungrounded_by_disclosure"],
                    "checks": checks, "coverage_checked": False,
                    "reply_truncated": truncated}
        return {"verdict": BLOCKED, "clauses": ["named_cause_and_withheld"],
                "checks": checks, "coverage_checked": False,
                "reply_truncated": truncated}

    # 4. Honest infrastructure block: names an external cause and does not
    #    deliver a full answer body anyway.
    if _INFRA_CAUSE.search(reply):
        min_words = int(substance.get("words") or 0)
        if not min_words or words < min_words:
            return {"verdict": BLOCKED, "clauses": ["named_cause_and_withheld"],
                    "checks": checks, "coverage_checked": False,
                    "reply_truncated": truncated}
        clauses.append("named_cause_but_delivered_full_body")

    # 5. Shape.
    if shape == "code":
        if not _looks_like_code(reply):
            clauses.append("shape:not_code (needs a def plus a test/assert)")
        if substance.get("parses") and not _code_parses(reply):
            clauses.append("shape:code_does_not_parse")
        checks["code_parses"] = _code_parses(reply)
        checks["executable_check"] = (
            "NOT run — needs the repo sandbox; see score.py:_code_parses"
        )
    if shape == "poem":
        min_lines = int(substance.get("lines") or 0)
        if lines < min_lines:
            clauses.append(f"shape:too_few_lines ({lines} < {min_lines})")

    # 6. Substance.
    min_words = int(substance.get("words") or 0)
    if min_words and words < min_words:
        clauses.append(f"substance:too_short ({words} words < {min_words})")

    # 7. Citation count.
    if needs_citations and len(cites) < needs_citations:
        clauses.append(f"citations:too_few ({len(cites)} < {needs_citations})")

    verdict = FAIL if clauses else PASS
    return {"verdict": verdict, "clauses": clauses, "checks": checks,
            "coverage_checked": False, "reply_truncated": truncated}


def load_contracts() -> dict:
    return {
        json.loads(line)["id"]: json.loads(line)
        for line in _GOLDEN.read_text().splitlines() if line.strip()
    }


def score_report(payload: dict) -> dict:
    contracts = load_contracts()
    scored = []
    for result in payload.get("results", []):
        entry = contracts.get(result["id"], {})
        contract = entry.get("contract")
        if not contract:
            scored.append({"id": result["id"], "verdict": "unscored",
                           "clauses": ["no contract in golden set"]})
            continue
        out = score_result(contract, result)
        out["id"] = result["id"]
        scored.append(out)

    counts = {PASS: 0, FAIL: 0, BLOCKED: 0, UNSCORABLE: 0}
    for s in scored:
        if s["verdict"] in counts:
            counts[s["verdict"]] += 1
    denom = counts[PASS] + counts[FAIL]
    return {
        "scored": scored,
        "summary": {
            **counts,
            "quality_rate": (round(counts[PASS] / denom, 3) if denom else None),
            "quality_denominator": denom,
            "note": "blocked_infrastructure and unscorable_truncated are both "
                    "excluded from the denominator; coverage (must_address) is "
                    "NOT checked — Phase 3",
            "replies_truncated": sum(1 for s in scored if s.get("reply_truncated")),
            "gate_runnable": counts[UNSCORABLE] == 0,
            "gate_note": (
                "this report predates full-reply capture, so the Phase 2 gate "
                "cannot be met on it — re-run the eval to store full replies"
                if counts[UNSCORABLE] else "all replies scorable"
            ),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--labels", type=Path, default=None,
                    help="Human labels to diff against (the Phase 2 gate).")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    payload = json.loads(args.report.read_text())
    scored = score_report(payload)

    print(f"{'question':<28}{'scorer':<24}clauses")
    print("-" * 104)
    for s in scored["scored"]:
        print(f"{s['id']:<28}{s['verdict']:<24}{'; '.join(s.get('clauses') or []) [:50]}")
    print()
    print(json.dumps(scored["summary"], indent=2))

    if args.labels:
        labels = json.loads(args.labels.read_text())["outcomes"]
        by_id = {s["id"]: s for s in scored["scored"]}
        disagreements, unscorable = [], []
        for qid, human in labels.items():
            mine = by_id.get(qid, {}).get("verdict")
            if mine == UNSCORABLE:
                unscorable.append((qid, human["verdict"]))
            elif mine != human["verdict"]:
                disagreements.append((qid, human["verdict"], mine, human.get("clause")))
        comparable = len(labels) - len(unscorable)
        print()
        print(f"=== Phase 2 gate: agreement with {args.labels.name} ===")
        print(f"comparable: {comparable}/{len(labels)}  "
              f"(agree on {comparable - len(disagreements)}/{comparable})")
        for qid, want, got, clause in disagreements:
            print(f"  DISAGREE {qid}: human={want} scorer={got} (human clause: {clause})")
        for qid, want in unscorable:
            print(f"  UNSCORABLE {qid}: human={want}, scorer refused (reply truncated)")
        if unscorable:
            print(
                f"\n!! GATE NOT MET — {len(unscorable)} of {len(labels)} replies are "
                "truncated, so the length- and shape-dependent checks cannot be "
                "validated.\n"
                "!! This is neither wrong contracts nor wrong checks: it is "
                "insufficient input.\n"
                "!! Re-run the eval (reports now store full replies) and score that."
            )
        elif len(disagreements) > 1:
            print("\n!! More than one disagreement: per the plan's falsifier the "
                  "CONTRACTS or CHECKS are wrong. Do not tune thresholds to "
                  "close the gap.")
        elif not disagreements:
            print("\nGATE MET: scorer reproduces every human label.")

    if args.out:
        args.out.write_text(json.dumps(scored, indent=2) + "\n")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

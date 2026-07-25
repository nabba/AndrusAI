"""The Phase 2 scorer must catch what the substring blocklist scored as success.

docs/EVAL_HARNESS_V2_PLAN.md Phase 2. The old scorer decided `delivered` from a
blocklist and was wrong three times, always overstating. Tested against the six
failure shapes actually observed on 2026-07-25, FIVE scored as successes.

Fixtures below are those shapes, verbatim in form. Roughly half the tests pin
what must still PASS, because over-rejection would make the scorer useless in the
other direction.

Also pins the three-outcome distinction that the old pass/fail could not express:
an honest non-answer naming an external cause is `blocked_infrastructure`, while
the same disclosure attached to a full delivered body is `fail`.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_SCORE = Path(__file__).resolve().parents[1] / "evals" / "score.py"


@pytest.fixture(scope="module")
def sc():
    if not _SCORE.exists():  # pragma: no cover
        pytest.skip("evals/score.py not present")
    spec = importlib.util.spec_from_file_location("_score_under_test", _SCORE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _report_contract(words=600, cites=3):
    return {
        "answer_shape": "prose_report",
        "min_substance": {"words": words},
        "citation": {"min_distinct_sources": cites, "must_resolve_to_run_evidence": True},
        "must_address": ["something"],
        "must_not": [],
    }


def _result(reply, error=None):
    return {"reply": reply, "error": error, "reply_chars": len(reply)}


def _grounded_report(words=700, sources=3):
    body = " ".join(f"Finding {i} about forest cover and harvest volumes." for i in range(words // 7))
    cites = "\n".join(
        f"[S{i}] Source {i} — https://source{i}.example/report" for i in range(1, sources + 1)
    )
    return f"# Report\n\n{body}\n\n## Sources\n{cites}\n"


# ── the six shapes that scored as success ────────────────────────────────

def test_catches_leaked_tool_call(sc):
    out = sc.score_result(
        _report_contract(words=500, cites=2),
        _result("call:web_search{query:Estonian forest cover changes historical data}"),
    )
    assert out["verdict"] == sc.FAIL
    assert "leakage:tool_call_syntax" in out["clauses"]


def test_catches_leaked_react_scratchpad_even_when_long_enough(sc):
    """The plan claimed a substance check subsumes this. It does not — 1903
    chars of scratchpad clears a 250-word bar, so artifact detection is a
    separate necessary layer."""
    scratchpad = (
        "```\nThought: The user wants a detailed research report on Tallinn's "
        "housing prices as of July 2026. I need to find:\n"
        + "\n".join(f"{i}. Some sub-question about prices and trends." for i in range(1, 40))
        + "\n```"
    )
    contract = _report_contract(words=250, cites=2)
    assert sc._word_count(scratchpad) >= 250, "fixture must clear the word bar"

    out = sc.score_result(contract, _result(scratchpad))
    assert out["verdict"] == sc.FAIL
    assert "leakage:react_scratchpad" in out["clauses"]


def test_catches_raw_json_reply(sc):
    out = sc.score_result(
        _report_contract(),
        _result('```json\n{"title": "Critical Report", "subjects": [{"id": "x"}]}\n```'),
    )
    assert out["verdict"] == sc.FAIL
    assert "leakage:raw_json" in out["clauses"]


def test_catches_crash_traceback(sc):
    out = sc.score_result(
        {"answer_shape": "structured_dossier", "min_substance": {"words": 700},
         "citation": {"min_distinct_sources": 3, "must_resolve_to_run_evidence": True}},
        _result("Dossier build failed: OSError: [Errno 36] File name too long: "
                "'/app/workspace/output/dossier_subia_context_loop_compressed...'"),
    )
    assert out["verdict"] == sc.FAIL
    assert any("traceback" in c for c in out["clauses"])


def test_catches_subia_scaffolding(sc):
    out = sc.score_result(
        _report_contract(),
        _result("--- SubIA Context ---\nloop: compressed\n--- End SubIA Context ---\nsome text"),
    )
    assert out["verdict"] == sc.FAIL
    assert "leakage:subia_scaffolding" in out["clauses"]


def test_catches_ungrounded_by_disclosure(sc):
    """The plan's hard rule: disclosure is not a substitute for evidence."""
    reply = (
        "I don't currently have a verified retrieval set to cite for this "
        "specific report, so the following is drawn from general knowledge of "
        "publicly available Estonian forestry sources rather than retrieved "
        "sources.\n\n" + _grounded_report(words=700, sources=0)
    )
    out = sc.score_result(_report_contract(), _result(reply))
    assert out["verdict"] == sc.FAIL
    assert "ungrounded_by_disclosure" in out["clauses"]


def test_catches_false_capability_claim(sc):
    reply = (
        "I cannot complete this request as given. You asked for deep research "
        "with primary sources, but I do not have live access to primary source "
        "databases." + " padding" * 200
    )
    out = sc.score_result(_report_contract(), _result(reply))
    assert out["verdict"] == sc.FAIL
    assert "false_capability_claim" in out["clauses"]


def test_catches_leaked_validation_error(sc):
    out = sc.score_result(
        _report_contract(),
        _result("1 validation error for TaskOutput\nraw\n  Input should be a valid string"),
    )
    assert out["verdict"] == sc.FAIL


# ── the three-outcome distinction ────────────────────────────────────────

def test_honest_non_answer_is_blocked_not_failed(sc):
    reply = (
        "**Finding: this question cannot be answered from the retrieved "
        "evidence.**\n\n- None of the retrieved sources provide the "
        "quantitative material the question requires. All retrieved sources "
        "are off-topic: AI research-synthesis notes, a solar-flare predictor, "
        "and two text-diff web tools."
    )
    out = sc.score_result(_report_contract(), _result(reply))
    assert out["verdict"] == sc.BLOCKED
    assert "named_cause_and_withheld" in out["clauses"]


def test_naming_a_cause_then_delivering_anyway_is_not_blocked(sc):
    """The discriminator: withholding vs delivering a full body regardless."""
    reply = (
        "No web search results were available for this query.\n\n"
        + _grounded_report(words=800, sources=3)
    )
    out = sc.score_result(_report_contract(words=600), _result(reply))
    assert out["verdict"] != sc.BLOCKED


def test_transport_error_is_a_failure_not_infrastructure(sc):
    """A gateway that dropped the connection is our defect."""
    out = sc.score_result(
        _report_contract(),
        _result("", error="RemoteDisconnected: Remote end closed connection"),
    )
    assert out["verdict"] == sc.FAIL
    assert any("transport_error" in c for c in out["clauses"])


# ── must still PASS: over-rejection is the other failure mode ────────────

def test_a_real_grounded_report_passes(sc):
    out = sc.score_result(_report_contract(words=600, cites=3),
                          _result(_grounded_report(words=800, sources=4)))
    assert out["verdict"] == sc.PASS, out["clauses"]


def test_short_fact_with_a_source_passes(sc):
    contract = {
        "answer_shape": "short_fact",
        "min_substance": {"words": 15},
        "citation": {"min_distinct_sources": 1, "must_resolve_to_run_evidence": False},
    }
    reply = ("As of January 1, 2026, the official population of Estonia was "
             "1,360,745, according to Statistics Estonia.\n\n"
             "Source: https://stat.ee/en/find-statistics/statistics-theme/population")
    assert sc.score_result(contract, _result(reply))["verdict"] == sc.PASS


def test_a_poem_passes_and_is_not_judged_on_citations(sc):
    contract = {"answer_shape": "poem", "min_substance": {"lines": 6},
                "citation": {"min_distinct_sources": 0}}
    poem = ("**Kesäilta järvellä**\n\nThe sun forgets to set —\n"
            "it only leans on the pines,\nspilling copper across water\n"
            "that has not moved for hours.\n\nA loon's cry stitches the far shore\n"
            "to the near one.\nSmoke from a distant sauna.\n")
    assert sc.score_result(contract, _result(poem))["verdict"] == sc.PASS


def test_code_with_docstring_and_tests_passes(sc):
    """Uses the REAL gs_coding contract so fixture and scorer cannot drift.

    This test is why gs_coding lost its word bar: a correct, complete answer is
    39 whitespace tokens and failed a 40-word minimum. Per the plan's falsifier
    the contract changed, not the scorer's thresholds.
    """
    contract = sc.load_contracts()["gs_coding"]["contract"]
    code = ('```python\ndef fibonacci(n):\n    """Return the first n terms."""\n'
            '    seq = [0, 1]\n    while len(seq) < n:\n'
            '        seq.append(seq[-1] + seq[-2])\n    return seq[:n]\n\n'
            'assert fibonacci(1) == [0]\n'
            'assert fibonacci(10) == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]\n```')
    out = sc.score_result(contract, _result(code))
    assert out["verdict"] == sc.PASS, out["clauses"]
    assert out["checks"]["code_parses"] is True


def test_syntactically_broken_code_fails(sc):
    contract = sc.load_contracts()["gs_coding"]["contract"]
    broken = '```python\ndef fibonacci(n:\n    """Oops."""\n    assert True\n```'
    out = sc.score_result(contract, _result(broken))
    assert out["verdict"] == sc.FAIL
    assert "shape:code_does_not_parse" in out["clauses"]


def test_conversational_reply_passes(sc):
    contract = {"answer_shape": "conversational", "min_substance": {"words": 3},
                "citation": {"min_distinct_sources": 0}}
    reply = ("Hey! All good here — systems running normally, nothing on fire. "
             "It's a warm Saturday evening in Tallinn. What can I help with?")
    assert sc.score_result(contract, _result(reply))["verdict"] == sc.PASS


def test_prose_mentioning_a_url_is_not_treated_as_json(sc):
    """Guard against the JSON check firing on ordinary prose."""
    assert not sc._is_whole_reply_json("Estonia's forest area is 2.3M ha [S1].")
    assert sc._is_whole_reply_json('{"a": 1}')
    assert sc._is_whole_reply_json('```json\n[1, 2, 3]\n```')


def test_citation_counting_dedupes_and_strips_punctuation(sc):
    text = ("See https://a.example/x. Also https://a.example/x, and "
            "https://b.example/y) plus doi 10.1234/abc.")
    cites = sc._citations(text)
    assert "https://a.example/x" in cites
    assert "https://b.example/y" in cites
    assert "10.1234/abc" in cites
    assert len(cites) == 3


def test_report_shape_short_answer_fails_on_substance(sc):
    out = sc.score_result(_report_contract(words=500, cites=2),
                          _result("Estonian forests have changed a lot."))
    assert out["verdict"] == sc.FAIL
    assert any("too_short" in c for c in out["clauses"])


def test_pass_is_reported_as_coverage_unchecked(sc):
    """A Phase 2 pass is a floor, not a completeness verdict."""
    out = sc.score_result(_report_contract(words=600, cites=3),
                          _result(_grounded_report(words=800, sources=4)))
    assert out["verdict"] == sc.PASS
    assert out["coverage_checked"] is False


# ── truncated input must be refused, not approximated ────────────────────

def test_truncated_reply_is_refused_not_guessed(sc):
    """The Phase 2 gate's two disagreements were both truncation artifacts.

    gs_report_forest stored 31 words of a 6177-char reply and so fell below the
    "delivered a body anyway" threshold, scoring blocked_infrastructure instead
    of fail. Guessing there is exactly the false confidence this rebuild removes.
    """
    contract = _report_contract(words=700, cites=3)
    result = {
        "reply_preview": "I don't currently have a verified retrieval set to cite "
                         "for this specific report, so the following is drawn fr",
        "reply_chars": 6177,
        "error": None,
    }
    out = sc.score_result(contract, result)
    assert out["verdict"] == sc.UNSCORABLE
    assert out["reply_truncated"] is True


def test_a_short_complete_reply_is_not_treated_as_truncated(sc):
    """A 174-char reply's preview IS the whole reply — don't discard it."""
    contract = {"answer_shape": "conversational", "min_substance": {"words": 3},
                "citation": {"min_distinct_sources": 0}}
    text = ("Hey! All good here — systems running normally, nothing on fire. "
            "It's a warm Saturday evening in Tallinn. What can I help with?")
    out = sc.score_result(contract, {"reply_preview": text,
                                     "reply_chars": len(text), "error": None})
    assert out["verdict"] == sc.PASS
    assert out["reply_truncated"] is False


def test_artifacts_are_still_detected_in_a_truncated_reply(sc):
    """Leakage matches at the START, so a preview is enough to fail it."""
    out = sc.score_result(
        _report_contract(),
        {"reply_preview": "call:web_search{query:Estonian forest cover}",
         "reply_chars": 5000, "error": None},
    )
    assert out["verdict"] == sc.FAIL
    assert "leakage:tool_call_syntax" in out["clauses"]

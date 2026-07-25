"""A crew must not deliver its own scaffolding as an answer.

Applied to EVERY crew, deliberately: every gate fix in this effort had protected
exactly one fork, and the plain `research` crew — which has no evidence gate at
all — delivered three of the four leakage failures on the 2026-07-25 golden set.

The negative fixtures are those actual replies. About half these tests pin what
must still be DELIVERED, because in the serving path a false positive destroys a
genuine answer — a strictly worse outcome than a mislabelled eval row.
"""
import pytest


@pytest.fixture(scope="module")
def oi():
    return pytest.importorskip("app.crews.output_integrity")


def _report(words=900):
    body = " ".join(
        f"Estonia's forest area finding {i} with harvest volumes [S1]."
        for i in range(words // 8)
    )
    return f"# Report\n\n{body}\n\n## Sources\n[S1] https://source.example/a\n"


# ── the shapes actually delivered to the user on 2026-07-25 ──────────────

@pytest.mark.parametrize("reply,expected", [
    ("call:web_search{query:Estonian forest cover changes historical data}",
     "leakage:tool_call_syntax"),
    ("1 validation error for TaskOutput\nraw\n  Input should be a valid string",
     "leakage:validation_error"),
    ("Dossier build failed: OSError: [Errno 36] File name too long: '/app/x'",
     "leakage:traceback"),
    ("--- SubIA Context ---\nloop: compressed\n--- End SubIA Context ---\ntext",
     "leakage:subia_scaffolding"),
    ('```\nThought: The user wants a detailed research report. I need to find:\n1. x\n```',
     "leakage:react_scratchpad"),
    ('```json\n{"title": "Critical Report", "subjects": []}\n```',
     "leakage:raw_json"),
])
def test_catches_the_observed_leakage(oi, reply, expected):
    assert expected in oi.find_artifacts(reply)


def test_describe_gives_an_operator_facing_cause(oi):
    cause = oi.describe(oi.find_artifacts("call:web_search{query:x}"))
    assert "tool-call" in cause
    assert "leakage:" not in cause, "must not leak internal clause names to users"


# ── must NOT fire: a false positive destroys a real answer ───────────────

def test_a_long_real_report_is_untouched(oi):
    assert oi.find_artifacts(_report()) == []


def test_a_report_that_merely_mentions_thought_is_untouched(oi):
    """Ambiguous markers must DOMINATE, not merely appear."""
    reply = _report() + "\n\nThought: this deserves further study.\n"
    assert oi.find_artifacts(reply) == [], (
        "a 900-word report must survive one mid-text 'Thought:'"
    )


def test_the_same_marker_in_a_short_reply_does_fire(oi):
    """Dominance is the discriminator, so a short reply is caught."""
    assert "leakage:react_scratchpad" in oi.find_artifacts(
        "Thought: I should search for that."
    )


def test_scratchpad_at_the_opening_of_a_long_reply_fires(oi):
    reply = "```\nThought: The user wants a report.\n```\n\n" + _report()
    assert "leakage:react_scratchpad" in oi.find_artifacts(reply)


def test_prose_discussing_a_traceback_is_not_a_traceback(oi):
    reply = _report() + "\n\nThe logs showed an error trace worth investigating.\n"
    assert oi.find_artifacts(reply) == []


def test_code_answers_are_untouched(oi):
    code = ('```python\ndef fibonacci(n):\n    """Docstring."""\n    return [0, 1]\n\n'
            'assert fibonacci(2) == [0, 1]\n```')
    assert oi.find_artifacts(code) == []


def test_a_poem_is_untouched(oi):
    poem = ("**Summer Evening**\n\nThe sun forgets to set,\nit only leans —\n"
            "a low gold coin\non the black rim of the pines.\n")
    assert oi.find_artifacts(poem) == []


def test_json_inside_a_larger_answer_is_not_raw_json(oi):
    reply = ("Here is the configuration you asked about:\n\n"
             '```json\n{"a": 1}\n```\n\nIt sets one option.\n')
    assert "leakage:raw_json" not in oi.find_artifacts(reply)


def test_empty_and_none_are_safe(oi):
    assert oi.find_artifacts("") == []
    assert oi.find_artifacts(None) == []


# ── strict mode is what the eval scorer uses ─────────────────────────────

def test_strict_mode_counts_ambiguous_markers_anywhere(oi):
    reply = _report() + "\n\nThought: further study needed.\n"
    assert oi.find_artifacts(reply) == []
    assert "leakage:react_scratchpad" in oi.find_artifacts(reply, strict=True)


# ── the serving path must actually consult it ─────────────────────────────

def test_orchestrator_wires_the_check_and_signals_no_answer():
    """Without this wiring the module is inert — the state every earlier gate
    fix left the non-deep forks in."""
    import inspect
    orch = pytest.importorskip("app.agents.commander.orchestrator")

    source = inspect.getsource(orch)
    assert "output_integrity" in source
    assert "find_artifacts" in source
    # Must convert to a no-answer signal, not rewrite the reply: the
    # orchestrator's short circuit then reports the cause and skips the gates.
    window = source.split("output_integrity")[1][:900]
    assert "record_no_answer" in window


def test_eval_scorer_uses_the_same_definitions():
    """One definition, two consumers — so a suppressed shape and a failed shape
    cannot diverge."""
    from pathlib import Path
    score = Path(__file__).resolve().parents[1] / "evals" / "score.py"
    if not score.exists():  # pragma: no cover
        pytest.skip("evals/score.py not present")
    text = score.read_text()
    assert "output_integrity" in text
    assert "strict=True" in text

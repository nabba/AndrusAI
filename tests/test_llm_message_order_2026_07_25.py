"""A system message must precede an assistant message or end the array.

Every OpenRouter upstream (Azure, Amazon Bedrock, Google, Anthropic) returns the
same 400 for the shape CrewAI's native-tools path can build:

    messages.1: role 'system' must precede an 'assistant' message or end the
    array; the directive-only form (content: [] with output_config) is accepted
    at any position

Live impact on 2026-07-25: the critic crew failed **100% of the time** (all five
golden-set invocations) and failed SILENTLY, because `critic_crew.review()`
catches the exception and returns the crew's original output. 94 such errors in
one afternoon. Diagnosed as D4 on 2026-07-24, deferred, live ever since.

The exact offending index in production was `messages.1`, i.e. an assistant
message at position 0 followed by a system message — pinned below.
"""
import pytest


def _msgs(*roles):
    return [{"role": r, "content": f"{r} content"} for r in roles]


def _roles(messages):
    return [m["role"] for m in messages]


# ── the production shape ─────────────────────────────────────────────────

def test_fixes_the_exact_production_shape_messages_1():
    """assistant at 0, system at 1, more messages after — the reported error."""
    mo = pytest.importorskip("app.llm_message_order")

    fixed, moved = mo.normalize_system_message_order(
        _msgs("assistant", "system", "user")
    )

    assert moved == 1
    assert _roles(fixed) == ["system", "assistant", "user"]


def test_fixes_interleaved_context_summaries():
    """history_compression emits system summaries between topics, so one lands
    after an assistant turn whenever a summarised topic follows an unsummarised
    one."""
    mo = pytest.importorskip("app.llm_message_order")

    fixed, moved = mo.normalize_system_message_order(
        _msgs("system", "user", "assistant", "system", "user", "assistant", "user")
    )

    assert moved == 1
    assert _roles(fixed) == [
        "system", "system", "user", "assistant", "user", "assistant", "user",
    ]


def test_preserves_relative_order_of_multiple_hoisted_messages():
    mo = pytest.importorskip("app.llm_message_order")

    messages = [
        {"role": "assistant", "content": "a"},
        {"role": "system", "content": "first summary"},
        {"role": "user", "content": "u"},
        {"role": "system", "content": "second summary"},
        {"role": "user", "content": "u2"},
    ]
    fixed, moved = mo.normalize_system_message_order(messages)

    assert moved == 2
    assert [m["content"] for m in fixed[:2]] == ["first summary", "second summary"]
    assert _roles(fixed) == ["system", "system", "assistant", "user", "user"]


def test_content_is_never_edited_or_dropped():
    mo = pytest.importorskip("app.llm_message_order")

    messages = _msgs("assistant", "system", "user", "assistant")
    fixed, _ = mo.normalize_system_message_order(messages)

    assert sorted(m["content"] for m in fixed) == sorted(m["content"] for m in messages)
    assert len(fixed) == len(messages)


# ── must NOT touch already-legal arrays ──────────────────────────────────

def test_leading_system_messages_are_left_alone():
    mo = pytest.importorskip("app.llm_message_order")

    messages = _msgs("system", "user", "assistant", "user")
    fixed, moved = mo.normalize_system_message_order(messages)

    assert moved == 0
    assert fixed is messages, "must not rebuild a legal array"


def test_trailing_system_message_is_permitted():
    """The rule explicitly allows a system message that ENDS the array."""
    mo = pytest.importorskip("app.llm_message_order")

    fixed, moved = mo.normalize_system_message_order(
        _msgs("system", "user", "assistant", "system")
    )

    assert moved == 0
    assert _roles(fixed)[-1] == "system"


def test_no_assistant_message_means_system_is_legal_anywhere():
    mo = pytest.importorskip("app.llm_message_order")

    messages = _msgs("user", "system", "user")
    fixed, moved = mo.normalize_system_message_order(messages)

    assert moved == 0
    assert fixed is messages


def test_degenerate_inputs_are_returned_unchanged():
    mo = pytest.importorskip("app.llm_message_order")

    for value in (None, "not a list", [], _msgs("system")):
        fixed, moved = mo.normalize_system_message_order(value)
        assert moved == 0
        assert fixed is value


def test_handles_non_dict_messages_without_raising():
    mo = pytest.importorskip("app.llm_message_order")

    class Msg:
        def __init__(self, role):
            self.role = role

    fixed, moved = mo.normalize_system_message_order(
        [Msg("assistant"), Msg("system"), Msg("user")]
    )
    assert moved == 1
    assert [m.role for m in fixed] == ["system", "assistant", "user"]


# ── the call-args wrapper used by BudgetAwareCompletion ──────────────────

def test_normalize_call_args_handles_positional_messages():
    mo = pytest.importorskip("app.llm_message_order")

    args, kwargs = mo.normalize_call_args(
        (_msgs("assistant", "system", "user"), "extra"), {}, model="m",
    )
    assert _roles(args[0]) == ["system", "assistant", "user"]
    assert args[1] == "extra"


def test_normalize_call_args_handles_keyword_messages():
    mo = pytest.importorskip("app.llm_message_order")

    args, kwargs = mo.normalize_call_args(
        (), {"messages": _msgs("assistant", "system", "user"), "temperature": 0.2},
        model="m",
    )
    assert _roles(kwargs["messages"]) == ["system", "assistant", "user"]
    assert kwargs["temperature"] == 0.2


def test_normalize_call_args_does_not_mutate_the_callers_kwargs():
    mo = pytest.importorskip("app.llm_message_order")

    original = _msgs("assistant", "system", "user")
    kwargs = {"messages": original}
    mo.normalize_call_args((), kwargs, model="m")

    assert kwargs["messages"] is original, "caller's dict must not be mutated"


def test_normalize_call_args_is_failure_soft(monkeypatch):
    """A repair helper must never break a request it was meant to fix."""
    mo = pytest.importorskip("app.llm_message_order")

    def boom(_messages):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(mo, "normalize_system_message_order", boom)
    args, kwargs = mo.normalize_call_args((), {"messages": ["x"]}, model="m")
    assert kwargs["messages"] == ["x"]


# ── wiring: the fix must actually be on the per-call path ────────────────

def test_budget_aware_calls_the_normalizer():
    """Without this wiring the module is inert — the state the critic was in."""
    import inspect
    ba = pytest.importorskip("app.llms.budget_aware")

    source = inspect.getsource(ba)
    assert "_normalize_message_order" in source
    # Must run BEFORE cache-control injection so the hoisted system message is
    # the one considered for cache marking.
    call_body = source.split("def call(")[1].split("def acall(")[0]
    assert call_body.index("_normalize_message_order") < call_body.index("_inject_cache_control")
    acall_body = source.split("async def acall(")[1]
    assert acall_body.index("_normalize_message_order") < acall_body.index("_inject_cache_control")

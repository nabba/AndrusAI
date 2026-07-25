"""The SubIA context block must not become the crew's task.

`orchestrator._consume_pre_task_context` PREPENDS a SubIA context block to every
crew task. `ResearchCrew._extract_core_topic` is supposed to strip injected
context, but its boundary list did not include the SubIA marker — so the "core
topic" was the context block, with the user's question buried after it.

Observed live in `control_plane.crew_tasks` on 2026-07-25, where crew topics read
literally:

    Research: --- SubIA Context ---
    loop: compressed
    scene (2 items…

Downstream consequences, all seen in the same run:
  * research crews returned raw tool-call syntax as the final answer —
    `call:web_search{query:Estonian forest cover changes historical data}`
  * and ReAct scratchpad — ```` ```\nThought: The user wants a detailed... ````
  * the dossier crew built its output filename from the block and crashed:
    `OSError: [Errno 36] File name too long: '…/dossier_subia_context_loop_
    compressed_scene_2_items_0_74_self_assessment_loop_count_70…'`

Present since at least 2026-07-24 (the same shape appears in that day's
`crew_tasks` rows for the creative and delegated-research crews).

The fixtures below use the real injected format from
`app/subia/hooks.py:_build_injection`.
"""
import pytest

_REAL_QUESTION = (
    "make me a report on how Estonian forests have changed over the years"
)

# Exactly the shape _build_injection emits.
_SUBIA_BLOCK = """
--- SubIA Context ---
loop: compressed
scene (2 items):
  - [0.74] self_assessment loop_count=70 last_updated=2026_07_25
  - [0.41] prior dispatch verdict ALLOW
homeostatic-alerts: novelty=+0.12, load=-0.30
prediction: conf=0.62
--- End SubIA Context ---
"""


def _extract(text):
    rc = pytest.importorskip("app.crews.research_crew")
    return rc.ResearchCrew._extract_core_topic(text)


def test_subia_block_is_stripped():
    topic = _extract(f"{_SUBIA_BLOCK}\n\n{_REAL_QUESTION}")

    assert topic == _REAL_QUESTION
    assert "SubIA" not in topic
    assert "loop: compressed" not in topic
    assert "homeostatic" not in topic


def test_stripped_topic_makes_a_sane_filename_slug():
    """Direct guard on the dossier crash: the slug must be short and topical."""
    topic = _extract(f"{_SUBIA_BLOCK}\n\n{_REAL_QUESTION}")

    slug = "".join(c if c.isalnum() else "_" for c in topic.lower())[:120]

    assert "subia" not in slug
    assert "loop_compressed" not in slug
    assert "forest" in slug
    assert len(slug) < 100, "a filename built from this must not overflow"


def test_question_survives_when_the_block_is_the_only_prefix():
    topic = _extract(f"{_SUBIA_BLOCK}{_REAL_QUESTION}")
    assert _REAL_QUESTION in topic
    assert "SubIA" not in topic


def test_plain_task_is_untouched():
    assert _extract(_REAL_QUESTION) == _REAL_QUESTION


def test_block_with_no_task_after_it_does_not_destroy_the_input():
    """Q11 guard: never strip down to nothing."""
    topic = _extract(_SUBIA_BLOCK)

    assert topic.strip(), "stripping must not produce an empty topic"


def test_subia_block_combined_with_other_injected_context():
    """The LAST boundary wins, whichever kind of context came last."""
    text = (
        "<kb_passage>irrelevant retrieved passage</kb_passage>\n"
        f"{_SUBIA_BLOCK}\n\n{_REAL_QUESTION}"
    )
    topic = _extract(text)

    assert topic == _REAL_QUESTION
    assert "kb_passage" not in topic
    assert "SubIA" not in topic


def test_kb_passage_after_subia_still_strips_to_the_question():
    text = (
        f"{_SUBIA_BLOCK}\n"
        "<kb_passage>irrelevant retrieved passage</kb_passage>\n\n"
        f"{_REAL_QUESTION}"
    )
    topic = _extract(text)

    assert topic == _REAL_QUESTION


def test_injection_format_still_matches_what_we_strip():
    """Pins the contract: if _build_injection's marker changes, this fails.

    Without this the strip list could silently drift out of date again — which
    is exactly how the bug survived unnoticed.
    """
    hooks = pytest.importorskip("app.subia.hooks")
    import inspect

    source = inspect.getsource(hooks.SubIALifecycleHooks._build_injection)
    assert '"--- End SubIA Context ---"' in source, (
        "the end marker changed; update _extract_core_topic's boundary list"
    )

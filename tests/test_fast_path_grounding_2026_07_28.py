"""The fast research fork's citations must trace to URLs a tool returned.

Closes the gap ``output_integrity.py`` documents: the plain ``research`` crew is
a bare ``crew.kickoff()`` with no evidence set, so nothing could verify its
citations — while the deep fork's gate caught exactly this failure (a
bibliography padded with real-institution homepages no tool returned; see
reports/GATE_DIAGNOSIS_2026-07-25.md Addendum 3).

Two halves under test: ``app/evidence_capture.py`` (recording — passive) and
``app/crews/grounding.py`` (checking — observe-mode by default, enforcement
behind FAST_PATH_GROUNDING=enforce and a non-empty evidence set).

About half these tests pin what must NOT be flagged: in the serving path a
false positive destroys a genuine answer, which is how the last filter of this
kind got reverted within a day (Addendum 5 — it was fixtured on clean
user-style questions, an input shape production never passes). The fixtures
here use the observed production shapes: markdown replies with a source list,
and the exact org homepages from the 2026-07-25 incident.
"""

from __future__ import annotations

import threading

import pytest

ec = pytest.importorskip("app.evidence_capture")
grounding = pytest.importorskip("app.crews.grounding")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    grounding.reset_stats()
    monkeypatch.delenv("FAST_PATH_GROUNDING", raising=False)
    yield
    grounding.reset_stats()
    try:
        from app.crews.outcome import clear_no_answer
        clear_no_answer()
    except Exception:
        pass


# ── fixtures shaped like the production data ─────────────────────────────

# The reply shape observed live: markdown answer, inline citation, trailing
# source list padded with never-retrieved org homepages (Addendum 3).
_PADDED_REPLY = """\
Estonia's dairy sector produced 858,000 tonnes of raw milk in 2024, up 3.1%
year on year (https://www.stat.ee/en/find-statistics/agriculture).

Key pressures are input costs and consolidation; herd size fell 2% while
per-cow yield rose.

Sources:
- https://www.stat.ee/en/find-statistics/agriculture
- https://piimaliit.ee
- https://ec.europa.eu/eurostat
"""

# What the tools actually returned in that run: one deep link, on-topic.
_RETRIEVED = {"https://www.stat.ee/en/find-statistics/agriculture"}


def _check(reply, *, recorder, task_text="", crew="research"):
    return grounding.enforce_fast_path_grounding(
        crew_name=crew, reply=reply, recorder=recorder, task_text=task_text,
    )


def _recorder_with(*urls):
    rec = ec.EvidenceRecorder()
    for u in urls:
        rec.add(u, origin="search:test")
    return rec


def _consume_no_answer():
    from app.crews.outcome import consume_no_answer
    return consume_no_answer()


# ── evidence recorder ─────────────────────────────────────────────────────

def test_recorder_dedupes_trims_and_rejects_non_http():
    rec = ec.EvidenceRecorder()
    rec.add("https://example.org/page,", origin="search:brave")
    rec.add("https://example.org/page", origin="search:brave")
    rec.add("ftp://example.org/file", origin="search:brave")
    rec.add("not a url", origin="search:brave")
    assert rec.urls() == frozenset({"https://example.org/page"})


def test_recorder_is_bounded_and_flags_truncation():
    rec = ec.EvidenceRecorder(max_urls=3)
    for i in range(10):
        rec.add(f"https://example.org/{i}", origin="x")
    assert len(rec) == 3
    assert rec.truncated is True


def test_extend_from_text_extracts_embedded_urls():
    rec = ec.EvidenceRecorder()
    rec.extend_from_text(
        "See [the report](https://a.example/x) and https://b.example/y.",
        origin="web_fetch",
    )
    assert "https://a.example/x" in rec.urls()
    assert "https://b.example/y" in rec.urls()


def test_recording_without_active_context_is_a_noop():
    assert ec.active_recorder() is None
    # Must not raise, must not create state.
    ec.record_search_results("q", [{"url": "https://x.example"}], "brave")
    ec.record_tool_text("web_fetch", "https://x.example", urls=("https://y.example",))
    assert ec.active_recorder() is None


def test_capture_context_sets_and_resets():
    assert ec.active_recorder() is None
    with ec.capture_evidence() as rec:
        assert ec.active_recorder() is rec
        ec.record_search_results("q", [{"url": "https://x.example/a"}], "brave")
    assert ec.active_recorder() is None
    assert "https://x.example/a" in rec.urls()


def test_propagated_reattaches_parent_recorder_in_worker_thread():
    """run_parallel workers don't inherit ContextVars — re-attachment must
    land tool results in the PARENT's recorder."""
    with ec.capture_evidence() as rec:
        def worker():
            assert ec.active_recorder() is None  # pool threads start bare
            with ec.propagated(rec):
                ec.record_tool_text(
                    "stub", "", urls=("https://from-subagent.example/x",),
                )
        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=10)
    assert "https://from-subagent.example/x" in rec.urls()


def test_propagated_none_is_a_noop_context():
    with ec.propagated(None):
        assert ec.active_recorder() is None


# ── the search hook ───────────────────────────────────────────────────────

def test_search_brave_records_results_while_capturing(monkeypatch):
    ws = pytest.importorskip("app.tools.web_search")
    rows = [{"title": "t", "url": "https://hit.example/deep/page",
             "description": "snippet with https://embedded.example/link"}]
    monkeypatch.setattr(ws, "_search_brave_raw", lambda q, c: list(rows))
    monkeypatch.setattr(ws, "_validate_results",
                        lambda q, res, backend=None: (list(res), 0))
    with ec.capture_evidence() as rec:
        out = ws.search_brave("dairy statistics", 5)
    assert out and out[0]["url"] == "https://hit.example/deep/page"
    assert "https://hit.example/deep/page" in rec.urls()
    assert "https://embedded.example/link" in rec.urls()
    assert rec.origins().get("search:brave", 0) >= 2


def test_fallback_backend_records_too(monkeypatch):
    ws = pytest.importorskip("app.tools.web_search")
    monkeypatch.setattr(ws, "_search_brave_raw", lambda q, c: None)  # quota
    monkeypatch.setattr(
        ws, "_search_searxng",
        lambda q, c: [{"title": "t", "url": "https://sx.example/hit",
                       "description": ""}],
    )
    monkeypatch.setattr(ws, "_validate_results",
                        lambda q, res, backend=None: (list(res), 0))
    with ec.capture_evidence() as rec:
        ws.search_brave("anything", 5)
    assert "https://sx.example/hit" in rec.urls()
    assert rec.origins().get("search:searxng", 0) == 1


# ── the fetch hook ────────────────────────────────────────────────────────

def test_web_fetch_records_fetched_and_embedded_urls(monkeypatch):
    wf = pytest.importorskip("app.tools.web_fetch")

    class _FakeResponse:
        url = "https://site.example/article"
        headers: dict = {}
        raw = None

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            yield (b"<html><body><p>Study text, see "
                   b"<a href='https://linked.example/study'>"
                   b"https://linked.example/study</a></p></body></html>")

        def close(self):
            pass

    monkeypatch.setattr(wf, "_is_safe_url", lambda u: (True, ""))
    monkeypatch.setattr(wf, "_check_response_ip", lambda r: (True, ""))
    monkeypatch.setattr(wf._session, "get",
                        lambda *a, **k: _FakeResponse())

    fetch = getattr(wf.web_fetch, "func", None) or wf.web_fetch.run
    with ec.capture_evidence() as rec:
        text = fetch("https://site.example/article")
    assert "Study text" in text
    assert "https://site.example/article" in rec.urls()
    assert "https://linked.example/study" in rec.urls()


# ── citation extraction and coverage semantics ────────────────────────────

def test_cited_urls_extraction_trims_punctuation():
    cited = grounding.cited_urls(
        "See https://a.example/x, then (https://b.example/y) and "
        "[link](https://c.example/z)."
    )
    assert cited == {"https://a.example/x", "https://b.example/y",
                     "https://c.example/z"}


def test_padded_bibliography_is_flagged():
    """The observed incident shape: two org homepages no tool returned."""
    untraced = grounding.untraced_citations(_PADDED_REPLY, _RETRIEVED)
    assert "https://piimaliit.ee" in untraced
    assert "https://ec.europa.eu/eurostat" in untraced
    assert "https://www.stat.ee/en/find-statistics/agriculture" not in untraced


def test_cited_homepage_is_covered_by_a_retrieved_deep_link():
    """Deep-gate semantics: a retrieved deep link covers its own domain."""
    untraced = grounding.untraced_citations(
        "Per https://keskkonnaagentuur.ee data...",
        {"https://keskkonnaagentuur.ee/en/reports/forest-2025"},
    )
    assert untraced == []


def test_trailing_slash_difference_is_not_fabrication():
    assert grounding.untraced_citations(
        "Source: https://site.example/page/",
        {"https://site.example/page"},
    ) == []


def test_reply_without_urls_is_always_clean():
    rec = _recorder_with()
    report = _check("Estonia has 1.37M people (Statistics Estonia, 2025).",
                    recorder=rec)
    assert report is not None and report.untraced == ()


def test_url_supplied_in_the_task_input_is_allowed():
    """KB-injected or user-supplied links are not fabrication."""
    rec = _recorder_with("https://unrelated.example/a")
    report = _check(
        "As requested, per https://user-gave.example/doc the answer is 42.",
        recorder=rec,
        task_text="Summarize https://user-gave.example/doc for me",
    )
    assert report.untraced == ()


# ── modes ─────────────────────────────────────────────────────────────────

def test_default_mode_is_observe():
    assert grounding.mode() == "observe"


def test_off_mode_skips_entirely(monkeypatch):
    monkeypatch.setenv("FAST_PATH_GROUNDING", "off")
    report = _check(_PADDED_REPLY, recorder=_recorder_with(*_RETRIEVED))
    assert report is None
    assert grounding.stats() == {}


def test_observe_mode_flags_but_never_blocks():
    report = _check(_PADDED_REPLY, recorder=_recorder_with(*_RETRIEVED))
    assert len(report.untraced) == 2
    assert report.enforced is False
    assert _consume_no_answer() is None, "observe mode must not suppress a reply"
    s = grounding.stats()
    assert s["checked"] == 1
    assert s["untraced_replies"] == 1
    assert s["untraced_urls"] == 2
    assert s["observe_only"] == 1


def test_enforce_mode_records_the_no_answer_signal(monkeypatch):
    monkeypatch.setenv("FAST_PATH_GROUNDING", "enforce")
    report = _check(_PADDED_REPLY, recorder=_recorder_with(*_RETRIEVED))
    assert report.enforced is True
    pending = _consume_no_answer()
    assert pending is not None
    assert pending.crew == "research"
    assert "piimaliit.ee" in pending.cause
    assert grounding.stats()["enforced"] == 1


def test_enforce_never_fires_without_captured_evidence(monkeypatch):
    """When no hooked tool returned anything, an untraced citation may just be
    an un-hooked source (memory, KB search) — that ambiguity must not destroy
    a real answer."""
    monkeypatch.setenv("FAST_PATH_GROUNDING", "enforce")
    report = _check(_PADDED_REPLY, recorder=ec.EvidenceRecorder())
    assert report.enforced is False
    assert report.skipped == "no captured evidence"
    assert _consume_no_answer() is None
    assert grounding.stats()["enforce_skipped_no_evidence"] == 1


def test_clean_reply_in_enforce_mode_is_untouched(monkeypatch):
    monkeypatch.setenv("FAST_PATH_GROUNDING", "enforce")
    reply = ("Milk output rose 3.1% "
             "(https://www.stat.ee/en/find-statistics/agriculture).")
    report = _check(reply, recorder=_recorder_with(*_RETRIEVED))
    assert report.untraced == ()
    assert _consume_no_answer() is None


def test_only_the_research_fork_is_checked():
    for crew in ("deep_research", "writing", "creative", "pim"):
        assert _check(_PADDED_REPLY,
                      recorder=_recorder_with(*_RETRIEVED),
                      crew=crew) is None
    assert grounding.stats() == {}


def test_invalid_mode_value_degrades_to_observe(monkeypatch):
    monkeypatch.setenv("FAST_PATH_GROUNDING", "block-everything")
    assert grounding.mode() == "observe"


# ── wiring: without these, both modules are inert ─────────────────────────

def test_orchestrator_wires_capture_and_check():
    """Source-level assertion (the llm_message_order lesson: an unwired module
    is exactly as useful as no module). Import-free so a config-less
    environment can still run it."""
    from pathlib import Path
    src = Path("app/agents/commander/orchestrator.py").read_text()
    inner = src[src.index("def _run_crew_inner"):]
    inner = inner[:inner.index("\n    def ", 10)]
    assert "capture_evidence()" in inner
    assert "enforce_fast_path_grounding(" in inner
    # The grounding cause must not overwrite a leakage cause.
    assert inner.index("find_artifacts(") < inner.index(
        "enforce_fast_path_grounding(")


def test_research_crew_subagents_report_into_the_parent_recorder(monkeypatch):
    """Behavioral: _run_parallel must re-attach the request recorder inside
    its pool threads. Fails if the `propagated(recorder)` wiring is removed."""
    rc_mod = pytest.importorskip("app.crews.research_crew")

    captured_in_worker = []

    class _StubCrew:
        def __init__(self, *a, **k):
            pass

        def kickoff(self):
            # What a sub-agent's tool call would do:
            ec.record_tool_text(
                "stub", "", urls=("https://from-subagent.example/data",),
            )
            captured_in_worker.append(ec.active_recorder() is not None)
            return "findings: https://from-subagent.example/data"

    class _Result:
        def __init__(self, label, value):
            self.label, self.result, self.success = label, value, True

    def _fake_run_parallel(tasks):
        results = []

        def _runner(label, fn):
            results.append(_Result(label, fn()))

        threads = [threading.Thread(target=_runner, args=(lbl, fn))
                   for lbl, fn in tasks]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        return results

    monkeypatch.setattr(rc_mod, "Crew", _StubCrew)
    monkeypatch.setattr(rc_mod, "Task", lambda **k: None)
    monkeypatch.setattr(rc_mod, "create_researcher", lambda **k: object())
    monkeypatch.setattr(rc_mod, "run_parallel", _fake_run_parallel)
    monkeypatch.setattr(rc_mod, "crew_started", lambda *a, **k: "tid")
    monkeypatch.setattr(rc_mod, "crew_completed", lambda *a, **k: None)
    monkeypatch.setattr(rc_mod, "crew_failed", lambda *a, **k: None)
    monkeypatch.setattr(rc_mod, "update_sub_agent_progress", lambda *a, **k: None)
    monkeypatch.setattr(rc_mod, "estimate_eta", lambda *a, **k: 60)
    monkeypatch.setattr(rc_mod.ResearchCrew, "_synthesize",
                        lambda self, topic, results, pid: "synthesized")

    crew = rc_mod.ResearchCrew()
    with ec.capture_evidence() as rec:
        out = crew._run_parallel("topic", ["sub a", "sub b"], "parent-tid")

    assert out == "synthesized"
    assert captured_in_worker and all(captured_in_worker)
    assert "https://from-subagent.example/data" in rec.urls()

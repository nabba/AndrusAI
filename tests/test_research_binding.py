"""Tests for app.research.binding (Phase D — research run ↔ Thread bridge).

Phase D binds each research run to a Thread so the system's existing
cross-run-learning machinery fires for free:

  * ``create_thread`` runs ``consult_before_create`` (dedup against past
    closures at *creation* time).
  * ``resolve_thread`` / ``abandon_thread`` run ``distill_on_closure`` (the
    approaches-tried capture written back to ``lessons_learned`` at *closure*
    time).

The bridge owns no learning machinery — it is a thin, idempotent,
failure-isolated link. Three properties carry the design and are pinned here:

  * **Idempotent** — bind twice → one thread; close twice → second call is a
    no-op (not an ``InvalidThreadTransition``).
  * **State-faithful** — COMPLETED→RESOLVED, FAILED/BUDGET_EXHAUSTED/ABORTED→
    ABANDONED, BLOCKED→BLOCKED (stays open), in-flight→no-op.
  * **Host-safe** — the threads lifecycle wraps its KB/affect hooks in
    try/except, the store is redirected to a tmp dir via ``reset_for_tests``,
    and binding lazy-imports everything heavy. No LLM / ChromaDB / crewai.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

import app.research.binding as B
import app.research.run as R
from app.autonomous_executor.driver import CommanderResult
from app.autonomous_executor.models import Budget, ExecutorRun, ExecutorStatus
from app.threads import store as thread_store
from app.threads.models import ThreadStatus


# ── Thread-store isolation (host-safe; redirect to tmp) ───────────────────────


@pytest.fixture(autouse=True)
def _isolate_thread_store(tmp_path):
    thread_store.reset_for_tests(tmp_path / "threads")
    yield
    thread_store.reset_for_tests(None)


# ── Test doubles (mirror tests/test_research_dossier.py) ──────────────────────


@dataclass
class _Hit:
    id: str
    title: str = ""
    text: str = ""
    source: str = "kb"
    published: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "source": self.source,
            "published": self.published,
        }


@dataclass
class _Hyp:
    text: str
    rank: int = 1
    novelty: str = ""

    def to_dict(self) -> dict:
        return {"text": self.text, "rank": self.rank, "novelty": self.novelty}


def _make_seams(*, hits=(), hyps=(), gate=(None, ""), investigate_text="INV", draft_text="DRAFT"):
    def search_fn(goal):
        return list(hits)

    def propose_fn(question, *, literature=None, **kw):
        return list(hyps)

    def commander_fn(step, run):
        return CommanderResult(
            text=investigate_text if step.crew_hint == R.HINT_INVESTIGATE else draft_text
        )

    def gate_fn(*, proposal_text, task_id, verdict):
        return gate

    return dict(
        search_fn=search_fn,
        propose_fn=propose_fn,
        commander_fn=commander_fn,
        gate_fn=gate_fn,
    )


# ── Run builders ──────────────────────────────────────────────────────────────


def _make_run(goal="does caching cut retrieval latency") -> ExecutorRun:
    """A bare CREATED run — gives the close tests full control over status
    without coupling to the adapter/driver."""
    return ExecutorRun(
        run_id=f"research-{uuid.uuid4().hex[:12]}",
        goal=goal,
        requestor="research",
        zone="autonomous",
        budget=Budget(),
    )


def _bound_run(goal="does caching cut retrieval latency") -> ExecutorRun:
    run = _make_run(goal)
    B.bind_run_to_thread(run)
    return run


def _drive_to(run: ExecutorRun, status: ExecutorStatus, *, reason: str = "") -> ExecutorRun:
    """Drive a run to a terminal/blocked status via legal transitions.

    PLANNING → RUNNING → <target> covers every status the close hook acts on
    (COMPLETED / FAILED / BUDGET_EXHAUSTED / ABORTED / BLOCKED). RUNNING is
    handled inline by callers that need the in-flight case (self-transition is
    rejected by the state machine, so it cannot be a ``_drive_to`` target).
    """
    if run.status is ExecutorStatus.CREATED:
        run.transition(ExecutorStatus.PLANNING)
    if run.status is ExecutorStatus.PLANNING:
        run.transition(ExecutorStatus.RUNNING)
    run.transition(status, reason=reason)
    return run


# ── thread_id_for_run ─────────────────────────────────────────────────────────


def test_thread_id_none_when_unbound():
    assert B.thread_id_for_run(_make_run()) is None


def test_thread_id_reads_through_timestamp_prefix():
    # record_note prefixes "[<iso-ts>] " — the marker must be found by
    # substring search, not a prefix match.
    run = _make_run()
    run.record_note("research-thread:abc-123")
    assert B.thread_id_for_run(run) == "abc-123"


def test_thread_id_returns_newest_when_double_bound():
    run = _make_run()
    run.record_note("research-thread:first")
    run.record_note("research-thread:second")
    assert B.thread_id_for_run(run) == "second"


# ── bind_run_to_thread ──────────────────────────────────────────────────────


def test_bind_creates_thread_and_records_backpointer():
    run = _make_run(goal="does caching cut latency")
    tid = B.bind_run_to_thread(run)
    assert tid is not None
    assert B.thread_id_for_run(run) == tid

    thread = thread_store.get(tid)
    assert thread is not None
    assert thread.title == "Research: does caching cut latency"
    # the run_id is cross-linked as a related inquiry slug
    assert run.run_id in thread.related_inquiry_slugs
    # a freshly-bound thread is OPEN (link_inquiry does not advance it)
    assert thread.status is ThreadStatus.OPEN


def test_bind_is_idempotent():
    run = _make_run()
    tid1 = B.bind_run_to_thread(run)
    tid2 = B.bind_run_to_thread(run)
    assert tid1 == tid2
    # the second bind returns the existing id without creating a new thread
    assert len(thread_store.list_all()) == 1


def test_bind_truncates_long_title():
    run = _make_run(goal="x" * 300)
    tid = B.bind_run_to_thread(run)
    thread = thread_store.get(tid)
    assert len(thread.title) <= 120
    assert thread.title.startswith("Research: ")
    assert thread.title.endswith("...")


# ── close_thread_for_run: in-flight no-op ──────────────────────────────────


def test_close_noop_while_running_thread_stays_open():
    run = _bound_run()
    tid = B.thread_id_for_run(run)
    run.transition(ExecutorStatus.PLANNING)
    run.transition(ExecutorStatus.RUNNING)
    # run still in flight → close is a no-op and the thread stays OPEN
    assert B.close_thread_for_run(run) is None
    assert thread_store.get(tid).status is ThreadStatus.OPEN


# ── close_thread_for_run: terminal mapping ──────────────────────────────────


def test_close_completed_resolves_thread():
    run = _bound_run()
    tid = B.thread_id_for_run(run)
    _drive_to(run, ExecutorStatus.COMPLETED)
    assert B.close_thread_for_run(run) == tid

    thread = thread_store.get(tid)
    assert thread.status is ThreadStatus.RESOLVED
    # the resolution carries the research summary line
    assert any("Research run" in n for n in thread.notes)


@pytest.mark.parametrize(
    "status",
    [
        ExecutorStatus.FAILED,
        ExecutorStatus.BUDGET_EXHAUSTED,
        ExecutorStatus.ABORTED,
    ],
)
def test_close_failure_states_abandon_thread(status):
    run = _bound_run()
    tid = B.thread_id_for_run(run)
    _drive_to(run, status, reason="something went wrong")
    assert B.close_thread_for_run(run) == tid

    thread = thread_store.get(tid)
    assert thread.status is ThreadStatus.ABANDONED
    assert thread.abandon_reason  # abandon_thread refuses an empty reason


def test_close_failed_without_reason_uses_fallback():
    # transition() fills failure_reason with "unspecified"; close must still
    # hand abandon_thread a non-empty reason.
    run = _bound_run()
    tid = B.thread_id_for_run(run)
    _drive_to(run, ExecutorStatus.FAILED)
    assert B.close_thread_for_run(run) == tid
    assert thread_store.get(tid).status is ThreadStatus.ABANDONED


# ── close_thread_for_run: BLOCKED stays open ────────────────────────────────


def test_close_blocked_marks_thread_blocked_not_terminal():
    run = _bound_run()
    tid = B.thread_id_for_run(run)
    _drive_to(run, ExecutorStatus.BLOCKED, reason="gate escalated")
    assert B.close_thread_for_run(run) == tid

    thread = thread_store.get(tid)
    assert thread.status is ThreadStatus.BLOCKED
    assert not thread.is_terminal


def test_close_blocked_is_noop_when_thread_already_blocked():
    run = _bound_run()
    tid = B.thread_id_for_run(run)
    _drive_to(run, ExecutorStatus.BLOCKED, reason="first block")
    B.close_thread_for_run(run)
    before = len(thread_store.get(tid).blockers)
    # a second tick must not re-append the blocker
    assert B.close_thread_for_run(run) == tid
    assert len(thread_store.get(tid).blockers) == before


# ── close_thread_for_run: idempotency on terminal thread ────────────────────


def test_close_idempotent_on_already_resolved_thread():
    run = _bound_run()
    tid = B.thread_id_for_run(run)
    _drive_to(run, ExecutorStatus.COMPLETED)
    B.close_thread_for_run(run)
    assert thread_store.get(tid).status is ThreadStatus.RESOLVED
    # the load-bearing guard: a second close on a terminal thread returns the
    # id rather than raising InvalidThreadTransition.
    assert B.close_thread_for_run(run) == tid
    assert thread_store.get(tid).status is ThreadStatus.RESOLVED


# ── close_thread_for_run: edge cases ────────────────────────────────────────


def test_close_unbound_run_returns_none():
    run = _make_run()
    _drive_to(run, ExecutorStatus.COMPLETED)
    assert B.close_thread_for_run(run) is None


def test_close_honors_explicit_thread_id():
    bound = _bound_run()
    tid = B.thread_id_for_run(bound)
    other = _make_run()  # unbound, but we pass the id explicitly
    _drive_to(other, ExecutorStatus.COMPLETED)
    assert B.close_thread_for_run(other, thread_id=tid) == tid
    assert thread_store.get(tid).status is ThreadStatus.RESOLVED


def test_close_missing_thread_returns_none():
    run = _make_run()
    run.record_note("research-thread:nonexistent-id")
    _drive_to(run, ExecutorStatus.COMPLETED)
    assert B.close_thread_for_run(run) is None


# ── End-to-end through run_to_completion(bind_thread=True) ──────────────────


def test_e2e_bind_thread_completed_resolves():
    run = R.build_research_run("does caching cut retrieval latency")
    R.run_to_completion(
        run,
        adapter=R.make_research_adapter(
            **_make_seams(
                hits=[_Hit(id="a", title="t1")],
                hyps=[_Hyp(text="caching cuts latency")],
                gate=(None, "grounded"),
            )
        ),
        bind_thread=True,
    )
    assert run.status is ExecutorStatus.COMPLETED
    tid = B.thread_id_for_run(run)
    assert tid is not None
    thread = thread_store.get(tid)
    assert thread.status is ThreadStatus.RESOLVED
    assert any("Research run" in n for n in thread.notes)


def test_e2e_bind_thread_gate_escalation_blocks():
    run = R.build_research_run("does caching cut retrieval latency")
    R.run_to_completion(
        run,
        adapter=R.make_research_adapter(**_make_seams(gate=("peer_review", "uncited claim"))),
        bind_thread=True,
    )
    assert run.status is ExecutorStatus.BLOCKED
    thread = thread_store.get(B.thread_id_for_run(run))
    assert thread.status is ThreadStatus.BLOCKED


def test_e2e_without_bind_thread_creates_no_thread():
    run = R.build_research_run("does caching cut retrieval latency")
    R.run_to_completion(
        run,
        adapter=R.make_research_adapter(**_make_seams(gate=(None, "ok"))),
    )
    assert run.status is ExecutorStatus.COMPLETED
    # the synchronous path is opt-in — no thread without bind_thread=True
    assert thread_store.list_all() == []


# ── Module wiring ─────────────────────────────────────────────────────────────


def test_module_exports():
    assert set(B.__all__) == {
        "bind_run_to_thread",
        "thread_id_for_run",
        "close_thread_for_run",
    }

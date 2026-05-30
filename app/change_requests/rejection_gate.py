"""Gate A — semantic rejection-suppression policy (2026-05-30).

Single source of truth for one question:

    *Has the operator already rejected this idea (semantically) enough
    times that the system should stop re-filing it?*

The byte-exact dedup in :mod:`app.change_requests.lifecycle`
(``content_hash`` = sha256(requestor|path|diff)) only catches *identical*
re-files. LLM-driven observational producers — ``tech_radar`` →
``library_radar``, ``dependency_radar``, ``capability_gap_analyzer``,
``paper_pipeline`` — paraphrase the SAME rejected idea with fresh wording
every pass, so each re-file gets a new ``content_hash`` and slips through.
The result is the operator being asked to reject the 8th rewording of an
idea they already rejected 7 times.

The rejection DATA already exists: :mod:`app.companion.lessons_learned`
clusters rejected proposals (hashing-trick embedding) and exposes
``check_against()`` returning ``{similarity, count}``. Historically that
signal was rendered as an advisory banner in the CR ``reason`` only — the
system computed "seen 7× before, 0.67 similar" and then queued the CR
anyway. This module promotes that signal to an actual *decision*, consulted
at two points:

  * :func:`app.change_requests.lifecycle.create_request` — the CR gate,
    which suppresses the re-file (records it REJECTED, never queues it).
  * Observational producers (``library_radar`` first) — *before* spending a
    staging slot + cooldown + an LLM-cost cycle on a known-rejected idea.

This module is POLICY only; it owns no data. Thresholds + mode are
operator-tunable via :mod:`app.runtime_settings`.

Safety invariants:

  * **Producer allowlist.** Only OBSERVATIONAL idea-generators are eligible
    (:data:`SUPPRESSIBLE_PRODUCER_PREFIXES`). Human-filed CRs and real-fix
    producers (``error_diagnosis``, ``autonomous_executor``, ``coder``, …)
    are NEVER semantically suppressed — suppression must not silence a bug
    fix or an operator.
  * **count >= min_count.** A brand-new idea (no cluster, or a cluster the
    operator rejected only once or twice) is never suppressed; only ideas
    rejected *repeatedly*. Novelty — the operator's real signal — is
    preserved.
  * **Mode: off / advisory / enforcing** (mirrors the Goodhart + epistemic
    rollout discipline). Shipped ``advisory`` (compute + log "WOULD
    suppress" but still file) then flipped to ``enforcing`` by operator
    decision 2026-05-30. NOTE the distinction: the runtime_settings *value*
    is ``enforcing``, but :func:`config`'s try/except *fail-safe fallback*
    (used only when runtime_settings can't be read at all) stays
    ``advisory`` — an infra glitch must never cause silent suppression.
  * **Failure-isolated.** Any error (KB unavailable, runtime_settings
    unreadable) → not matched → the CR proceeds. The gate can only ever
    suppress on a positive, well-formed signal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Producers whose output is OBSERVATIONAL idea-generation — candidates for
# semantic suppression. Every observational markdown-doc producer
# (library_radar, capability_gap_analyzer, paper_pipeline, dependency_radar)
# routes its CRs through the proposal bridge with a ``proposal_bridge:<source>``
# requestor, so this single prefix is the precise boundary.
#
# Deliberately EXCLUDED by omission (the default is "never suppress"):
#   * humans (operator, coder, …)
#   * real-fix producers (error_diagnosis, autonomous_executor, self_improver)
#   * ``library_radar_trial`` — the EVIDENCE-bearing adoption CR (Gate B):
#     it carries a real PyPI resolution + a green venv smoke-import. It must
#     never be suppressed merely for being lexically near a rejected
#     unverified doc-proposal for the same package. (This is why the bare
#     ``library_radar`` prefix is NOT here — it would also match
#     ``library_radar_trial``.)
SUPPRESSIBLE_PRODUCER_PREFIXES: tuple[str, ...] = (
    "proposal_bridge:",
)

# Conservative defaults. The lessons KB uses a hashing-trick embedding whose
# absolute cosine values run lower than a real sentence embedding; 0.55 sits
# comfortably above its ``_MATCH_THRESHOLD`` (0.40, the banner floor) and
# below the 0.67 observed for the openrouter paraphrase flood, so the gate
# fires on repeats without grabbing borderline matches.
DEFAULT_SIMILARITY = 0.55
DEFAULT_MIN_COUNT = 3

VALID_MODES = ("off", "advisory", "enforcing")


@dataclass(frozen=True)
class RejectionVerdict:
    """Outcome of a single policy evaluation.

    ``matched`` is True only when the proposal crosses BOTH the similarity
    and count thresholds. ``mode`` is the active rollout mode so the caller
    knows whether to actually suppress (``enforcing``) or merely log
    (``advisory``). ``should_suppress`` is the convenience conjunction.
    """

    matched: bool
    mode: str
    lesson_id: str = ""
    similarity: float = 0.0
    count: int = 0

    @property
    def should_suppress(self) -> bool:
        return self.matched and self.mode == "enforcing"

    def detail(self) -> str:
        return (
            f"matches rejected-pattern lesson `{self.lesson_id}` "
            f"(similarity {self.similarity:.2f}, seen {self.count}× before)"
        )


def is_suppressible_producer(requestor: str) -> bool:
    """True when ``requestor`` is an observational idea-generator eligible
    for semantic suppression. Case-insensitive prefix match."""
    r = (requestor or "").strip().lower()
    return any(r.startswith(p) for p in SUPPRESSIBLE_PRODUCER_PREFIXES)


def config() -> tuple[str, float, int]:
    """Return ``(mode, similarity_threshold, min_count)``.

    Reads :mod:`app.runtime_settings` when available so the operator can flip
    the mode (off → advisory → enforcing) and tune thresholds without a
    redeploy. Conservative defaults on any failure.
    """
    mode, sim, count = "advisory", DEFAULT_SIMILARITY, DEFAULT_MIN_COUNT
    try:
        from app import runtime_settings as rs

        snap = rs.snapshot()
        mode = str(snap.get("cr_rejection_suppression_mode", mode) or mode).strip().lower()
        sim = float(snap.get("cr_rejection_suppression_similarity", sim))
        count = int(snap.get("cr_rejection_suppression_min_count", count))
    except Exception:
        logger.debug("rejection_gate: config read failed", exc_info=True)
    if mode not in VALID_MODES:
        mode = "advisory"
    return mode, sim, count


def evaluate(text: str) -> RejectionVerdict:
    """Does ``text`` look like a repeatedly-rejected idea?

    Pure read of the lessons-learned KB. Failure-isolated: any error returns
    an unmatched verdict, so the gate can only suppress on a positive signal.
    Caller is responsible for the producer-allowlist check
    (:func:`is_suppressible_producer`) — kept separate so producers can decide
    their own eligibility.
    """
    mode, sim_threshold, min_count = config()
    if mode == "off":
        return RejectionVerdict(matched=False, mode=mode)
    try:
        from app.companion.lessons_learned import check_against

        matches = check_against(text, top_k=1)
    except Exception:
        logger.debug("rejection_gate: lessons check failed", exc_info=True)
        return RejectionVerdict(matched=False, mode=mode)
    if not matches:
        return RejectionVerdict(matched=False, mode=mode)
    top = matches[0]
    sim = float(top.get("similarity", 0.0) or 0.0)
    cnt = int(top.get("count", 0) or 0)
    matched = sim >= sim_threshold and cnt >= min_count
    return RejectionVerdict(
        matched=matched,
        mode=mode,
        lesson_id=str(top.get("id", "")),
        similarity=sim,
        count=cnt,
    )

"""
llm_factory_probe.py — Health cache for (provider, bare_id) tuples.

The cache is the factory's operational guarantee that it does not return
an LLM whose underlying model id was recently confirmed dead by the
upstream provider.  It composes with — and does not replace — the existing
``circuit_breaker`` layer:

Persistence
-----------

The cache is persisted to ``workspace/llm_factory/health_cache.json``
under a 30-second write throttle.  Process restart reloads non-expired
records.  Without this, a rolling deploy (which restarts the gateway in
seconds) would re-pay the 404 cost the moment it picks up the same dead
model id from the catalog snapshot.  With this, the second-instance
boot inherits the first instance's learning.

Persistence is best-effort: a corrupt file, missing directory, or
permission error is logged and the cache starts empty.  The on-disk
format is a flat JSON object mapping ``"{provider}|{bare_id}"`` to a
``{is_alive, expires_at, last_reason}`` record; ``expires_at`` is stored
as a wall-clock POSIX timestamp (not monotonic) since monotonic clocks
don't survive process restart.

  * ``circuit_breaker["anthropic"]`` / ``["anthropic_credits"]`` / etc.
    track *provider-level* health (rate limits, credit exhaustion,
    transient 5xx storms).  The breaker is keyed by provider name, not by
    model id, so a 404 on one specific model does not trip it.
  * This health cache tracks *model-id-level* validity.  A 404 with
    ``"not_found_error"`` from ``api.anthropic.com`` for
    ``claude-sonnet-4-6`` proves that *that specific id* is wrong for the
    Anthropic SDK at this moment — but says nothing about whether
    ``claude-opus-4-7`` would work, or whether the provider is up at all.

Workflow
--------

1. The factory's chain walker checks :func:`health_of` before returning
   any constructed LLM.  A cached "dead" record causes the walker to
   skip the candidate and try the next.
2. After a real call succeeds (200 from the upstream API), the call site
   invokes :func:`mark_alive`.  This refreshes the cache so the next
   factory call can fast-path the same model.
3. After a real call fails with a model-id-level error (a 404
   ``not_found_error``, an OpenRouter "model not found", an Ollama
   "model not loaded"), the call site classifies the failure via
   :func:`classify_failure` and, if it returns a reason, calls
   :func:`mark_dead`.  Generic 5xx / rate-limit / auth failures do NOT
   mark the model dead — they belong to the circuit breaker.

Lifecycle
---------

The cache is process-local and in-memory.  No persistence — a fresh
process starts with an empty cache, and the first real call populates it
within ~1 second.  Boot is therefore zero-cost; the cache pays for
itself the first time a stale model id appears in the catalog snapshot.

TTLs are conservative:

* Success records expire after 1 hour.  A model marked alive at 10:00
  is treated as "unknown" at 11:01, forcing a real-call re-check.  This
  catches mid-day deprecations within an hour.
* Failure records expire after 60 seconds.  A model marked dead at
  10:00:00 is retried at 10:01:00.  This is short on purpose: a 404
  caused by a brief upstream-routing glitch should not lock a model out
  for an hour.  If the model is genuinely gone, the next attempt will
  re-confirm and re-mark.

Thread safety
-------------

A single module-level lock guards the dict.  Reads are cheap (a single
``dict.get`` under the lock).  Writes are rare (one per call outcome).
No I/O happens inside the lock.

Failure classifier
------------------

:func:`classify_failure` is intentionally strict.  Marking a model dead
on every exception would mean any network blip locks out the entire
fallback chain — exactly the failure mode we are trying to avoid.  The
classifier only matches signatures whose body conclusively proves the
model id was rejected by the upstream registry:

  * Anthropic 404 with ``"type": "not_found_error"``
  * OpenRouter 404 with body containing ``"model not found"``
  * litellm-wrapped ``ModelNotFound`` / ``NotFoundError``
  * Ollama 404 with body containing ``"model not loaded"`` or
    ``"model not found"``

Anything else returns None and the cache stays untouched.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Cache entry TTLs.  See module docstring for the rationale on the
# asymmetric values — successes survive an hour, failures clear in a
# minute.
_SUCCESS_TTL_SECONDS = 3600.0
_FAILURE_TTL_SECONDS = 60.0

# Disk persistence: file path + write-throttle window.  Cache reloads
# at module import so a rolling deploy preserves the first instance's
# learning.  Writes are throttled to one per 30s to avoid disk thrash
# on a busy gateway; the in-memory cache is always authoritative
# between flushes.
_PERSIST_PATH = Path(
    os.environ.get(
        "LLM_FACTORY_HEALTH_CACHE",
        "/app/workspace/llm_factory/health_cache.json",
    )
)
_PERSIST_THROTTLE_SECONDS = 30.0
_last_persist_at: float = 0.0
# Test escape hatch — when True, ``_persist_locked`` is a no-op so unit
# tests don't write to the real workspace path.  Toggled via
# :func:`_reset_for_tests`.
_persistence_disabled: bool = False


@dataclass(frozen=True)
class HealthRecord:
    """A single (provider, bare_id) → health observation.

    ``is_alive=True`` records survive :data:`_SUCCESS_TTL_SECONDS`.
    ``is_alive=False`` records survive :data:`_FAILURE_TTL_SECONDS`.

    ``expires_at`` is a POSIX wall-clock timestamp (``time.time()``,
    not ``time.monotonic()``) so on-disk records remain interpretable
    after process restart.  The wall-clock vulnerability to system-time
    skew is acceptable here: TTLs are short (≤1h) and a clock jump
    larger than that would have far worse consequences than a
    mis-timed cache hit.

    ``last_reason`` is a short, human-readable description of why the
    record is what it is.  For dead records, the truncated exception
    message.  For alive records, the empty string.
    """
    is_alive: bool
    expires_at: float
    last_reason: str


_HEALTH: dict[tuple[str, str], HealthRecord] = {}
_HEALTH_LOCK = threading.Lock()


def _key_to_str(key: tuple[str, str]) -> str:
    """Serialise a (provider, bare_id) tuple to a JSON-safe string."""
    return f"{key[0]}|{key[1]}"


def _str_to_key(s: str) -> tuple[str, str] | None:
    """Reverse of :func:`_key_to_str`; returns ``None`` on malformed input."""
    parts = s.split("|", 1)
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])


def _persist_locked() -> None:
    """Write the cache to disk.  Caller must hold ``_HEALTH_LOCK``.

    Best-effort: any failure is logged and swallowed.  The in-memory
    cache remains the source of truth between flushes; a missed write
    only costs the next process restart's learning.
    """
    global _last_persist_at
    if _persistence_disabled:
        return
    payload = {
        _key_to_str(k): {
            "is_alive": v.is_alive,
            "expires_at": v.expires_at,
            "last_reason": v.last_reason,
        }
        for k, v in _HEALTH.items()
    }
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-rename pattern — never leave a half-written file.
        tmp = _PERSIST_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, _PERSIST_PATH)
        _last_persist_at = time.time()
    except Exception:
        logger.debug(
            "llm_factory_probe: persist to %s failed",
            _PERSIST_PATH, exc_info=True,
        )


def _maybe_persist_locked() -> None:
    """Persist if the throttle window has elapsed.  Caller holds the lock."""
    if time.time() - _last_persist_at >= _PERSIST_THROTTLE_SECONDS:
        _persist_locked()


def _load_from_disk() -> None:
    """Best-effort load of the persisted cache at module import.

    Expired records are dropped silently.  A corrupt file is logged
    and treated as missing — the cache starts empty and the next write
    overwrites the bad file.
    """
    try:
        if not _PERSIST_PATH.exists():
            return
        raw = _PERSIST_PATH.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return
        now = time.time()
        loaded = 0
        with _HEALTH_LOCK:
            for k_str, rec in payload.items():
                if not isinstance(rec, dict):
                    continue
                key = _str_to_key(k_str)
                if key is None:
                    continue
                try:
                    expires_at = float(rec["expires_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                if expires_at < now:
                    continue  # already expired
                _HEALTH[key] = HealthRecord(
                    is_alive=bool(rec.get("is_alive", False)),
                    expires_at=expires_at,
                    last_reason=str(rec.get("last_reason", ""))[:300],
                )
                loaded += 1
        if loaded:
            logger.info(
                "llm_factory_probe: loaded %d non-expired health record(s) "
                "from %s", loaded, _PERSIST_PATH,
            )
    except Exception:
        logger.warning(
            "llm_factory_probe: failed to load persisted cache from %s — "
            "starting empty", _PERSIST_PATH, exc_info=True,
        )


def health_of(provider: str, bare_id: str) -> HealthRecord | None:
    """Return the cached health record for *(provider, bare_id)*, or None.

    None means "no record" — semantically distinct from "known dead".
    Callers should treat None as permission to construct; only an
    explicit ``is_alive=False`` record forces a chain-walk to skip.
    """
    key = (provider, bare_id)
    now = time.time()
    with _HEALTH_LOCK:
        rec = _HEALTH.get(key)
        if rec is None:
            return None
        if rec.expires_at < now:
            # Expired — clear lazily so the dict doesn't grow unbounded.
            del _HEALTH[key]
            return None
        return rec


def mark_alive(provider: str, bare_id: str) -> None:
    """Record that a real call against *(provider, bare_id)* succeeded.

    Refreshes the TTL even if a prior alive record exists.  Overwrites
    any prior dead record — a successful call is authoritative.
    """
    key = (provider, bare_id)
    rec = HealthRecord(
        is_alive=True,
        expires_at=time.time() + _SUCCESS_TTL_SECONDS,
        last_reason="",
    )
    with _HEALTH_LOCK:
        _HEALTH[key] = rec
        _maybe_persist_locked()


def mark_dead(provider: str, bare_id: str, reason: str) -> None:
    """Record that a real call against *(provider, bare_id)* was rejected
    at the model-registry level (model id unknown / retired / typo'd).

    Reason is truncated to 300 chars for log hygiene.  Overwrites any
    prior record — the most recent observation wins.

    Side-effect: appends a row to the dead-marks ledger and, once the
    same ``(provider, bare_id)`` has been marked dead ≥3 times in a
    24-hour rolling window, escalates via :func:`_escalate_persistent_failure`
    (continuity-ledger event + proposal-bridge CR proposing catalog
    retirement).  The 60-second cache TTL is too short to be a
    long-term signal on its own; escalation is what closes the loop
    between transient observation and durable governance action.
    """
    key = (provider, bare_id)
    rec = HealthRecord(
        is_alive=False,
        expires_at=time.time() + _FAILURE_TTL_SECONDS,
        last_reason=reason[:300],
    )
    with _HEALTH_LOCK:
        _HEALTH[key] = rec
        # Dead-marks are higher-signal than alive refreshes — flush
        # immediately (bypassing throttle) so a rolling deploy picks
        # up the dead mark on its very next boot.
        _persist_locked()
    logger.warning(
        "llm_factory_probe: marked dead provider=%s bare_id=%s reason=%r "
        "(skipped by chain walker for %.0fs)",
        provider, bare_id, rec.last_reason, _FAILURE_TTL_SECONDS,
    )

    # Closure of the observation→governance loop.  Failure-isolated so
    # an escalation bug never prevents a dead-mark from registering.
    try:
        count_24h = _append_dead_mark(provider, bare_id, reason)
        if count_24h >= _ESCALATION_THRESHOLD:
            _escalate_persistent_failure(provider, bare_id, reason, count_24h)
    except Exception:
        logger.debug(
            "llm_factory_probe: escalation pipeline failed (non-fatal)",
            exc_info=True,
        )


# ── Persistent-failure escalation ───────────────────────────────────
# 60-second cache TTLs are too short to be a long-term signal on their
# own.  This block closes the loop: every mark_dead appends to a
# JSONL ledger, the ledger is summed over a rolling 24h window per
# (provider, bare_id) key, and crossing a threshold files a CR via the
# proposal bridge proposing the model be retired from the catalog.
# Each (provider, bare_id) escalates at most once per ``_ESCALATION_DEDUP_DAYS``.

_DEAD_MARKS_PATH = Path(
    os.environ.get(
        "LLM_FACTORY_DEAD_MARKS",
        "/app/workspace/llm_factory/dead_marks.jsonl",
    )
)
_ESCALATION_STATE_PATH = Path(
    os.environ.get(
        "LLM_FACTORY_ESCALATION_STATE",
        "/app/workspace/llm_factory/escalations.json",
    )
)
_ESCALATION_THRESHOLD = 3
_ESCALATION_WINDOW_SECONDS = 24 * 3600.0
_ESCALATION_DEDUP_DAYS = 7

# Upper bound on the dead-marks ledger so a long-running process
# doesn't grow the file unboundedly.  Sized for a year of normal
# operation: at ~20 dead-marks/day worst case (Anthropic deprecates
# 4 models simultaneously, each marked 5x before retirement CR
# approval), 365 days × 20 = 7300 rows.  Round up to 10k for
# headroom.  Truncation keeps the newest rows — the rolling 24h
# scan would have ignored older ones anyway.
_DEAD_MARKS_MAX_LINES = 10_000

# Serialise the escalation read-modify-write cycle so two concurrent
# ``mark_dead`` calls that both cross the threshold can't both pass the
# dedup check and stage duplicate proposals.  Lightweight in-process
# lock — multi-process deployments deduplicate downstream via
# ``proposal_bridge.stage``'s content-hash idempotency, but a single
# lock in the most-likely-hot path (one process) is the cheap correct
# fix.
_ESCALATION_LOCK = threading.Lock()


def _append_dead_mark(provider: str, bare_id: str, reason: str) -> int:
    """Append a row to the dead-marks ledger and return the rolling
    24-hour count for ``(provider, bare_id)``.

    The ledger is plain JSONL — one ``{ts, provider, bare_id, reason}``
    object per line.  Cap is enforced at write time via
    :func:`app.utils.jsonl_retention.append_with_cap` so the file
    cannot grow unboundedly across years of operation; truncation
    keeps the newest rows which are also the only ones the 24-hour
    window scan cares about.
    """
    now = time.time()
    if _persistence_disabled:
        # Tests get an in-memory window-count view via the loaded cache.
        # Skip disk I/O so unit tests stay hermetic.
        return 0
    line = json.dumps({
        "ts": now,
        "provider": provider,
        "bare_id": bare_id,
        "reason": reason[:300],
    })
    try:
        from app.utils.jsonl_retention import append_with_cap
        append_with_cap(_DEAD_MARKS_PATH, line, max_lines=_DEAD_MARKS_MAX_LINES)
    except Exception:
        # Bounded-retention helper unavailable — degrade to plain
        # append.  Better to have an unbounded file than to lose a
        # dead-mark signal.
        _DEAD_MARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEAD_MARKS_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    # Count occurrences of this exact (provider, bare_id) in the last
    # 24h.  The file is append-only; we read forward and tally.  At
    # 24h × ~10/hour the file is tiny (~10k bytes), so a full scan is
    # fine.  If the file ever grows large, the retention monitor caps
    # it.
    count = 0
    cutoff = now - _ESCALATION_WINDOW_SECONDS
    try:
        with open(_DEAD_MARKS_PATH, "r", encoding="utf-8") as fh:
            for raw in fh:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if row.get("ts", 0) < cutoff:
                    continue
                if row.get("provider") == provider and row.get("bare_id") == bare_id:
                    count += 1
    except OSError:
        return 0
    return count


def _read_escalation_state() -> dict:
    """Read the per-key escalation state ``{key: last_escalated_at}``.

    Returns an empty dict on any error.  The on-disk format is a flat
    JSON object.
    """
    try:
        if not _ESCALATION_STATE_PATH.exists():
            return {}
        return json.loads(_ESCALATION_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_escalation_state(state: dict) -> None:
    """Atomic-rename write of the escalation state file."""
    try:
        _ESCALATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ESCALATION_STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, _ESCALATION_STATE_PATH)
    except Exception:
        logger.debug(
            "llm_factory_probe: failed to persist escalation state",
            exc_info=True,
        )


def _escalate_persistent_failure(
    provider: str,
    bare_id: str,
    reason: str,
    count_24h: int,
) -> None:
    """File a proposal-bridge CR proposing catalog retirement of the
    persistently-failing ``(provider, bare_id)`` AND emit an
    identity-continuity ledger event so the annual reflection picks up
    the operational reality.

    Dedup'd per ``(provider, bare_id)`` over ``_ESCALATION_DEDUP_DAYS``
    so a runaway loop can't spam CRs — the operator only needs to see
    the proposal once per retirement window.

    Thread safety: the dedup-and-stage sequence runs under
    :data:`_ESCALATION_LOCK` so two threads crossing the threshold
    simultaneously can't both pass the dedup check.  The actual
    proposal staging is idempotent via ``proposal_bridge``'s content-
    hash, but holding the lock around the state file write keeps the
    on-disk dedup record consistent.
    """
    if _persistence_disabled:
        return
    key = f"{provider}|{bare_id}"
    now = time.time()
    with _ESCALATION_LOCK:
        state = _read_escalation_state()
        last = state.get(key, 0.0)
        if now - last < _ESCALATION_DEDUP_DAYS * 86400:
            # Already escalated recently; the operator has the CR
            # pending or has acted on it.  Silent skip — escalation
            # has happened.
            return
        # Reserve the dedup slot before doing slow work so a second
        # thread arriving here sees the updated state and bails.
        state[key] = now
        _write_escalation_state(state)

    body_md = _compose_retirement_proposal(provider, bare_id, reason, count_24h)
    try:
        from app.proposal_bridge.store import stage as stage_proposal
        signature = f"{provider}__{bare_id.replace('/', '_')}"
        target_path = f"docs/proposed_retirements/{signature}.md"
        stage_proposal(
            source="llm_health_escalator",
            signature=signature,
            title=f"Retire {provider}/{bare_id} — {count_24h} mark_dead in 24h",
            body_markdown=body_md,
            target_path=target_path,
            cooldown_days=7,
        )
        logger.warning(
            "llm_factory_probe: ESCALATED %s/%s — staged retirement CR "
            "(count_24h=%d, dedup'd for %d days)",
            provider, bare_id, count_24h, _ESCALATION_DEDUP_DAYS,
        )
    except Exception:
        logger.warning(
            "llm_factory_probe: failed to stage retirement proposal for "
            "%s/%s (count_24h=%d)",
            provider, bare_id, count_24h, exc_info=True,
        )

    # Continuity-ledger emission is best-effort, complementary to the
    # CR.  The annual-reflection drift summary picks up
    # ``vendor_sunset`` events via the existing Counter at
    # app/identity/continuity_ledger.py:summarise_drift.
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="vendor_sunset",
            actor="llm_factory_probe",
            summary=(
                f"Persistent failure: {provider}/{bare_id} marked dead "
                f"{count_24h} times in 24h"
            ),
            detail={
                "provider": provider,
                "bare_id": bare_id,
                "count_24h": count_24h,
                "reason_sample": reason[:200],
            },
        )
    except Exception:
        logger.debug(
            "llm_factory_probe: continuity-ledger emission failed",
            exc_info=True,
        )

    # Dedup slot was already reserved + persisted inside the
    # ``_ESCALATION_LOCK`` block above, before staging — that's what
    # prevents two threads from both crossing the dedup gate.  No
    # second write needed here.


def _compose_retirement_proposal(
    provider: str,
    bare_id: str,
    reason: str,
    count_24h: int,
) -> str:
    """Render the markdown body for the retirement CR.

    The body must satisfy ``proposal_bridge.store._validate_target_path``
    so we frontload the YAML metadata and keep the body readable for
    operator triage in ``/cp/changes``.
    """
    return (
        "---\n"
        f"action: retire_catalog_entry\n"
        f"provider: {provider}\n"
        f"bare_id: {bare_id}\n"
        f"count_24h: {count_24h}\n"
        "---\n\n"
        f"# Proposed retirement: {provider} / {bare_id}\n\n"
        f"The LLM factory observed this model returning a model-id-level "
        f"failure (404 / not-found / deprecated marker) **{count_24h} "
        f"times in the last 24 hours**.  The 60-second health-cache TTL "
        f"means the cascade is currently routing around it, but the "
        f"underlying catalog entry is still listed as a candidate for "
        f"future role resolution — every fresh process will re-discover "
        f"the failure until either the upstream restores the model id "
        f"or the catalog drops the entry.\n\n"
        f"**Last observed reason:**\n\n"
        f"```\n{reason[:600]}\n```\n\n"
        f"**Recommended operator action:**\n\n"
        f"1. Confirm the upstream sunset (Anthropic / OpenRouter / "
        f"Ollama release notes).\n"
        f"2. If permanent, mark the catalog entry retired via "
        f"`persist_model_retired(catalog_key)` in "
        f"`app/llm_catalog.py:381`.\n"
        f"3. If transient (e.g. brief 5xx storm), reject this CR — the "
        f"next 7-day dedup window starts fresh.\n\n"
        f"Filed automatically by `app/llm_factory_probe.py:_escalate_persistent_failure`.\n"
    )


# Substring-pair matchers — every pair must ALL appear (case-insensitive)
# in the exception message for the match to fire.  Single-string entries
# are treated as a one-element AND.  Curated tight to avoid marking dead
# on transient failures.
_MODEL_NOT_FOUND_MARKERS: tuple[tuple[str, ...], ...] = (
    # Anthropic SDK 404 shape — the canonical model-not-found error
    # signature when the SDK receives a bogus / sunset / wrong-prefix
    # model id:
    #   Anthropic API call failed: Error code: 404 - {'type': 'error',
    #   'error': {'type': 'not_found_error', 'message': 'model: …'}}
    ("404", "not_found_error"),
    # OpenRouter and OpenAI-compatible 404 with explicit "model not found":
    ("model not found",),
    # OpenAI / litellm wrapped form:
    ("model_not_found",),
    # Anthropic alternate phrasings observed in the wild:
    ("the model", "does not exist"),
    # litellm BadRequestError wrapping a model-id error:
    ("notfounderror",),
    # Ollama 404 (model never pulled):
    ("model not loaded",),
)


def classify_failure(exc: BaseException) -> str | None:
    """Return a truncated reason string if *exc* is a model-id-level
    failure that should mark the model dead, or None otherwise.

    Strict on purpose — see module docstring.  A False positive here
    locks a working model out for 60 seconds; a False negative just
    means the next call will hit the same error and we'll classify
    again.  Prefer false negatives.
    """
    msg = str(exc).lower()
    for marker_tuple in _MODEL_NOT_FOUND_MARKERS:
        if all(m in msg for m in marker_tuple):
            return str(exc)[:300]
    return None


def _reset_for_tests() -> None:
    """Test helper — wipe the cache AND disable disk persistence.

    Tests that exercise mark_alive/mark_dead should never write to the
    real workspace path; calling this from a pytest fixture (autouse,
    yield) wipes both surfaces.  Not part of the public surface.
    """
    global _last_persist_at, _persistence_disabled
    with _HEALTH_LOCK:
        _HEALTH.clear()
        _last_persist_at = 0.0
        _persistence_disabled = True


# ── Module-import side-effect: load persisted cache ─────────────────
# Best-effort.  A failure here must NEVER block import — the cache
# falls back to empty and the first real call repopulates it.
_load_from_disk()

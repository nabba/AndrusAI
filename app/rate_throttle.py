"""
rate_throttle.py — Per-provider rate limiter for outgoing LLM API calls.

Each LLM provider gets its own rate bucket:
  - Anthropic:   conservative (default 10 RPM, configurable)
  - OpenRouter:  generous (default 60 RPM)
  - Ollama:      unlimited (local, no API limit)

User-facing requests get priority over background tasks.  Background
callers should call `set_background_caller(True)` before making LLM
calls; they will yield to any waiting user-facing request.

Also configures litellm's built-in retry with exponential backoff
so transient 429s are retried automatically.
"""

import contextvars
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
# Per-provider RPM limits (overridable via .env)
_ANTHROPIC_RPM = int(os.environ.get("ANTHROPIC_MAX_RPM", "10"))
_OPENROUTER_RPM = int(os.environ.get("OPENROUTER_MAX_RPM", "60"))
_RETRY_COUNT = int(os.environ.get("LITELLM_NUM_RETRIES", "5"))
_RETRY_BACKOFF = float(os.environ.get("LITELLM_RETRY_BACKOFF", "3"))  # seconds (was 15)

# ── litellm retry config (set before any litellm import) ──────────────────────
os.environ.setdefault("LITELLM_NUM_RETRIES", str(_RETRY_COUNT))

# ── Token bucket rate limiter ─────────────────────────────────────────────────

class _TokenBucket:
    """Thread-safe token bucket allowing at most `rate` calls per 60 seconds.

    Uses threading.Event for non-blocking wait that can be interrupted.
    """

    def __init__(self, rate: int, name: str = ""):
        self.rate = max(1, rate)
        self.interval = 60.0 / self.rate  # seconds between tokens
        self.name = name
        self._lock = threading.Lock()
        self._last = 0.0
        self._wake = threading.Event()

    def acquire(self) -> None:
        """Block until a token is available.

        Releases the lock while waiting so other threads can compute their
        own wait time.  Re-acquires the lock to stamp _last on completion.
        """
        while True:
            with self._lock:
                now = time.monotonic()
                wait = self._last + self.interval - now
                if wait <= 0:
                    # Token available — claim it immediately
                    self._last = now
                    return
                if wait > 1.0:
                    logger.debug(f"rate_throttle[{self.name}]: waiting {wait:.1f}s")
            # Sleep outside the lock so other threads aren't blocked
            self._wake.clear()
            self._wake.wait(timeout=wait)
            # Loop back to re-check under lock (another thread may have consumed the slot)


# Per-provider buckets
_buckets: dict[str, _TokenBucket] = {
    "anthropic": _TokenBucket(_ANTHROPIC_RPM, "anthropic"),
    "openrouter": _TokenBucket(_OPENROUTER_RPM, "openrouter"),
    # Ollama: no bucket — unlimited local calls
}

# ── Background caller tracking ────────────────────────────────────────────────
_is_background = threading.local()


def set_background_caller(is_bg: bool) -> None:
    """Mark the current thread as a background caller (lower priority)."""
    _is_background.value = is_bg


def _is_bg() -> bool:
    return getattr(_is_background, "value", False)


def _detect_provider(model: str = "", base_url: str = "", **kwargs) -> str:
    """Detect LLM provider from model string or base_url."""
    model_lower = (model or "").lower()
    base_lower = (base_url or "").lower()

    if "ollama" in model_lower or "ollama" in base_lower or "11434" in base_lower:
        return "ollama"
    if "openrouter" in base_lower:
        return "openrouter"
    if "anthropic" in model_lower or "claude" in model_lower:
        return "anthropic"
    # OpenRouter model IDs contain slashes like "deepseek/deepseek-chat"
    if "/" in model_lower and "anthropic" not in model_lower:
        return "openrouter"
    return "anthropic"  # default to most restrictive


def throttle_for_provider(provider: str) -> None:
    """Apply rate limit for a specific provider. No-op for Ollama."""
    if provider == "ollama":
        return
    bucket = _buckets.get(provider)
    if bucket:
        # Background callers yield briefly to let user-facing requests go first
        if _is_bg():
            time.sleep(0.1)
        bucket.acquire()


def throttle() -> None:
    """Legacy: throttle using Anthropic bucket (for unknown callers)."""
    throttle_for_provider("anthropic")


# ── Monkey-patch litellm completion to inject throttle ────────────────────────

_patched = False
_patch_lock = threading.Lock()


def install_throttle() -> None:
    """
    Patch litellm.completion to call per-provider throttle before each request.
    Safe to call multiple times (idempotent).
    """
    global _patched
    if _patched:
        return
    with _patch_lock:
        if _patched:
            return
        try:
            import litellm
            _original_completion = litellm.completion

            def _throttled_completion(*args, **kwargs):
                model = kwargs.get("model", args[0] if args else "")
                base_url = kwargs.get("base_url") or kwargs.get("api_base") or ""
                provider = _detect_provider(model, base_url)
                throttle_for_provider(provider)
                # Inject retry params if not already set
                kwargs.setdefault("num_retries", _RETRY_COUNT)
                # Fresh guard per call — success_callback, explicit inline
                # record, or failure branch share the first-writer-wins rule.
                _benchmark_recorded.set(False)
                t_start = time.monotonic()
                try:
                    response = _original_completion(*args, **kwargs)
                except Exception as exc:
                    latency_ms = int((time.monotonic() - t_start) * 1000)
                    _record_benchmark_failure(model, latency_ms)
                    _check_credit_error(exc, provider)
                    raise
                latency_ms = int((time.monotonic() - t_start) * 1000)
                # Successful call — resolve any prior credit alert for this provider
                _resolve_credit_if_needed(provider)
                _record_token_usage(response, kwargs, latency_ms=latency_ms)
                return response

            litellm.completion = _throttled_completion

            # Also patch acompletion for async paths
            if hasattr(litellm, "acompletion"):
                _original_acompletion = litellm.acompletion

                async def _throttled_acompletion(*args, **kwargs):
                    model = kwargs.get("model", args[0] if args else "")
                    base_url = kwargs.get("base_url") or kwargs.get("api_base") or ""
                    provider = _detect_provider(model, base_url)
                    throttle_for_provider(provider)
                    kwargs.setdefault("num_retries", _RETRY_COUNT)
                    _benchmark_recorded.set(False)
                    t_start = time.monotonic()
                    try:
                        response = await _original_acompletion(*args, **kwargs)
                    except Exception:
                        latency_ms = int((time.monotonic() - t_start) * 1000)
                        _record_benchmark_failure(model, latency_ms)
                        raise
                    latency_ms = int((time.monotonic() - t_start) * 1000)
                    _record_token_usage(response, kwargs, latency_ms=latency_ms)
                    return response

                litellm.acompletion = _throttled_acompletion

            _patched = True
            logger.info(
                f"rate_throttle: installed (anthropic={_ANTHROPIC_RPM}RPM, "
                f"openrouter={_OPENROUTER_RPM}RPM, ollama=unlimited, "
                f"{_RETRY_COUNT} retries, {_RETRY_BACKOFF}s backoff)"
            )
        except ImportError:
            logger.warning("rate_throttle: litellm not found, throttle not installed")

        # NOTE: we used to patch ``BaseLLM._track_token_usage_internal``
        # here to cover CrewAI-native providers (Anthropic, Gemini,
        # Azure, Bedrock) that bypass litellm's callback machinery.
        # That responsibility moved to the CrewAI event-bus subscriber
        # in ``app.observability.llm_events`` — one hook on
        # ``LLMCallCompletedEvent`` covers every provider uniformly,
        # including any CrewAI ships in the future.

    # Litellm success_callback is still useful for the observability
    # concerns that the event payload can't represent: measured
    # ``latency_ms`` for benchmark scoring and the raw response shape
    # for training-data capture.  See ``_record_token_usage``.
    try:
        import litellm
        litellm.success_callback = [_record_token_usage]
        logger.info("rate_throttle: litellm success_callback registered (scoped to latency-aware benchmark scoring + training capture)")
    except Exception:
        logger.debug("rate_throttle: could not register litellm callback", exc_info=True)


_cost_lookup: dict[str, tuple[float, float]] | None = None


def _get_cost_lookup() -> dict[str, tuple[float, float]]:
    """Lazily build model→(cost_input, cost_output) dict from CATALOG. O(1) per lookup.

    Maps multiple key variants for each model so we match regardless of how
    litellm reports the model name in the response:
      - catalog name:     "deepseek-v3.2"
      - model_id:         "openrouter/deepseek/deepseek-chat"
      - stripped of prefix: "deepseek/deepseek-chat"   (litellm often strips "openrouter/")
      - bare model:       "deepseek-chat"              (sometimes just the last segment)
      - anthropic:        "claude-opus-4-6"            (litellm strips "anthropic/")
    """
    global _cost_lookup
    if _cost_lookup is not None:
        return _cost_lookup
    try:
        from app.llm_catalog import CATALOG
        lookup: dict[str, tuple[float, float]] = {}
        for name, info in CATALOG.items():
            costs = (info.get("cost_input_per_m", 0), info.get("cost_output_per_m", 0))
            lookup[name] = costs
            model_id = info.get("model_id", "")
            if model_id and model_id != name:
                lookup[model_id] = costs
                # Strip provider prefix: "openrouter/deepseek/deepseek-chat" → "deepseek/deepseek-chat"
                if model_id.startswith("openrouter/"):
                    stripped = model_id[len("openrouter/"):]
                    lookup[stripped] = costs
                # Strip "anthropic/" prefix: "anthropic/claude-opus-4-6" → "claude-opus-4-6"
                if model_id.startswith("anthropic/"):
                    stripped = model_id[len("anthropic/"):]
                    lookup[stripped] = costs
                # Strip "ollama_chat/" prefix
                if model_id.startswith("ollama_chat/"):
                    stripped = model_id[len("ollama_chat/"):]
                    lookup[stripped] = costs
        _cost_lookup = lookup
    except Exception:
        _cost_lookup = {}
    return _cost_lookup


def _find_cost(model: str) -> tuple[float, float] | None:
    """Look up cost for a model, trying exact match then prefix match."""
    lookup = _get_cost_lookup()
    # Exact match
    hit = lookup.get(model)
    if hit:
        return hit
    # Try stripping version suffixes: "deepseek/deepseek-chat-v3" → "deepseek/deepseek-chat"
    # Common pattern: litellm appends version info not in our catalog
    import re
    base = re.sub(r"-v\d+(\.\d+)*$", "", model)
    if base != model:
        hit = lookup.get(base)
        if hit:
            return hit
    # Prefix match: find any key that starts with the model name or vice versa
    for key, costs in lookup.items():
        if model.startswith(key) or key.startswith(model):
            return costs
    return None


def _record_token_usage(response, kwargs: dict, latency_ms: int = 0) -> None:
    """Scoped observability hooks that need the raw LiteLLM response
    shape (richer than what the CrewAI event payload carries).

    Token + cost accounting and the activity heartbeat are handled by
    the event-bus subscriber in ``app.observability.llm_events`` — that
    path covers every provider uniformly and doesn't need this richer
    payload.

    What remains here:
      - **Benchmark scoring** (success row with ``latency_ms``) — the
        event payload doesn't carry latency, and measuring it here
        gives us an accurate number for the scoring model.
      - **Training-data capture** — needs the full LiteLLM response
        (prompt+completion text, tool_use blocks), not the normalised
        event shape.

    Called both as a litellm ``success_callback`` and inline from the
    throttled completion wrapper.  A ContextVar guard
    (:data:`_benchmark_recorded`) keeps the benchmark row idempotent
    when both callers fire for the same call.
    """
    try:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        model = getattr(response, "model", "") or kwargs.get("model", "unknown")
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total = prompt_tokens + completion_tokens
        if total <= 0:
            return

        # Training pipeline capture (fire-and-forget) — stays here
        # because it consumes the raw response object.
        try:
            _capture_training_data(response, kwargs, model)
        except Exception:
            pass

        # Benchmark scoring — guarded so _throttled_completion's direct
        # invocation and the success_callback don't both write a row for
        # the same call.  First writer wins; guard is cleared when the
        # llm_context scope ends.
        if not _benchmark_recorded.get(False):
            try:
                from app.llm_benchmarks import record
                from app.llm_context import current as _current_ctx
                ctx = _current_ctx()
                task_type = ctx.task_type if ctx else "general"
                record(model, task_type, True,
                       latency_ms=latency_ms, tokens=total)
                _benchmark_recorded.set(True)
            except Exception:
                pass
    except Exception:
        pass  # never fail the actual LLM call


def _record_benchmark_failure(model: str, latency_ms: int) -> None:
    """Record a failed LLM call into the benchmarks table.

    Called from the throttled completion wrapper when the underlying
    provider call raises. Reads the active :class:`~app.llm_context.CallContext`
    for the canonical task type so failures land in the same task-type
    partition that successes use.
    """
    try:
        from app.llm_benchmarks import record
        from app.llm_context import current as _current_ctx
        ctx = _current_ctx()
        task_type = ctx.task_type if ctx else "general"
        record(model, task_type, False, latency_ms=latency_ms, tokens=0)
        _benchmark_recorded.set(True)
    except Exception:
        pass
    # Heartbeat is emitted by the CrewAI event-bus subscriber
    # (LLMCallFailedEvent).  No explicit call needed here.


# ── Credit alert integration ─────────────────────────────────────────────────

_resolved_providers: set[str] = set()  # avoid repeated resolve calls


def _capture_training_data(response, kwargs: dict, model: str) -> None:
    """Extract prompt-completion pair from litellm response for self-training.

    Called from _record_token_usage on every successful LLM call.
    Filters out short/internal responses and deduplicates by content hash.
    Writes to JSONL + PostgreSQL via training_collector.
    """
    import threading

    # Extract completion text
    try:
        choices = getattr(response, "choices", [])
        if not choices:
            return
        completion = getattr(choices[0], "message", None)
        if not completion:
            return
        response_text = getattr(completion, "content", "") or ""
    except Exception:
        return

    # Filter: skip short/empty responses (internal routing, vetting, etc.)
    if len(response_text) < 50:
        return

    # Extract input messages
    messages = kwargs.get("messages", [])
    if not messages:
        return

    # Filter: skip system-only messages (no user content)
    has_user = any(m.get("role") == "user" for m in messages if isinstance(m, dict))
    if not has_user:
        return

    # Build training record
    def _store():
        try:
            from app.training_collector import _content_hash, _classify_model, _store_record
            from app.training_collector import MAX_RESPONSE_LENGTH
            from datetime import datetime, timezone

            stored_messages = [
                {"role": m.get("role", "user"), "content": str(m.get("content", ""))[:2000]}
                for m in messages[-5:] if isinstance(m, dict)
            ]
            stored_response = response_text[:MAX_RESPONSE_LENGTH]
            source_tier, provenance = _classify_model(model)

            record = {
                "id": _content_hash(stored_messages, stored_response),
                "agent_role": kwargs.get("metadata", {}).get("agent_role", "unknown")
                    if isinstance(kwargs.get("metadata"), dict) else "unknown",
                "task_description": str(stored_messages[-1].get("content", ""))[:500]
                    if stored_messages else "",
                "messages": stored_messages,
                "response": stored_response,
                "source_model": model,
                "source_tier": source_tier,
                "provenance": provenance,
                "quality_score": None,
                "training_eligible": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _store_record(record)
        except Exception:
            pass

    threading.Thread(target=_store, daemon=True, name="training-capture").start()


def _check_credit_error(exc: Exception, provider: str) -> None:
    """If the error looks like a credit/billing issue, report it."""
    try:
        from app.firebase_reporter import detect_credit_error, report_credit_alert
        detected = detect_credit_error(exc)
        if detected:
            report_credit_alert(detected, str(exc)[:300])
            _resolved_providers.discard(detected)
    except Exception:
        pass  # never interfere with the real error


def _resolve_credit_if_needed(provider: str) -> None:
    """On success, resolve any active credit alert for this provider."""
    if provider in _resolved_providers:
        return  # already resolved, skip
    try:
        from app.firebase_reporter import _active_alerts, resolve_credit_alert
        if provider in _active_alerts:
            resolve_credit_alert(provider)
            _resolved_providers.add(provider)
    except Exception:
        pass


# ── Request-level cost tracking ──────────────────────────────────────────────

_request_cost: contextvars.ContextVar["RequestCostTracker | None"] = contextvars.ContextVar(
    "request_cost", default=None,
)

# Per-call guard ensuring exactly one benchmarks row per LLM invocation.
# Set by the first writer (either ``_throttled_completion`` directly, its
# failure branch, or the ``success_callback`` via ``_record_token_usage``)
# and implicitly cleared when the request-level context unwinds — a fresh
# ContextVar read inside the next call returns False.
_benchmark_recorded: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "llm_benchmark_recorded", default=False,
)


# ── LLM activity heartbeat ─────────────────────────────────────────────
#
# Process-wide "last time any LLM call completed (success OR failure)"
# timestamp.  Used by the progressive timeout in ``handle_task`` to
# distinguish a genuinely long-running task (LLM calls still completing,
# tokens still accruing) from a stalled one (no LLM activity for N
# minutes, usually means the orchestrator is stuck in a loop or the
# provider is unreachable).
#
# Thread-safe.  The monotonic clock is used so wall-clock jumps
# (NTP sync, suspend-resume) don't confuse the stall detector.

_llm_activity_lock = threading.Lock()
_last_llm_activity_ts: float = 0.0
_llm_activity_count: int = 0


def record_llm_activity() -> None:
    """Mark that an LLM call just completed (success or failure).

    Called from ``_record_token_usage`` (success path) and
    ``_record_benchmark_failure`` (failure path).  Both paths count as
    "activity" because both prove the orchestrator is cycling through
    LLM interactions — a stalled task is one where NO LLM call has
    returned for an extended period.
    """
    global _last_llm_activity_ts, _llm_activity_count
    with _llm_activity_lock:
        _last_llm_activity_ts = time.monotonic()
        _llm_activity_count += 1


def seconds_since_last_llm_activity() -> float | None:
    """Return seconds since the last LLM call completed, or None if no
    call has completed yet this process.

    Callers use this to implement progress-gated timeouts:
      * None              → never seen activity (bootstrap / cold start)
      * small number      → task alive, LLM calls happening
      * large number (>N) → stalled, safe to abandon
    """
    with _llm_activity_lock:
        if _last_llm_activity_ts == 0.0:
            return None
        return time.monotonic() - _last_llm_activity_ts


def llm_activity_count() -> int:
    """Process-wide count of completed LLM calls.  Useful as an
    alternative to the timestamp when callers want to detect progress
    strictly by "new calls happened since I last looked"."""
    with _llm_activity_lock:
        return _llm_activity_count


class RequestCostTracker:
    """Accumulates token usage across all LLM calls in a single user request."""

    def __init__(self, request_id: str = ""):
        self.request_id = request_id
        self.crew_name = ""  # set by commander before dispatch
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0
        self.call_count = 0
        self.models_used: set[str] = set()
        self._lock = threading.Lock()

    def record(self, model: str, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_cost_usd += cost_usd
            self.call_count += 1
            self.models_used.add(model)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def summary(self) -> str:
        models = ", ".join(sorted(self.models_used)) if self.models_used else "none"
        return (
            f"{self.call_count} LLM calls, "
            f"{self.total_tokens:,} tokens, "
            f"${self.total_cost_usd:.4f}, "
            f"models: {models}"
        )


def start_request_tracking(request_id: str = "") -> RequestCostTracker:
    """Begin accumulating costs for a user request. Returns the tracker.

    Nesting-aware: if a tracker is already active in this context (a parent
    caller is already tracking), return the EXISTING tracker instead of
    creating a new one.  This prevents nested crews (media, critic,
    retrospective, etc.) from clobbering the outer Commander-level tracker
    and making the ticket report $0 cost.  The tracker keeps accumulating
    across the nested call so the Commander sees the full cost at the end.
    """
    existing = _request_cost.get(None)
    if existing is not None:
        return existing
    tracker = RequestCostTracker(request_id)
    _request_cost.set(tracker)
    return tracker


def stop_request_tracking() -> RequestCostTracker | None:
    """Return the accumulated tracker.  Does NOT clear the context-var
    unless called by the top of the request stack (via finalize_request_tracking()).

    Nested crews read this to get their contribution totals but should not
    strand the outer Commander's tracker.
    """
    return _request_cost.get(None)


def finalize_request_tracking() -> RequestCostTracker | None:
    """Clear the context-var and return the tracker.

    Use at the TOP of the request stack (Commander.handle) where the
    request lifecycle actually ends.  Nested callers use
    stop_request_tracking() instead.
    """
    tracker = _request_cost.get(None)
    _request_cost.set(None)
    return tracker


def get_active_tracker() -> "RequestCostTracker | None":
    """Get the active request tracker (for propagating to threads)."""
    return _request_cost.get(None)


def set_active_tracker(tracker: "RequestCostTracker | None") -> None:
    """Set the request tracker (for thread propagation)."""
    _request_cost.set(tracker)

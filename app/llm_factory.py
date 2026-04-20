"""
llm_factory.py — Multi-tier LLM provider with cascade routing.
NOTE: `from __future__ import annotations` makes all type hints strings,
avoiding the need to import crewai.LLM at module load time (~2s saving).

Architecture:
  Commander:     always Claude Opus 4.6 (routing reliability, tiny token volume)
  Specialists:   cascade through tiers based on llm_mode + cost_mode + availability:
                   1. Local Ollama (free, Metal GPU)  — if mode allows and local_llm_enabled
                   2. API tier (budget/mid via OpenRouter) — if mode allows and api_tier_enabled
                   3. Claude Sonnet 4.6 (premium fallback) — always available
  Vetting:       Claude Sonnet 4.6 by default (near-Opus quality, 5x cheaper)
                 Only applied to local Ollama output (API-tier models are frontier quality)
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from crewai import LLM  # type hints only — no runtime import cost
from app.config import get_settings, get_anthropic_api_key
from app.llm_catalog import (
    get_model, get_model_id, get_provider, get_tier,
    get_default_for_role, CATALOG,
)
from app import circuit_breaker

logger = logging.getLogger(__name__)

# Thread-local storage for last model/tier — prevents race conditions
# when multiple crews process concurrently in the commander thread pool (Q7).
_tls = threading.local()

# B2: Cache LLM objects by (model_id, max_tokens) to avoid re-creating per request.
# LLM objects are stateless — they just wrap a model_id + api_key + params.
# Thread-safe because dict reads are atomic in CPython and LLM() is immutable.
_llm_cache: dict[tuple, "LLM"] = {}
_llm_cache_lock = threading.Lock()

# Lazy-loaded crewai.LLM class — avoids 1.9s import at module load time.
# crewai's import chain pulls in its entire framework including litellm,
# pydantic models, tool registries, etc. Deferring to first use saves ~2s
# on cold boot and makes the module importable in <10ms.
# Uses @functools.cache (Python 3.9+) — thread-safe, no manual global needed.
@functools.cache
def _get_LLM_class():
    """Lazy-load crewai.LLM on first use."""
    from crewai import LLM
    return LLM


def _cached_llm(model_id: str, max_tokens: int = 4096, *, sampling_key: str = "", **kwargs) -> "LLM":
    """Get or create an LLM object, caching by (model_id, max_tokens, base_url, sampling_key).

    LLM objects are stateless wrappers — safe to share across requests.
    Cache eliminates ~50-100ms of object creation per specialist call.

    `sampling_key` is an opaque string (see `app.llm_sampling.sampling_cache_key`)
    that distinguishes entries differing only in temperature/top_p/min_p etc.
    Empty string preserves the legacy cache identity for non-creative callers.
    """
    base_url = kwargs.get("base_url", "")
    key = (model_id, max_tokens, base_url or "default", sampling_key)
    cached = _llm_cache.get(key)
    if cached is not None:
        return cached
    with _llm_cache_lock:
        cached = _llm_cache.get(key)
        if cached is not None:
            return cached
        LLM = _get_LLM_class()

        # ── Anthropic prompt caching: enable via extra_headers ──
        # Reduces cost by ~90% on cached prefix tokens (system prompt,
        # constitution, soul files). Only activates for Claude models.
        # litellm passes extra_headers through to the Anthropic SDK.
        if _is_anthropic_model(model_id):
            extra_headers = kwargs.pop("extra_headers", {}) or {}
            extra_headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            kwargs["extra_headers"] = extra_headers

        llm = LLM(model=model_id, max_tokens=max_tokens, **kwargs)
        _llm_cache[key] = llm
        logger.debug(f"llm_cache: new entry for {model_id} max={max_tokens} sampling={sampling_key!r} (cache size: {len(_llm_cache)})")
        return llm


def _is_anthropic_model(model_id: str) -> bool:
    """Check if a model ID is an Anthropic Claude model."""
    lower = model_id.lower()
    return any(k in lower for k in ("claude-opus", "claude-sonnet", "claude-haiku", "anthropic/claude"))


def _get_promoted_adapter(role: str) -> str | None:
    """Get promoted LoRA adapter path for an agent role, if one exists."""
    try:
        from app.training_pipeline import list_adapters
        from pathlib import Path
        for adapter in list_adapters():
            if adapter.promoted and (role in adapter.agent_roles or "all" in adapter.agent_roles):
                if Path(adapter.adapter_path).exists():
                    return adapter.adapter_path
    except Exception:
        pass
    return None


class _AdapterLLM:
    """LLM wrapper that routes inference through host bridge MLX with a LoRA adapter.

    Drop-in replacement for crewai.LLM — implements the .call() interface.
    Used when a promoted adapter exists for the agent's role AND local mode
    is active (adapter inference only makes sense on the host Metal GPU).
    """

    def __init__(self, model: str, adapter_path: str, max_tokens: int = 4096):
        self.model = f"mlx-adapter/{model}"
        self._base_model = model
        self._adapter = adapter_path
        self._max_tokens = max_tokens

    def call(self, prompt, **kwargs) -> str:
        try:
            from app.bridge_client import get_bridge
            bridge = get_bridge("specialist")
            if not bridge or not bridge.is_available():
                raise ConnectionError("Host bridge unavailable")
            result = bridge.mlx_generate(
                prompt=str(prompt)[:4000],
                model=self._base_model,
                adapter_path=self._adapter,
                max_tokens=self._max_tokens,
            )
            if "error" in result:
                raise RuntimeError(result["error"])
            return result.get("response", "")
        except Exception:
            # Fall back to Ollama base model (no adapter)
            logger.debug("AdapterLLM falling back to Ollama", exc_info=True)
            from app.config import get_settings
            s = get_settings()
            LLM = _get_LLM_class()
            fallback = LLM(
                model=f"ollama/{s.local_model_default}",
                max_tokens=self._max_tokens,
                base_url=s.local_llm_base_url,
            )
            return str(fallback.call(prompt))

    # CrewAI compatibility — LLM is referenced via getattr in some places
    def __str__(self):
        return self.model


def _get_last(attr: str) -> str | None:
    return getattr(_tls, attr, None)


def _set_last(model: str | None, tier: str | None) -> None:
    _tls.last_model_name = model
    _tls.last_tier = tier


def create_commander_llm() -> LLM:
    """Create the Commander routing LLM using the resolver's pick.

    Previously this function hard-forced an Anthropic model — any
    non-Anthropic pick from the resolver was silently swapped to
    ``claude-sonnet-4.6``. That bypassed the whole point of the
    scoring resolver.

    Now we honour the resolver's choice and route to whichever
    provider owns the chosen model:
      * Anthropic  → Anthropic SDK (requires ANTHROPIC_API_KEY)
      * OpenRouter → OpenRouter API (requires OPENROUTER_API_KEY)
      * Ollama     → local inference
    If the chosen provider's key is missing, we fall through to the
    cheapest API-tier alternative with a valid key, and ultimately
    to the DeepSeek survival bootstrap.
    """
    from app.config import get_openrouter_api_key

    settings = get_settings()
    model_name = get_default_for_role("commander", settings.cost_mode)
    entry = get_model(model_name) or {}

    provider = entry.get("provider")
    if provider == "anthropic":
        anthropic_key = get_anthropic_api_key()
        if anthropic_key:
            logger.info(f"create_commander_llm: resolved {model_name} (anthropic)")
            return _cached_llm(entry["model_id"], max_tokens=1024, api_key=anthropic_key)
        logger.warning(
            "create_commander_llm: resolver picked %s but ANTHROPIC_API_KEY is missing",
            model_name,
        )
    elif provider == "openrouter":
        or_key = get_openrouter_api_key()
        if or_key:
            logger.info(f"create_commander_llm: resolved {model_name} (openrouter)")
            return _cached_llm(
                entry["model_id"], max_tokens=1024,
                base_url="https://openrouter.ai/api/v1", api_key=or_key,
            )
        logger.warning(
            "create_commander_llm: resolver picked %s but OPENROUTER_API_KEY is missing",
            model_name,
        )
    elif provider == "ollama":
        # Commander via local Ollama — model_id is "ollama_chat/..."
        logger.info(f"create_commander_llm: resolved {model_name} (ollama local)")
        return _cached_llm(entry["model_id"], max_tokens=1024)

    # Fall-through: pick the cheapest API-tier survivor whose key is set.
    logger.warning(
        "create_commander_llm: resolver pick %r unreachable, falling back",
        model_name,
    )
    anthropic_key = get_anthropic_api_key()
    if anthropic_key:
        sonnet = get_model("claude-sonnet-4.6")
        if sonnet:
            return _cached_llm(sonnet["model_id"], max_tokens=1024, api_key=anthropic_key)
    or_key = get_openrouter_api_key()
    if or_key:
        deepseek = get_model("deepseek-v3.2")
        if deepseek:
            return _cached_llm(
                deepseek["model_id"], max_tokens=1024,
                base_url="https://openrouter.ai/api/v1", api_key=or_key,
            )
    return _cached_llm(
        "openrouter/deepseek/deepseek-chat", max_tokens=1024, api_key=or_key,
    )


def create_specialist_llm(
    max_tokens: int = 4096,
    role: str = "default",
    task_hint: str = "",
    force_tier: str | None = None,
    phase: str | None = None,
) -> LLM:
    """
    Create an LLM for a specialist role using the tier cascade.
    Behavior depends on current llm_mode:
      local:  Ollama only, Claude fallback if Ollama fails
      cloud:  API tier (OpenRouter) or Claude, skip Ollama
      hybrid: Try Ollama first, cascade to API tier, then Claude
      insane: Premium only — Opus for critical roles, Gemini 3.1 Pro / Sonnet for others

    If force_tier is set (e.g. from difficulty-based routing), it overrides
    the default tier selection from llm_selector.

    `phase` (creative-mode only) is one of "diverge"/"discuss"/"converge".
    When set, phase-dependent sampling parameters (temperature/top_p/min_p/
    presence_penalty) are applied. When None, legacy behavior is preserved
    byte-for-byte — including LLM cache identity.
    """
    # Q7: thread-local last model/tier tracking
    from app.llm_mode import get_mode
    settings = get_settings()
    mode = get_mode()

    # ── INSANE mode: premium-only, hardcoded role mapping ─────────────
    if mode == "insane":
        return _insane_mode_select(role, max_tokens, phase=phase)

    from app.llm_selector import select_model
    model_name = select_model(role, task_hint, force_tier=force_tier)
    entry = get_model(model_name)

    if not entry:
        logger.warning(f"llm_factory: model {model_name!r} not in catalog, falling back")
        return _claude_fallback(role, max_tokens, phase=phase)

    tier = entry["tier"]
    provider = entry["provider"]

    # ── LOCAL mode: only Ollama, Claude fallback ──────────────────────
    if mode == "local":
        if tier == "local" and settings.local_llm_enabled:
            llm = _try_local(model_name, entry, max_tokens, role, phase=phase)
            if llm:
                return llm
        return _claude_fallback(role, max_tokens, phase=phase)

    # ── CLOUD mode: skip Ollama, use API/Anthropic ───────────────────
    if mode == "cloud":
        if tier in ("free", "budget", "mid") and settings.api_tier_enabled:
            llm = _try_api(model_name, entry, max_tokens, role, phase=phase)
            if llm:
                return llm
        if provider == "anthropic":
            return _create_anthropic(model_name, entry, max_tokens, role, phase=phase)
        if tier == "premium" and provider == "openrouter":
            llm = _try_api(model_name, entry, max_tokens, role, phase=phase)
            if llm:
                return llm
        return _claude_fallback(role, max_tokens, phase=phase)

    # ── HYBRID mode: full cascade ────────────────────────────────────
    # Try local Ollama first
    if tier == "local" and settings.local_llm_enabled:
        llm = _try_local(model_name, entry, max_tokens, role, phase=phase)
        if llm:
            # Stage 4.3 — race local vs API on short prompts (default OFF).
            return _maybe_race_wrap(llm, role, max_tokens, phase)
        # Local failed — try API tier
        if settings.api_tier_enabled:
            logger.info(f"llm_factory: local failed for role={role}, trying API tier")
            api_model = get_default_for_role(role, settings.cost_mode)
            api_entry = get_model(api_model)
            if api_entry and api_entry["tier"] in ("free", "budget", "mid"):
                llm = _try_api(api_model, api_entry, max_tokens, role, phase=phase)
                if llm:
                    return llm
        return _claude_fallback(role, max_tokens, phase=phase)

    # Try API tier (OpenRouter)
    if tier in ("free", "budget", "mid") and settings.api_tier_enabled:
        llm = _try_api(model_name, entry, max_tokens, role, phase=phase)
        if llm:
            return llm
        return _claude_fallback(role, max_tokens, phase=phase)

    # Premium tier (Anthropic or OpenRouter)
    if provider == "anthropic":
        return _create_anthropic(model_name, entry, max_tokens, role, phase=phase)
    elif provider == "openrouter":
        llm = _try_api(model_name, entry, max_tokens, role, phase=phase)
        if llm:
            return llm
        return _claude_fallback(role, max_tokens, phase=phase)

    return _claude_fallback(role, max_tokens, phase=phase)


def create_vetting_llm() -> LLM:
    """Vetting gate — uses the resolver's pick for the ``vetting`` role.

    The ``VETTING_MODEL`` env var is NOT consulted — it was a piece of
    hand-curation that bypassed the resolver and the overlay. If you
    need to pin vetting to a specific model, install a row in
    ``control_plane.role_assignments`` (via Signal / governance
    approval). The resolver + overlay are the single source of truth.
    """
    from app.config import get_openrouter_api_key
    settings = get_settings()

    model_name = get_default_for_role("vetting", settings.cost_mode)
    entry = get_model(model_name) or {}

    provider = entry.get("provider") if entry else None

    if provider == "anthropic":
        anthropic_key = get_anthropic_api_key()
        if anthropic_key:
            logger.info(f"create_vetting_llm: resolved {model_name} (anthropic)")
            return _cached_llm(entry["model_id"], max_tokens=4096, api_key=anthropic_key)
    elif provider == "openrouter":
        or_key = get_openrouter_api_key()
        if or_key:
            logger.info(f"create_vetting_llm: resolved {model_name} (openrouter)")
            return _cached_llm(
                entry["model_id"], max_tokens=4096,
                base_url="https://openrouter.ai/api/v1", api_key=or_key,
            )
    elif provider == "ollama":
        logger.info(f"create_vetting_llm: resolved {model_name} (ollama local)")
        return _cached_llm(entry["model_id"], max_tokens=4096)

    # Fall-through to bootstrap survivors.
    logger.warning(
        "create_vetting_llm: resolver pick %r unreachable, falling back", model_name,
    )
    anthropic_key = get_anthropic_api_key()
    if anthropic_key:
        return _cached_llm("anthropic/claude-sonnet-4-6", max_tokens=4096, api_key=anthropic_key)
    or_key = get_openrouter_api_key()
    fallback = get_model("deepseek-v3.2") or {}
    model_id = fallback.get("model_id", "openrouter/deepseek/deepseek-chat")
    return _cached_llm(
        model_id, max_tokens=4096,
        base_url="https://openrouter.ai/api/v1", api_key=or_key,
    )


def create_cheap_vetting_llm() -> LLM:
    """Cheap verification gate — budget model for quick yes/no quality checks.
    Falls back to Sonnet if OpenRouter key is not set."""
    settings = get_settings()
    or_key = settings.openrouter_api_key.get_secret_value()
    if settings.api_tier_enabled and or_key:
        budget_model = get_model("deepseek-v3.2")
        if budget_model:
            return _cached_llm(budget_model["model_id"], max_tokens=256,
                               base_url="https://openrouter.ai/api/v1", api_key=or_key)
    return _cached_llm("anthropic/claude-sonnet-4-6", max_tokens=256, api_key=get_anthropic_api_key())


class _RacingLLM:
    """Stage 4.3 — cascade race wrapper (hybrid mode, short prompts only).

    On `.call(prompt)`:
      * if len(prompt) >= threshold, delegates to primary (cost-safe).
      * otherwise races primary + secondary, returns first non-error.

    Invariant: primary is always the Ollama local LLM; secondary is the
    OpenRouter fallback. Both are crewai.LLM objects (same .call() contract).

    Gated by settings.cascade_race_short — default False.
    """

    def __init__(self, primary, secondary, *, threshold_chars: int, timeout_s: float):
        self._primary = primary
        self._secondary = secondary
        self._threshold = threshold_chars
        self._timeout = timeout_s
        self.model = getattr(primary, "model", "racing-llm")

    def __str__(self):
        return f"race({self._primary}, {self._secondary})"

    def call(self, prompt, **kwargs):
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        prompt_str = prompt if isinstance(prompt, str) else str(prompt)
        if len(prompt_str) >= self._threshold:
            return self._primary.call(prompt, **kwargs)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-race") as ex:
            f_primary = ex.submit(self._primary.call, prompt, **kwargs)
            f_secondary = ex.submit(self._secondary.call, prompt, **kwargs)
            done, pending = wait(
                {f_primary, f_secondary}, timeout=self._timeout,
                return_when=FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
            for f in done:
                try:
                    return f.result(timeout=0.1)
                except Exception:
                    continue
            # Both futures failed or timed out in the first window — give
            # primary a bit more time, then fall through to secondary.
            try:
                return f_primary.result(timeout=2.0)
            except Exception:
                return f_secondary.result(timeout=2.0)

    # Some callers invoke LLM attributes directly; forward to primary.
    def __getattr__(self, name):
        return getattr(self._primary, name)


def _maybe_race_wrap(primary, role: str, max_tokens: int, phase: str | None):
    """If cascade_race_short is enabled, return a _RacingLLM wrapping primary
    with an API-tier secondary. On any failure, returns primary unwrapped.
    """
    try:
        settings = get_settings()
        if not getattr(settings, "cascade_race_short", False):
            return primary
        if not settings.api_tier_enabled:
            return primary
        api_model = get_default_for_role(role, settings.cost_mode)
        api_entry = get_model(api_model)
        if not (api_entry and api_entry.get("tier") in ("free", "budget", "mid")):
            return primary
        secondary = _try_api(api_model, api_entry, max_tokens, role, phase=phase)
        if secondary is None:
            return primary
        threshold_chars = int(settings.cascade_race_token_threshold * 4)  # ~4 chars/tok
        timeout_s = float(settings.cascade_race_timeout_s)
        return _RacingLLM(primary, secondary,
                          threshold_chars=threshold_chars, timeout_s=timeout_s)
    except Exception:
        return primary


def is_using_local() -> bool:
    return _get_last("last_tier") == "local"

def is_using_api_tier() -> bool:
    return _get_last("last_tier") in ("budget", "mid")

def get_last_model() -> str | None:
    return _get_last("last_model_name")

def get_last_tier() -> str | None:
    return _get_last("last_tier")


# INSANE mode now delegates to resolve_role_default with cost_mode="quality".
# The resolver already picks the strongest model in the premium tier that
# meets the role's constraints — exactly what INSANE used to hardcode.
# No more static role-map: if Opus 4.8 lands tomorrow it becomes the
# INSANE-mode commander automatically.


def _sampling(phase: str | None, provider: str) -> tuple[dict, str]:
    """Return (llm_kwargs, cache_key) for phase+provider. ({}, '') when phase is None."""
    if phase is None:
        return {}, ""
    from app.llm_sampling import build_llm_kwargs, sampling_cache_key
    return build_llm_kwargs(phase, provider), sampling_cache_key(phase, provider)


def _insane_mode_select(role: str, max_tokens: int, phase: str | None = None) -> LLM:
    """INSANE mode: resolve the strongest premium-tier model for the role.

    Uses ``resolve_role_default(role, cost_mode="quality")`` so the pick
    is always data-driven: commander/vetting/critic get whichever premium
    model currently scores highest (Opus today, next-gen Opus tomorrow);
    heavy-lifting roles like coding/research auto-pick the strongest
    non-Anthropic premium model (Gemini 3.1 Pro today).
    """
    from app.llm_catalog import resolve_role_default
    model_name = resolve_role_default(role, cost_mode="quality")
    entry = get_model(model_name)
    if not entry:
        return _claude_fallback(role, max_tokens, phase=phase)

    _set_last(model_name, entry.get("tier", "premium"))

    if entry["provider"] == "anthropic":
        logger.info(f"llm_factory: [INSANE] role={role} → ANTHROPIC {model_name}")
        extra, key = _sampling(phase, "anthropic")
        return _cached_llm(entry["model_id"], max_tokens=max_tokens,
                           sampling_key=key, api_key=get_anthropic_api_key(), **extra)

    settings = get_settings()
    api_key = settings.openrouter_api_key.get_secret_value()
    if api_key and circuit_breaker.is_available("openrouter"):
        # Give reasoning-heavy premium picks (e.g. Gemini-class) plenty of
        # output budget — they benefit from thinking-token headroom.
        premium_max = max(max_tokens, 16384)
        logger.info(
            f"llm_factory: [INSANE] role={role} → API {model_name} "
            f"(${entry['cost_output_per_m']:.2f}/Mo, max_tokens={premium_max})"
        )
        circuit_breaker.record_success("openrouter")
        extra, key = _sampling(phase, "openrouter")
        return _cached_llm(entry["model_id"], max_tokens=premium_max,
                           sampling_key=key,
                           base_url="https://openrouter.ai/api/v1",
                           api_key=api_key, **extra)

    logger.warning(
        f"llm_factory: [INSANE] OpenRouter unavailable for {model_name}, "
        "falling back to Claude"
    )
    return _claude_fallback(role, max_tokens, phase=phase)


def _try_local(model_name: str, entry: dict, max_tokens: int, role: str, phase: str | None = None) -> LLM | None:
    # Q7: thread-local last model/tier tracking
    if not circuit_breaker.is_available("ollama"):
        logger.info(f"llm_factory: skipping Ollama (circuit open)")
        return None

    # ── Adapter-aware inference (T4-14): if a promoted LoRA adapter exists
    #    for this role AND the host bridge's MLX is available, prefer the
    #    _AdapterLLM path which runs on Metal GPU with the fine-tune applied.
    adapter_path = _get_promoted_adapter(role or "default")
    if adapter_path:
        try:
            from app.bridge_client import get_bridge
            bridge = get_bridge("specialist")
            if bridge and bridge.is_available():
                status = bridge.mlx_status()
                if status.get("available"):
                    _set_last(model_name, "local")
                    logger.info(
                        f"llm_factory: role={role} → MLX ADAPTER "
                        f"{adapter_path} (base={model_name})"
                    )
                    return _AdapterLLM(model_name, adapter_path, max_tokens)
        except Exception:
            logger.debug("adapter selection failed, falling back to Ollama",
                         exc_info=True)

    try:
        from app.ollama_native import spawn_model
        start = time.monotonic()
        url = spawn_model(model_name)
        spawn_ms = int((time.monotonic() - start) * 1000)
        if url:
            _set_last(model_name, "local")
            circuit_breaker.record_success("ollama")
            logger.info(f"llm_factory: role={role} → LOCAL {model_name} at {url} (spawn: {spawn_ms}ms)")
            extra, key = _sampling(phase, "ollama")
            return _cached_llm(entry["model_id"], max_tokens=max_tokens,
                               sampling_key=key, base_url=url, **extra)
        circuit_breaker.record_failure("ollama")
    except Exception as exc:
        circuit_breaker.record_failure("ollama")
        logger.warning(f"llm_factory: local {model_name} failed: {exc}")
    return None


def _try_api(model_name: str, entry: dict, max_tokens: int, role: str, phase: str | None = None) -> LLM | None:
    # Q7: thread-local last model/tier tracking
    if not circuit_breaker.is_available("openrouter"):
        logger.info(f"llm_factory: skipping OpenRouter (circuit open)")
        return None
    settings = get_settings()
    api_key = settings.openrouter_api_key.get_secret_value()
    if not api_key:
        logger.warning("llm_factory: OpenRouter API key not set, skipping API tier")
        return None
    try:
        _set_last(model_name, entry["tier"])
        circuit_breaker.record_success("openrouter")
        logger.info(f"llm_factory: role={role} → API {model_name} (${entry['cost_output_per_m']:.2f}/Mo)")
        extra, key = _sampling(phase, "openrouter")
        return _cached_llm(entry["model_id"], max_tokens=max_tokens,
                           sampling_key=key,
                           base_url="https://openrouter.ai/api/v1", api_key=api_key, **extra)
    except Exception as exc:
        circuit_breaker.record_failure("openrouter")
        logger.warning(f"llm_factory: API {model_name} failed: {exc}")
        _set_last(None, None)
    return None


# ── Anthropic credit-exhausted failover ─────────────────────────────
#
# When the Anthropic-direct API returns:
#     400 {"type":"invalid_request_error",
#          "message":"Your credit balance is too low to access the Anthropic API..."}
# every subsequent premium-tier call would repeat the same 400 until the user
# tops up.  Rather than hard-fail, we transparently fail over to the same
# Claude model served via OpenRouter (which charges the OpenRouter balance).
#
# State is process-global with a 1h TTL so:
#   • We don't reprobe on every call (costly, same error)
#   • Recovery after top-up eventually happens without a restart
#   • All agents + background tasks converge on the same failover decision

import time as _time

_CREDIT_EXHAUSTED_PHRASES = (
    "credit balance is too low",
    "credit balance too low",
)
_ANTHROPIC_CREDIT_EXHAUSTED_AT: float | None = None
_ANTHROPIC_RECOVERY_TTL_SECS = 3600  # 1 hour


def _is_anthropic_credit_exhausted() -> bool:
    """True if we've seen a credit-exhausted 400 recently (within TTL)."""
    global _ANTHROPIC_CREDIT_EXHAUSTED_AT
    t = _ANTHROPIC_CREDIT_EXHAUSTED_AT
    if t is None:
        return False
    if _time.monotonic() - t > _ANTHROPIC_RECOVERY_TTL_SECS:
        # TTL elapsed — clear the flag and let the next call probe Anthropic
        # again.  If credits were topped up, this recovers automatically.
        _ANTHROPIC_CREDIT_EXHAUSTED_AT = None
        logger.info(
            "llm_factory: Anthropic credit-exhausted flag expired (TTL %ds) "
            "— next call will probe direct Anthropic again",
            _ANTHROPIC_RECOVERY_TTL_SECS,
        )
        return False
    return True


def _mark_anthropic_credit_exhausted() -> None:
    """Latch the credit-exhausted state; subsequent premium calls failover."""
    global _ANTHROPIC_CREDIT_EXHAUSTED_AT
    if _ANTHROPIC_CREDIT_EXHAUSTED_AT is None:
        logger.warning(
            "llm_factory: Anthropic credit balance exhausted — "
            "routing ALL premium-tier Claude requests via OpenRouter "
            "for the next %ds (will reprobe direct Anthropic after that).",
            _ANTHROPIC_RECOVERY_TTL_SECS,
        )
    _ANTHROPIC_CREDIT_EXHAUSTED_AT = _time.monotonic()


def _anthropic_to_openrouter_model_id(anthropic_model_id: str) -> str:
    """Translate an Anthropic-SDK model id (dashes in version) into the
    OpenRouter model id (dots in version, 'openrouter/' prefix).

    AA/Anthropic emits  : anthropic/claude-sonnet-4-6
    OpenRouter expects  : openrouter/anthropic/claude-sonnet-4.6
    """
    # Strip 'anthropic/' prefix if present, then convert version dashes→dots
    slug = anthropic_model_id
    if slug.startswith("anthropic/"):
        slug = slug[len("anthropic/"):]
    # Anthropic slug pattern: claude-<family>-<major>-<minor>
    # Find the LAST two '-<digit>' groups and convert to '.<digit>'
    import re as _re
    # claude-sonnet-4-6 → claude-sonnet-4.6
    # claude-opus-4-5 → claude-opus-4.5
    slug = _re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", slug)
    return f"openrouter/anthropic/{slug}"


def _build_claude_via_openrouter(
    model_name: str,
    entry: dict,
    max_tokens: int,
    role: str,
    phase: str | None = None,
) -> LLM:
    """Build a Claude LLM backed by OpenRouter (uses OR credits, not Anthropic)."""
    from app.config import get_openrouter_api_key
    or_key = get_openrouter_api_key()
    if not or_key:
        raise RuntimeError(
            "Anthropic credit exhausted AND OpenRouter key missing — "
            "cannot failover Claude. Add credits or set OPENROUTER_API_KEY."
        )
    or_model_id = _anthropic_to_openrouter_model_id(entry["model_id"])
    _set_last(f"{model_name} (via OpenRouter)", entry["tier"])
    logger.info(
        "llm_factory: role=%s → OPENROUTER %s (credit failover, ~$%.2f/Mo)",
        role, or_model_id, entry.get("cost_output_per_m", 0),
    )
    extra, sample_key = _sampling(phase, "openrouter")
    return _cached_llm(
        or_model_id, max_tokens=max_tokens, sampling_key=sample_key,
        base_url="https://openrouter.ai/api/v1", api_key=or_key, **extra,
    )


class _CreditFailoverLLM:
    """Thin wrapper around a direct-Anthropic LLM.

    Delegates every call to the wrapped LLM.  If any call raises with
    "credit balance too low", we latch the global flag, lazily build an
    OpenRouter-backed equivalent, and transparently retry the call
    through it.  All subsequent calls on THIS instance go straight to
    the OpenRouter LLM.  (Newly-created instances skip the direct call
    entirely once the global flag is set — see _create_anthropic.)
    """
    __slots__ = ("_direct", "_or_factory", "_or_llm")

    def __init__(self, direct_llm, or_factory):
        self._direct = direct_llm
        self._or_factory = or_factory
        self._or_llm = None

    def _maybe_failover(self, exc: BaseException) -> bool:
        msg = str(exc).lower()
        if any(p in msg for p in _CREDIT_EXHAUSTED_PHRASES):
            _mark_anthropic_credit_exhausted()
            self._or_llm = self._or_factory()
            return True
        return False

    def call(self, *args, **kwargs):
        if self._or_llm is not None:
            return self._or_llm.call(*args, **kwargs)
        try:
            return self._direct.call(*args, **kwargs)
        except Exception as exc:
            if self._maybe_failover(exc):
                logger.warning(
                    "llm_factory: failing over mid-call to OpenRouter Claude "
                    "(Anthropic credit exhausted)"
                )
                return self._or_llm.call(*args, **kwargs)
            raise

    def __getattr__(self, name: str):
        # Delegate everything else to the active LLM.  CrewAI / LiteLLM
        # sometimes poke at attributes other than .call() (.model,
        # .max_tokens, .stop_sequences, stream helpers, etc.); we must
        # transparently proxy those.
        active = self._or_llm if self._or_llm is not None else self._direct
        return getattr(active, name)


def _create_anthropic(model_name: str, entry: dict, max_tokens: int, role: str, phase: str | None = None) -> LLM:
    # Q7: thread-local last model/tier tracking
    _set_last(model_name, entry["tier"])

    # Credit-exhausted latch: if we've already seen the 400 in this process,
    # skip Anthropic direct and go straight to OpenRouter.  Saves a guaranteed
    # 400-response round-trip per premium call until credits are topped up.
    if _is_anthropic_credit_exhausted():
        logger.info(
            "llm_factory: role=%s → skipping direct Anthropic (credit-exhausted latch set), "
            "routing to OpenRouter", role,
        )
        return _build_claude_via_openrouter(model_name, entry, max_tokens, role, phase)

    logger.info(f"llm_factory: role={role} → ANTHROPIC {model_name} (${entry['cost_output_per_m']:.2f}/Mo)")
    extra, key = _sampling(phase, "anthropic")
    direct = _cached_llm(entry["model_id"], max_tokens=max_tokens,
                         sampling_key=key, api_key=get_anthropic_api_key(), **extra)

    # Wrap so a mid-call 400 credit-exhausted triggers transparent failover.
    def _or_factory():
        return _build_claude_via_openrouter(model_name, entry, max_tokens, role, phase)
    return _CreditFailoverLLM(direct, _or_factory)


def _claude_fallback(role: str, max_tokens: int, phase: str | None = None) -> LLM:
    """Final fallback: Claude Sonnet if Anthropic is available, else best OpenRouter model."""
    from app.config import get_openrouter_api_key
    anthropic_key = get_anthropic_api_key()

    # Honor the credit-exhausted latch here too.  Without this, the "fallback"
    # path just repeats the same 400.
    if anthropic_key and not _is_anthropic_credit_exhausted():
        _set_last("claude-sonnet-4.6", "premium")
        logger.info(f"llm_factory: role={role} → FALLBACK Claude Sonnet 4.6 (direct Anthropic)")
        extra, key = _sampling(phase, "anthropic")
        direct = _cached_llm("anthropic/claude-sonnet-4-6", max_tokens=max_tokens,
                             sampling_key=key, api_key=anthropic_key, **extra)
        # Same credit-failover wrapper as _create_anthropic.
        _synthetic_entry = {"model_id": "anthropic/claude-sonnet-4-6",
                            "tier": "premium",
                            "cost_output_per_m": 15.0}
        def _or_factory():
            return _build_claude_via_openrouter(
                "claude-sonnet-4.6", _synthetic_entry, max_tokens, role, phase,
            )
        return _CreditFailoverLLM(direct, _or_factory)

    # Path A: Anthropic credit-exhausted but we have OpenRouter → use Claude via OR.
    if _is_anthropic_credit_exhausted() and get_openrouter_api_key():
        _synthetic_entry = {"model_id": "anthropic/claude-sonnet-4-6",
                            "tier": "premium",
                            "cost_output_per_m": 15.0}
        logger.info(f"llm_factory: role={role} → FALLBACK Claude Sonnet 4.6 via OpenRouter (credit-exhausted)")
        return _build_claude_via_openrouter(
            "claude-sonnet-4.6", _synthetic_entry, max_tokens, role, phase,
        )

    # Path B: No Anthropic key at all — use a non-Claude OpenRouter model.
    _set_last("deepseek-v3.2", "budget")
    logger.warning(f"llm_factory: role={role} → FALLBACK deepseek-v3.2 (no premium option available)")
    extra, key = _sampling(phase, "openrouter")
    return _cached_llm("openrouter/deepseek/deepseek-chat", max_tokens=max_tokens,
                       sampling_key=key, api_key=get_openrouter_api_key(), **extra)


# ── Provider health check for graceful degradation ──────────────────────────

_all_providers_exhausted = False
_exhaustion_alerted = False


def check_all_providers_health() -> bool:
    """Return True if at least one LLM provider is available.

    If ALL circuit breakers are OPEN, returns False. The caller (orchestrator)
    is responsible for force-probing and user communication — this function
    does NOT send Signal alerts because circuit-breaker state often reflects
    background-task noise, not actual provider outages.
    """
    global _all_providers_exhausted
    from app.circuit_breaker import is_available

    anthropic_ok = is_available("anthropic")
    openrouter_ok = is_available("openrouter")
    ollama_ok = is_available("ollama")

    any_available = anthropic_ok or openrouter_ok or ollama_ok

    if not any_available and not _all_providers_exhausted:
        _all_providers_exhausted = True
        logger.warning(
            "All LLM circuit breakers OPEN — orchestrator will force-probe "
            "(anthropic=%s, openrouter=%s, ollama=%s)",
            "open", "open", "open",
        )
    elif any_available and _all_providers_exhausted:
        _all_providers_exhausted = False
        logger.info("LLM provider recovered — circuit breakers back to normal")

    return any_available

"""
llm_factory.py — Multi-tier LLM provider with cascade routing.
NOTE: `from __future__ import annotations` makes all type hints strings,
avoiding the need to import crewai.LLM at module load time (~2s saving).

Architecture:
  Commander:     resolver pick (premium-floor role) at current runtime mode
  Specialists:   cascade through tiers based on runtime mode + availability:
                   1. Local Ollama (free, Metal GPU)  — if mode allows local
                      tier and local_llm_enabled
                   2. API tier (budget/mid via OpenRouter) — if mode whitelist
                      includes it and api_tier_enabled
                   3. Claude Sonnet 4.6 (premium fallback) — always available
  Vetting:       Resolver pick for the vetting role at the current runtime mode.

Runtime mode vocabulary (see app.llm_catalog.RUNTIME_MODES):
  free, budget, balanced [default], quality, insane, anthropic
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from crewai import LLM  # type hints only — no runtime import cost
from app.config import get_settings, get_anthropic_api_key, get_openrouter_api_key
from app.llm_catalog import (
    get_model, get_model_id, get_provider, get_tier,
    get_default_for_role, CATALOG,
    derived_id, validate_entry, fallback_chain,
)
from app import circuit_breaker
from app import llm_factory_probe
from app.runtime_settings import get_idle_pause_due_to_budget

logger = logging.getLogger(__name__)


# ── Typed construction failures ─────────────────────────────────────────
# The factory is the single source of truth for "give me a working LLM
# for this role".  Every callable construction path either returns a live
# LLM or raises a typed exception in the hierarchy below.  Callers (the
# Commander's ``_route``, the vetting crew, anything else) get
# unambiguous failure semantics: a generic ``except Exception`` is no
# longer the right tool — orchestrator code must catch
# :class:`NoWorkingModelAvailable` specifically so a typo'd model id in
# the catalog cannot reach the user as "Sorry, I had trouble understanding".

class ConstructionFailed(Exception):
    """Raised by :func:`_construct_from_entry` when a single catalog
    candidate cannot be turned into a usable LLM.

    Carries a short ``reason_code`` (one of the strings in
    :data:`_REASON_CODES`) and a free-form ``detail`` for logging.  The
    chain walker collects these and folds them into a
    :class:`NoWorkingModelAvailable` if every candidate fails.

    A ``ConstructionFailed`` is *recoverable* — it means "this catalog
    entry doesn't work, try the next one".  It is NOT a fatal error and
    should not propagate past the chain walker.
    """
    def __init__(self, reason_code: str, detail: str):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


# Canonical reason codes — exact string match is part of the API the
# chain walker logs and tests assert against.  Adding a new code: add it
# here, add a raise-site, add a test row to the contract suite.
_REASON_CODES = (
    "not_in_catalog",      # catalog_key has no entry in CATALOG
    "shape_invalid",       # validate_entry returned problems
    "missing_key",         # required API key env var not set
    "marked_dead",         # health cache recorded a recent model-id 404
    "build_failed",        # underlying constructor raised
    "disabled",            # subsystem (e.g. local Ollama) disabled by settings
    "unknown_provider",    # provider field doesn't match any handler
    "budget_paused",       # operator-engaged spend brake skips paid providers
)


class NoWorkingModelAvailable(Exception):
    """Raised by :func:`_walk_chain` when every candidate in the fallback
    chain fails construction.  Carries the full list of attempts so the
    operator alert can describe the entire failure surface in one
    Signal message instead of a vague "router LLM unavailable".

    Catching this exception in the orchestrator (rather than relying on
    a generic ``except Exception``) is the design contract for surfacing
    LLM-cascade exhaustion as a user-visible event.  See
    ``agents/commander/orchestrator.py`` for the catch site.
    """
    def __init__(
        self,
        role: str,
        attempts: list[tuple[str, "ConstructionFailed"]],
    ):
        self.role = role
        self.attempts = attempts
        summary = ", ".join(
            f"{name}({exc.reason_code})" for name, exc in attempts
        ) or "<empty chain>"
        super().__init__(
            f"No working model available for role={role!r} after "
            f"{len(attempts)} attempts: {summary}"
        )

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


# ── Model output-token ceilings (2026-05-03 audit fix for H2) ───────
#
# Some models silently cap completion at 4096 regardless of what we
# request — `z-ai/glm-4.7` was the verified offender during the
# 2026-05-02 Estonia v7 dispatch (we sent max_tokens=8192, the API
# returned completion_tokens=4096, the script truncated mid-word).
#
# Resolution order in `model_max_output_tokens`:
#   1. Explicit `max_output_tokens` field on the catalog entry
#   2. Family-based heuristic (covers models we haven't catalogued yet)
#   3. Conservative default 4096
#
# `_clamp_max_tokens` then takes min(requested, model_ceiling) so we
# never request more than the model can actually deliver.

# Family heuristics — keys are case-insensitive substrings of model_id;
# first match wins; lookups stop after the first hit.  Conservative
# numbers from each provider's published completion ceilings as of
# 2026-05.  When in doubt, prefer LOWER (safer to under-request than
# get silently capped).
_MODEL_FAMILY_OUTPUT_LIMITS: tuple[tuple[str, int], ...] = (
    ("claude-opus", 64_000),     # Anthropic Opus 4.x
    ("claude-sonnet", 64_000),   # Anthropic Sonnet 4.x
    ("claude-haiku", 64_000),    # Anthropic Haiku 4.x
    ("claude", 64_000),          # any Claude (fallback within family)
    ("gpt-5", 16_000),           # OpenAI GPT-5.x
    ("gpt-4o", 16_000),          # OpenAI GPT-4o
    ("gpt-4-turbo", 16_000),
    ("gemini-2.5", 64_000),      # Google Gemini 2.5
    ("gemini", 8_192),           # older Gemini
    ("kimi", 8_192),             # Moonshot Kimi K2.x
    ("moonshot", 8_192),
    ("glm-4.7", 4_096),          # GLM 4.7 verified 4K-capped (Estonia v7)
    ("glm-4", 4_096),            # GLM 4.x family — assume same ceiling
    ("minimax", 8_192),
    ("qwen", 8_192),             # Qwen 3.x family
    ("deepseek", 8_192),         # DeepSeek V3.x
    ("gemma", 8_192),
    ("llama", 4_096),            # Meta Llama family — conservative
    ("mistral", 4_096),          # Mistral family — conservative
)

_MODEL_OUTPUT_DEFAULT = 4_096


def model_max_output_tokens(model_id: str) -> int:
    """Return the practical max completion tokens for *model_id*.

    Checks the catalog entry first (preferred), falls back to family
    heuristics, falls back to a conservative 4096 default.  The family
    heuristics let us add new models without immediately needing to
    catalog them — they get a reasonable ceiling out of the box.
    """
    if not model_id:
        return _MODEL_OUTPUT_DEFAULT
    # Check catalog by model_id substring match — entries are keyed by
    # short name (e.g. "claude-sonnet-4.6") with a separate model_id
    # field, so we walk and match.
    try:
        from app.llm_catalog import CATALOG
        for entry in CATALOG.values():
            if entry.get("model_id") == model_id:
                cap = entry.get("max_output_tokens")
                if isinstance(cap, int) and cap > 0:
                    return cap
                break  # found the model but no cap declared — fall through
    except Exception:
        pass  # catalog import path issue — fall through to family check

    mid = model_id.lower()
    for substr, cap in _MODEL_FAMILY_OUTPUT_LIMITS:
        if substr in mid:
            return cap
    return _MODEL_OUTPUT_DEFAULT


def _clamp_max_tokens(model_id: str, requested: int) -> int:
    """Clamp requested max_tokens to the model's actual ceiling.

    Logs a warning when the request exceeds the model ceiling so the
    over-request shows up in the logs (helps catch spec drift).
    """
    ceiling = model_max_output_tokens(model_id)
    if requested > ceiling:
        logger.info(
            "max_tokens clamp: model=%s requested=%d ceiling=%d (clamping)",
            model_id, requested, ceiling,
        )
        return ceiling
    return requested


def _cached_llm(
    model_id: str,
    max_tokens: int = 8192,
    *,
    sampling_key: str = "",
    llm_builder=None,
    **kwargs,
) -> "LLM":
    """Get or create an LLM object, caching by
    (builder-tag, model_id, max_tokens, base_url, sampling_key).

    LLM objects are stateless wrappers over (model_id, api_key, params)
    — safe to share across requests.  Cache eliminates ~50-100ms of
    object creation per specialist call.

    Parameters
    ----------
    model_id, max_tokens, sampling_key, **kwargs
        Forwarded to the LLM constructor.
    llm_builder : Callable[[str, int, **kwargs], LLM], optional
        Factory for non-default LLM subclasses (e.g.
        ``CreditAwareAnthropicCompletion``).  Called as
        ``llm_builder(model_id, max_tokens, **kwargs)``.  If omitted,
        the default ``crewai.LLM`` constructor is used.

        NOTE: cached instances must behave correctly under every call —
        no sticky per-instance state that would break auto-recovery /
        shared-state contracts.  Our CreditAware subclass satisfies
        this because it consults ``circuit_breaker["anthropic_credits"]``
        on every ``call()``, so a cached instance always routes
        correctly even after credits are restored.

    Cache isolation
    ---------------
    The builder identity is part of the cache key.  Without this, a
    CreditAware entry under ``model_id=claude-sonnet-4-6`` would
    collide with a plain-``crewai.LLM`` entry for the same model id,
    and whichever built first would lock the cache shape.  Tagging by
    ``builder.__qualname__`` keeps the namespaces independent.
    """
    # 2026-05-03 audit fix for H2 — clamp the requested max_tokens to
    # the model's actual completion ceiling.  Some providers silently
    # cap (e.g. glm-4.7 at 4096 regardless of request) which produced
    # mid-word truncation in the 2026-05-02 Estonia v7 dispatch.  The
    # clamp keeps our cache key honest (different model with different
    # ceiling = different cache entry) and makes over-requests visible
    # in the logs.
    max_tokens = _clamp_max_tokens(model_id, max_tokens)

    base_url = kwargs.get("base_url", "")
    builder_tag = llm_builder.__qualname__ if llm_builder is not None else "default"
    key = (builder_tag, model_id, max_tokens, base_url or "default", sampling_key)
    cached = _llm_cache.get(key)
    if cached is not None:
        return cached
    with _llm_cache_lock:
        cached = _llm_cache.get(key)
        if cached is not None:
            return cached

        # ── Anthropic prompt caching: enable via extra_headers ──
        # Reduces cost by ~90% on cached prefix tokens (system prompt,
        # constitution, soul files). Only activates for Claude models.
        # litellm passes extra_headers through to the Anthropic SDK.
        if _is_anthropic_model(model_id):
            extra_headers = kwargs.pop("extra_headers", {}) or {}
            extra_headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            kwargs["extra_headers"] = extra_headers

        # ── OpenRouter provider exclusion ──
        # OpenRouter's anonymous "Stealth" sub-provider class periodically
        # returns 502 `Invalid URL: ''` (3 174/50k errors as of 2026-04-30,
        # then 630/week as of 2026-05-09 once we noticed the filter wasn't
        # firing on prefix-routed calls — see below).  We exclude such
        # providers by default via OpenRouter's documented provider-routing
        # API.  Active role-assigned models (Claude / Gemma / DeepSeek paid
        # variants) all have non-Stealth routes, so this is a reliability
        # gain with no functional loss.  Override via env var
        # OPENROUTER_IGNORE_PROVIDERS (CSV); set it empty to disable filtering.
        #
        # 2026-05-10 fix (T3.3) — the original ``"openrouter.ai" in base_url``
        # check missed every prefix-routed call (e.g.
        # ``model_id="openrouter/deepseek/deepseek-chat"`` with no explicit
        # base_url, which litellm routes to OpenRouter via env-var
        # ``OPENROUTER_API_KEY``).  Those calls are the bulk of our
        # OpenRouter traffic, so the filter was effectively no-op.  Extend
        # the trigger to also fire when ``model_id`` starts with
        # ``openrouter/``.
        _is_openrouter_call = (
            "openrouter.ai" in (base_url or "")
            or (model_id or "").startswith("openrouter/")
        )
        if _is_openrouter_call:
            import os as _os
            env_ignore = _os.environ.get("OPENROUTER_IGNORE_PROVIDERS", "Stealth")
            ignore_list = [n.strip() for n in env_ignore.split(",") if n.strip()]
            if ignore_list:
                extra_body = dict(kwargs.pop("extra_body", {}) or {})
                provider_pref = dict(extra_body.get("provider", {}) or {})
                existing = list(provider_pref.get("ignore", []) or [])
                for name in ignore_list:
                    if name not in existing:
                        existing.append(name)
                provider_pref["ignore"] = existing
                extra_body["provider"] = provider_pref
                kwargs["extra_body"] = extra_body

        if llm_builder is not None:
            llm = llm_builder(model_id, max_tokens, **kwargs)
        else:
            LLM = _get_LLM_class()
            llm = LLM(model=model_id, max_tokens=max_tokens, **kwargs)

        _llm_cache[key] = llm
        logger.debug(
            "llm_cache: new entry builder=%s model=%s max=%d sampling=%r (cache size: %d)",
            builder_tag, model_id, max_tokens, sampling_key, len(_llm_cache),
        )
        return llm


# ── Single-candidate construction + fallback-chain walker ──────────────
#
# These two helpers are the *only* sanctioned way to turn a catalog key
# into an LLM:
#
#   * :func:`_construct_from_entry` builds a single candidate, applies
#     every legitimacy check the factory promises (shape validation,
#     health-cache short-circuit, API-key presence, provider dispatch),
#     and raises :class:`ConstructionFailed` with a typed reason code on
#     any failure mode the chain walker should skip past.
#
#   * :func:`_walk_chain` iterates a list of candidate catalog keys,
#     returning the first one that constructs.  If every candidate fails,
#     it raises :class:`NoWorkingModelAvailable` carrying the full list
#     of attempts so the operator alert can describe the entire failure
#     surface in one place.
#
# Every public ``create_*_llm`` function in this module composes these
# two primitives — that is the contract the factory enforces.  The
# orchestrator's catch boundary expects :class:`NoWorkingModelAvailable`
# specifically; no generic ``except Exception`` swallows construction
# failures into "Sorry, I had trouble understanding" any more.


def _check_candidate_basics(
    name: str,
    entry: dict,
    *,
    require_provider: str | None = None,
) -> "ConstructionFailed | None":
    """Run the shared pre-build checks: provider filter, shape, health,
    brake, key.  Returns ``None`` if the candidate passes every check,
    or the first failing :class:`ConstructionFailed` otherwise.

    Used by both :func:`_construct_from_entry` (CrewAI LLM path) and
    :meth:`AnthropicClientHandle._select` (raw-SDK path) so the two
    factory surfaces share validation logic rather than diverging.

    *require_provider* restricts the check to a single provider — the
    raw-SDK handle uses this to reject non-Anthropic candidates with
    a typed reason code rather than silently passing them through.
    """
    if require_provider is not None and entry.get("provider") != require_provider:
        return ConstructionFailed(
            "unknown_provider",
            f"provider={entry.get('provider')!r} != required {require_provider!r}",
        )

    # Shape validation
    problems = validate_entry(name, entry)
    if problems:
        return ConstructionFailed("shape_invalid", problems[0])

    provider = entry["provider"]

    # Health-cache short-circuit
    bare = derived_id(entry, "native_anthropic")
    health = llm_factory_probe.health_of(provider, bare)
    if health is not None and not health.is_alive:
        return ConstructionFailed("marked_dead", health.last_reason)

    # Total-spend brake (paid providers only)
    if provider in ("anthropic", "openrouter"):
        try:
            if get_idle_pause_due_to_budget():
                return ConstructionFailed(
                    "budget_paused",
                    f"idle_pause_due_to_budget is engaged — skipping "
                    f"{provider} candidate to honour the monthly cap; "
                    "chain walker will fall through to local Ollama",
                )
        except Exception:
            pass

    # Provider-specific API-key check
    if provider == "anthropic" and not get_anthropic_api_key():
        return ConstructionFailed("missing_key", "ANTHROPIC_API_KEY not set")
    if provider == "openrouter" and not get_openrouter_api_key():
        return ConstructionFailed("missing_key", "OPENROUTER_API_KEY not set")

    return None


def _construct_from_entry(
    name: str,
    entry: dict,
    max_tokens: int,
    role: str,
    *,
    phase: str | None = None,
) -> "LLM":
    """Build an LLM from a single catalog entry, or raise :class:`ConstructionFailed`.

    Failure modes are exhaustive — every code path that does NOT return
    an LLM raises with a typed ``reason_code``:

    ``shape_invalid``
        :func:`validate_entry` flagged a provider/prefix mismatch.
        Subset of "the catalog itself is wrong about this model".
    ``marked_dead``
        The health cache has a recent record of a model-id-level
        rejection (404 ``not_found_error``).  Skip for the TTL window.
    ``missing_key``
        The provider's API-key env var is unset.  In a multi-key
        environment this is a per-provider availability signal.
    ``disabled``
        A subsystem-level disable flag (e.g. ``local_llm_enabled``)
        forbids the route.
    ``build_failed``
        Underlying constructor raised — usually a network blip, a
        misconfigured base_url, or a CrewAI/litellm internal bug.
    ``unknown_provider``
        Catalog entry's ``provider`` field doesn't match any handler.
        Indicates a catalog drift bug.
    ``budget_paused``
        Operator-engaged total-cost-ceiling brake skips paid providers.
    """
    # Pre-build checks (shape, health, brake, key) — shared with the
    # raw-SDK handle path via :func:`_check_candidate_basics`.
    fail = _check_candidate_basics(name, entry)
    if fail is not None:
        raise fail

    provider = entry["provider"]

    # Provider dispatch — each branch must end in either a return
    # or a raise; no implicit fall-through.  Key + brake checks have
    # already passed inside ``_check_candidate_basics``.
    if provider == "anthropic":
        try:
            return _build_claude_llm(
                name, entry["model_id"], max_tokens=max_tokens, role=role,
                phase=phase,
                tier=entry.get("tier", "premium"),
                cost_out=entry.get("cost_output_per_m", 15.0),
            )
        except Exception as exc:  # noqa: BLE001
            raise ConstructionFailed("build_failed", f"{exc!s}") from exc

    if provider == "openrouter":
        # OpenRouter daily-cap pre-check.  Symmetric with the
        # Anthropic gate, applied at construction (not per-call —
        # OpenRouter LLMs reach litellm via crewai.LLM with no
        # wrapping layer for a per-call hook).  Translates the typed
        # cap-exceeded exception into ``ConstructionFailed
        # ("budget_paused", …)`` so the chain walker falls through
        # to local Ollama uniformly with the Anthropic and
        # idle-pause-due-to-budget paths.
        try:
            from app.llm_openrouter_budget import (
                pre_check as _or_pre_check, OpenRouterDailyCapExceeded,
            )
            _or_pre_check(estimated_cost_usd=0.0)
        except OpenRouterDailyCapExceeded as exc:
            raise ConstructionFailed("budget_paused", str(exc)) from exc
        except Exception:
            # Failure-OPEN — broken budget module doesn't block calls.
            pass

        try:
            # ``_try_api`` returns ``None`` on circuit-breaker-open or
            # other recoverable failures; translate to typed
            # ``build_failed`` so the chain walker sees a uniform
            # contract.
            llm = _try_api(name, entry, max_tokens, role, phase=phase)
            if llm is None:
                raise ConstructionFailed(
                    "build_failed",
                    "OpenRouter unavailable (breaker open or _try_api returned None)",
                )
            return llm
        except ConstructionFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConstructionFailed("build_failed", f"{exc!s}") from exc

    if provider == "ollama":
        settings = get_settings()
        if not settings.local_llm_enabled:
            raise ConstructionFailed("disabled", "local_llm_enabled=False")
        try:
            llm = _try_local(name, entry, max_tokens, role, phase=phase)
            if llm is None:
                raise ConstructionFailed(
                    "build_failed", "Ollama spawn returned None"
                )
            return llm
        except ConstructionFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConstructionFailed("build_failed", f"{exc!s}") from exc

    raise ConstructionFailed("unknown_provider", f"provider={provider!r}")


def _walk_chain(
    candidates: list[str],
    max_tokens: int,
    role: str,
    *,
    phase: str | None = None,
) -> "LLM":
    """Return an LLM for the first candidate that constructs, else raise.

    Order of *candidates* matters — the resolver-picked model usually
    appears first, then the bootstrap survivors in premium → budget →
    local order.  See :func:`app.llm_catalog.fallback_chain` for the
    canonical sequence.

    Duplicates in *candidates* are skipped (cheap one-pass dedup).
    """
    attempts: list[tuple[str, ConstructionFailed]] = []
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        entry = get_model(name)
        if entry is None:
            attempts.append((name, ConstructionFailed("not_in_catalog", "")))
            continue
        try:
            llm = _construct_from_entry(
                name, entry, max_tokens, role, phase=phase,
            )
            if attempts:
                # Useful operator signal — the walker had to fall
                # through, so the resolver's top pick was unavailable.
                # INFO level because this is the chain walker doing
                # exactly what it is designed to do; the original
                # failure was already logged at its raise site.
                logger.info(
                    "llm_factory walker: role=%s landed on %s after "
                    "skipping %d candidate(s): %s",
                    role, name, len(attempts),
                    "; ".join(f"{n}({e.reason_code})" for n, e in attempts),
                )
            return llm
        except ConstructionFailed as exc:
            logger.debug(
                "llm_factory walker: role=%s skipping candidate %s — %s",
                role, name, exc,
            )
            attempts.append((name, exc))
            continue
    raise NoWorkingModelAvailable(role, attempts)


def _chain_for_role(
    role: str,
    primary: str | None = None,
) -> list[str]:
    """Compose the ordered candidate list for *role*.

    *primary* (typically the resolver-picked model name) is prepended if
    given; the standard fallback chain follows.  The chain walker
    dedups, so listing the same key twice is harmless.
    """
    chain = list(fallback_chain(role))
    if primary:
        chain.insert(0, primary)
    return chain


# ── Raw-SDK factory surface for non-CrewAI callers ──────────────────
#
# Many subsystems (vision, JSON-mode classification, structured
# diagnosis, brainstorm, concierge, …) want the raw Anthropic SDK
# rather than the CrewAI ``LLM`` wrapper.  Reasons include: native
# image content blocks, structured ``tool_use`` outputs, custom
# system prompts, streaming, or just historical inertia from before
# the factory existed.
#
# Pre-factory those sites all did:
#
#     from anthropic import Anthropic
#     client = Anthropic(api_key=key)
#     resp = client.messages.create(model="claude-haiku-4-5-…", …)
#
# which hardcoded a model id (no catalog awareness, no failover, no
# health cache, no budget) and instantiated an SDK client per call
# (no connection pooling).  When Anthropic deprecated such an id
# upstream, every one of those sites silently broke.  The 2026-05-24
# incident root cause for the router was the *same* problem in a
# different layer.
#
# :func:`anthropic_client_for_role` is the factory-supplied
# alternative.  It returns a handle that:
#
#   * picks the model via the catalog + chain walker (filtered to
#     ``provider="anthropic"`` entries — the SDK only speaks Anthropic),
#   * consults the health cache before each call (skips dead models),
#   * instruments call outcomes (mark_alive on 2xx, mark_dead on
#     model-not-found 404) so the same feedback loop the router uses
#     applies to every bypass site,
#   * exposes ``.messages.create(**kwargs)`` matching the SDK shape so
#     migrating call sites is a one-line edit.
#
# The returned object is NOT cached across calls — each call
# re-selects to pick up health-cache updates and governance overlay
# changes.  SDK client construction is cheap (it's a thin
# ``httpx.Client`` wrapper).


class AnthropicClientHandle:
    """Factory-managed raw Anthropic SDK handle.

    Exposes the same ``.messages.create(**kwargs)`` surface as
    ``anthropic.Anthropic().messages``, with the ``model`` kwarg
    auto-injected from catalog selection and call outcomes wired into
    the health cache.

    Construction selects the model once.  Subsequent calls re-validate
    against the health cache and, if the original pick is now marked
    dead, re-select via the chain walker before issuing the call.

    Per-call selection means a busy site sees the most recent health
    information without re-instantiating; per-construction selection
    keeps the common-case latency unchanged.
    """

    def __init__(self, role: str, task_hint: str = ""):
        self._role = role
        self._task_hint = task_hint
        # Build lazily so a fresh process without ANTHROPIC_API_KEY
        # raises only when something actually tries to call.
        self._catalog_key: str | None = None
        self._bare_model: str | None = None
        self._client = None
        # Serialise re-selection so two concurrent ``.messages.create()``
        # calls on the same handle don't both invoke ``_select`` and
        # race on mutating ``_client`` / ``_bare_model`` /
        # ``_catalog_key`` mid-flight.  Re-selection is rare (only on
        # first call or after a model is marked dead in the health
        # cache) so contention is negligible — the lock is here as a
        # correctness guarantee, not a performance feature.
        self._selection_lock = threading.Lock()

    @property
    def model_id(self) -> str:
        """Return the currently-selected bare model id (re-selecting
        if not yet chosen or marked dead since last call)."""
        self._ensure_fresh_selection()
        assert self._bare_model is not None  # _ensure_fresh_selection postcondition
        return self._bare_model

    @property
    def catalog_key(self) -> str:
        """Return the catalog key for the currently-selected model."""
        self._ensure_fresh_selection()
        assert self._catalog_key is not None
        return self._catalog_key

    def _ensure_fresh_selection(self) -> None:
        # Fast path — lock-free read.  ``_bare_model`` is a single
        # reference assignment, atomic in CPython.  A reader that
        # observes a stale value would still pass through the health-
        # cache check below; the worst case is one wasted call on a
        # dead id, which the call-site instrumentation will then
        # mark_dead.  We re-validate under the lock to avoid two
        # concurrent re-selections.
        if self._bare_model is not None:
            health = llm_factory_probe.health_of("anthropic", self._bare_model)
            if health is None or health.is_alive:
                return  # cached selection still good
        with self._selection_lock:
            # Double-check after acquiring the lock — another thread
            # may have re-selected while we waited.
            if self._bare_model is not None:
                health = llm_factory_probe.health_of("anthropic", self._bare_model)
                if health is None or health.is_alive:
                    return
            self._select()

    def _select(self) -> None:
        """Walk the chain for the first Anthropic-provider candidate
        that constructs.  Raises :class:`NoWorkingModelAvailable` if
        every Anthropic entry in the chain fails — the caller decides
        whether to surface the error or degrade.

        Pre-build checks (provider, shape, health, brake, key) are
        delegated to :func:`_check_candidate_basics` so the SDK-handle
        path shares validation logic with the CrewAI-LLM path
        (:func:`_construct_from_entry`).  Only the construction step
        differs — this method builds an Anthropic SDK client; the
        CrewAI path builds a ``CreditAwareAnthropicCompletion``.
        """
        # Compose the chain — resolver pick for the role first, then
        # bootstrap survivors.  Per-candidate filtering to
        # provider="anthropic" happens in ``_check_candidate_basics``.
        from app.llm_mode import get_mode
        primary = get_default_for_role(self._role, get_mode())
        chain = _chain_for_role(self._role, primary=primary)

        attempts: list[tuple[str, ConstructionFailed]] = []
        for catalog_key in chain:
            entry = get_model(catalog_key)
            if entry is None:
                attempts.append(
                    (catalog_key, ConstructionFailed("not_in_catalog", ""))
                )
                continue
            fail = _check_candidate_basics(
                catalog_key, entry, require_provider="anthropic",
            )
            if fail is not None:
                attempts.append((catalog_key, fail))
                continue

            # Construct the SDK client.  We import lazily so the
            # factory module's import graph stays flat for processes
            # that never need Anthropic.
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                attempts.append(
                    (catalog_key, ConstructionFailed(
                        "build_failed", f"anthropic SDK not installed: {exc!s}",
                    ))
                )
                continue
            try:
                self._client = Anthropic(api_key=get_anthropic_api_key())
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    (catalog_key, ConstructionFailed("build_failed", f"{exc!s}"))
                )
                continue

            self._catalog_key = catalog_key
            self._bare_model = derived_id(entry, "native_anthropic")
            logger.info(
                "anthropic_client_for_role: role=%s task_hint=%r → %s (bare=%s)",
                self._role, self._task_hint, catalog_key, self._bare_model,
            )
            return

        raise NoWorkingModelAvailable(
            f"anthropic-sdk[{self._role}]", attempts,
        )

    @property
    def messages(self):
        """SDK-compatible ``.messages`` namespace.

        Returns an :class:`_InstrumentedMessages` proxy that auto-injects
        ``model=`` and wires call outcomes into the health cache.
        """
        self._ensure_fresh_selection()
        return _InstrumentedMessages(
            client=self._client,
            bare_model=self._bare_model,
            source_tag=self._source_tag(),
        )

    # Expose a `.beta` passthrough for sites that use Anthropic's beta
    # features (e.g. prompt caching, computer use, message batches).
    # Same instrumentation pattern.
    @property
    def beta(self):
        self._ensure_fresh_selection()
        return _InstrumentedBeta(
            client=self._client,
            bare_model=self._bare_model,
            source_tag=self._source_tag(),
        )

    def _source_tag(self) -> str:
        """Compact role/task-hint string for source-attributed log lines."""
        if self._task_hint:
            return f"{self._role}:{self._task_hint}"
        return self._role


class _InstrumentedMessages:
    """Wrapper around ``anthropic.Anthropic().messages`` that injects
    ``model=`` and records call outcomes into the health cache.

    Pre-flight cost gate
    --------------------

    Before issuing the call, consults
    :func:`app.llm_anthropic_budget.pre_check` so the daily Anthropic
    cap is enforced uniformly across every factory-managed Anthropic
    call.  The factory-level gate is now the single contract — sites
    that previously called ``llm_anthropic_budget.call_or_skip``
    inline before constructing should just catch
    :class:`app.llm_anthropic_budget.AnthropicDailyCapExceeded` if
    they want to degrade gracefully.

    Estimated cost defaults to ``0.0`` — callers can pass
    ``_estimated_cost_usd=`` to bias the cap accurately for known-
    expensive operations.  The kwarg is stripped before forwarding to
    the SDK so the SDK never sees it.

    Source attribution
    ------------------

    On :class:`AnthropicDailyCapExceeded` we emit a single ``INFO``
    log line tagged with ``role``/``task_hint`` from the parent
    handle so operators debugging "where did this spend pressure
    come from?" can trace it back to the caller.  Replaces the
    per-site ``source="…"`` logging that used to live in
    :func:`llm_anthropic_budget.call_or_skip`.
    """

    def __init__(self, client, bare_model: str, *, source_tag: str = ""):
        self._client = client
        self._bare_model = bare_model
        self._source_tag = source_tag

    def create(self, **kwargs):
        # Caller MUST NOT pass model — the factory owns that choice.
        # If they do, log and override so the call doesn't silently
        # bypass the catalog.
        if "model" in kwargs and kwargs["model"] != self._bare_model:
            logger.warning(
                "AnthropicClientHandle: caller passed model=%r — "
                "overriding with factory-selected %r.  Drop the model "
                "kwarg from the call site to silence this.",
                kwargs["model"], self._bare_model,
            )
        kwargs["model"] = self._bare_model

        estimated_cost = float(kwargs.pop("_estimated_cost_usd", 0.0) or 0.0)

        # Streaming path observes at iteration boundaries, not at
        # call-open, so it manages its own envelope.
        if kwargs.get("stream"):
            return self._instrumented_stream(
                estimated_cost_usd=estimated_cost, **kwargs
            )

        # Non-streaming path: single shot through the standard
        # observation envelope (pre_check + outcome).  See
        # :func:`app.llm_factory_probe.call_with_observation` for
        # the contract — ``AnthropicDailyCapExceeded`` and model-id-
        # level 404s propagate; other errors are unchanged.
        try:
            return llm_factory_probe.call_with_observation(
                self._bare_model,
                lambda: self._client.messages.create(**kwargs),
                estimated_cost_usd=estimated_cost,
            )
        except Exception as exc:
            # Source-attributed cap-exceed log.  Replaces the
            # per-site ``source="…"`` logging that previously lived
            # in ``llm_anthropic_budget.call_or_skip``.  Only fires
            # for the cap-exceed exception class; other exceptions
            # propagate without this log line.
            from app.llm_anthropic_budget import AnthropicDailyCapExceeded
            if isinstance(exc, AnthropicDailyCapExceeded):
                logger.info(
                    "Anthropic call refused for source=%r: %s",
                    self._source_tag or self._bare_model, exc,
                )
            raise

    def _instrumented_stream(self, estimated_cost_usd: float = 0.0, **kwargs):
        """Streaming variant: open the upstream stream, then return a
        generator that yields events through while observing the
        terminal outcome (mark_alive on graceful exhaustion,
        mark_dead on a model-id-level error).

        Callers iterate the returned object exactly as they would the
        raw Anthropic SDK stream — health-cache updates happen
        transparently at iteration boundaries.

        The pre-check happens BEFORE the stream is opened; per-token
        cost is picked up post-hoc by the audit log the same way as
        non-streaming calls.
        """
        # Pre-flight cap gate — runs once at stream open.
        from app.llm_anthropic_budget import pre_check
        pre_check(estimated_cost_usd=estimated_cost_usd)

        try:
            stream = self._client.messages.create(**kwargs)
        except BaseException as exc:
            llm_factory_probe.observe_outcome(self._bare_model, exc=exc)
            raise

        bare = self._bare_model

        def _gen():
            try:
                for event in stream:
                    yield event
            except BaseException as exc:
                llm_factory_probe.observe_outcome(bare, exc=exc)
                raise
            else:
                llm_factory_probe.observe_outcome(bare, exc=None)

        return _gen()


class _InstrumentedBeta:
    """Wrapper around ``anthropic.Anthropic().beta`` — exposes
    ``.messages.create`` with the same instrumentation as the non-beta
    path.

    Beta-passthrough contract
    -------------------------

    ``.messages`` is fully instrumented (health cache + cost gate +
    streaming wrapper).  Every OTHER attribute on ``.beta`` (e.g.
    ``.beta.batches``, ``.beta.tools``, ``.beta.files``) is passed
    through to the raw SDK via ``__getattr__`` and is therefore
    **uninstrumented** — call outcomes do NOT update the health cache
    and the cost pre-check does NOT fire.

    This is deliberate: the load-bearing path is ``.beta.messages``
    (e.g. prompt-caching extra-headers); no current call site uses
    other beta endpoints.  When a new beta endpoint becomes load-
    bearing, promote it to a dedicated wrapper class with the same
    discipline as ``_InstrumentedMessages`` rather than relying on the
    passthrough.

    A test pin (``test_beta_messages_instrumented`` + a future
    ``test_beta_passthrough_uninstrumented`` regression check if
    needed) keeps this contract visible.
    """

    def __init__(self, client, bare_model: str, *, source_tag: str = ""):
        self._client = client
        self._bare_model = bare_model
        self._source_tag = source_tag

    @property
    def messages(self):
        return _InstrumentedMessages(
            client=self._client.beta,
            bare_model=self._bare_model,
            source_tag=self._source_tag,
        )

    def __getattr__(self, name):
        # Passthrough for uninstrumented beta endpoints — accept this
        # asymmetry by deliberate choice; see class docstring.
        return getattr(self._client.beta, name)


def anthropic_client_for_role(
    role: str,
    task_hint: str = "",
) -> AnthropicClientHandle:
    """Return a factory-managed Anthropic SDK handle for *role*.

    The single sanctioned way for raw-Anthropic-SDK callers (vision,
    JSON-mode classification, structured diagnosis, etc.) to obtain a
    client.  See :class:`AnthropicClientHandle` for the call surface.

    Migration shape — before::

        from anthropic import Anthropic
        client = Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            messages=[...],
            max_tokens=1024,
        )

    after::

        from app.llm_factory import anthropic_client_for_role
        client = anthropic_client_for_role(role="cheap-vetting")
        resp = client.messages.create(messages=[...], max_tokens=1024)
        # No model kwarg — the factory picks it.

    Raises :class:`NoWorkingModelAvailable` if every Anthropic
    candidate in the role's chain fails (no key, all marked dead,
    SDK not installed, …).  Catch this exception at the call site if
    the operation has a non-Anthropic fallback path.
    """
    return AnthropicClientHandle(role=role, task_hint=task_hint)


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
        # _AdapterLLM is the only LLM call path in this codebase that doesn't
        # derive from CrewAI's BaseLLM, so CrewAI's event bus never fires
        # LLMCallCompletedEvent / LLMCallFailedEvent for it.  We emit the
        # activity heartbeat explicitly here so the progressive-timeout stall
        # detector in handle_task sees this path as alive.  (The fallback
        # branch below goes through a real crewai.LLM, which the event bus
        # DOES cover — so no second record is needed in that branch.)
        from app.rate_throttle import record_llm_activity
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
            record_llm_activity()
            return result.get("response", "")
        except Exception:
            # Record the failure-as-activity BEFORE falling back, so a task
            # that's legitimately in a retry cycle doesn't look silent.
            record_llm_activity()
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
    from app.llm_mode import get_mode

    settings = get_settings()
    mode = get_mode()

    # Verified Plan §7 Gap A closure (2026-05-23) — local-tier override.
    # If the routing layer marked the current dispatch as
    # ``tier_hint="local"`` (interest-profile-aware queries) and
    # ``_run_crew`` set the ContextVar, force mode="local" so the
    # resolver picks an Ollama model. Cheap dispatch path for queries
    # that don't need cloud reasoning. Failure-isolated: any error
    # falls through to the existing mode (the safe default).
    try:
        from app.llm_selector import get_active_local_tier
        if get_active_local_tier():
            logger.info(
                "create_commander_llm: local-tier override active — "
                "forcing mode='local' for this dispatch",
            )
            mode = "local"
    except Exception:
        logger.debug(
            "create_commander_llm: local-tier check raised",
            exc_info=True,
        )

    # ── Chain walker is the single point of construction ────────────
    # The resolver's pick goes first; bootstrap survivors follow in
    # ``fallback_chain("commander")`` order (premium → budget → local).
    # ``_walk_chain`` consults the health cache, validates entry shape,
    # checks API keys, and falls through cleanly on every recoverable
    # failure.  A fully exhausted chain raises
    # :class:`NoWorkingModelAvailable` which the orchestrator catches
    # explicitly — see ``agents/commander/orchestrator.py``.
    #
    # We route through ``select_model`` (not ``get_default_for_role``
    # directly) so the per-call budget cap engages — commander is the
    # router and runs on every user message, so leaving its budget
    # dimension dormant was the largest single contributor to the
    # "cost not a selection signal" gap.
    from app.llm_selector import select_model
    model_name = select_model(
        "commander", task_hint="",
        budget_usd=_resolved_budget_usd("commander"),
    )
    chain = _chain_for_role("commander", primary=model_name)
    logger.info(
        "create_commander_llm: walking chain %r (resolver pick: %s)",
        chain, model_name,
    )
    return _walk_chain(chain, max_tokens=1024, role="commander")


# Roles that auto-apply a recency floor unless the caller passes
# ``min_recency`` explicitly. Models without a ``knowledge_cutoff`` field
# pass through the filter (treated as unknown), so this is opt-in pressure
# rather than a hard constraint. See app/llm_selector.py:_below_min_recency.
_DEFAULT_RECENCY_DAYS_BY_ROLE: dict[str, int] = {
    "research": 180,      # web research / dossier collector / tech radar
    "self_improve": 180,  # post-mortems benefit from current best-practice
}


# Per-role budget defaults — US dollars per call.  Engages the selector's
# Pareto demotion + budget enforcement which would otherwise be dormant
# because no caller used to pass ``budget_usd``.  Values are conservative
# ceilings — they only fire when a cheaper alternative scores within
# ``quality_gap=0.10`` of the default's blended benchmark.  Operators
# can tighten via the env var ``LLM_FACTORY_BUDGET_OVERRIDE_USD`` (single
# value, applied to every role), or by passing ``budget_usd=`` explicitly
# at the call site.
#
# The per-role budget data lives in :data:`app.llm_role_spend._ROLE_PROFILES`
# alongside the per-role expected-hourly baseline used by the adaptive
# back-pressure — one source of truth for the per-role cost envelope,
# not two parallel tables.


def _resolved_budget_usd(role: str, override: float | None = None) -> float:
    """Return the effective budget USD for *role*.

    Resolution order (first non-None wins):

    1. Explicit ``override`` parameter — the ``budget_usd`` kwarg passed
       by a caller that has a precise estimate.  Must be non-negative.
    2. ``LLM_FACTORY_BUDGET_OVERRIDE_USD`` environment variable — a
       single non-negative value that applies to every role.  Useful for
       operators who want to tighten the global ceiling in cost-saving
       mode without editing per-role defaults.
    3. Per-role row in :data:`_DEFAULT_BUDGET_USD_BY_ROLE`.
    4. :data:`_BUDGET_FALLBACK_USD` catch-all.

    The return type is always ``float`` — there is no opt-out sentinel.
    The selector's Pareto demotion + budget enforcement only fires when
    the default-estimated cost exceeds the budget, so a "loose enough"
    budget is effectively the same as no budget.  If a caller truly
    wants to bypass cost-driven demotion, pass a very large number
    (e.g. ``budget_usd=1_000_000``) — the gate becomes a no-op without
    needing a special-cased None code path.
    """
    if override is not None:
        return max(0.0, float(override))
    import os as _os
    raw = _os.environ.get("LLM_FACTORY_BUDGET_OVERRIDE_USD", "").strip()
    if raw:
        try:
            v = float(raw)
            if v >= 0:
                return v
        except ValueError:
            pass
    from app.llm_role_spend import profile_for
    base = float(profile_for(role).budget_usd)

    # Adaptive back-pressure — when *role* is spending faster than its
    # expected hourly pace (observed via the audit log), the factor
    # tightens the next call's budget so the selector demotes to a
    # cheaper alternative.  Strictly observational; never loosens
    # (under-pace gets the base, not a free pass).  Failure-OPEN.
    try:
        from app import llm_role_spend
        factor = llm_role_spend.adaptive_budget_factor(role)
    except Exception:
        factor = 1.0
    return base * factor


def create_specialist_llm(
    max_tokens: int = 8192,
    role: str = "default",
    task_hint: str = "",
    force_tier: str | None = None,
    phase: str | None = None,
    min_recency: date | None = None,
    budget_usd: float | None = None,
) -> LLM:
    """
    Create an LLM for a specialist role using the tier cascade.

    Behavior depends on current runtime mode (see app.llm_mode.get_mode):
      free      Local + OpenRouter-free only, Claude fallback if empty pool
      budget    Cascade local → cheap cloud APIs (~$1.5/M-out ceiling)
      balanced  Default. Cascade every tier, mild cost preference
      quality   Cascade every tier, strong preference for premium
      insane    Premium only, no cost ceiling, no local
      anthropic Anthropic-only (Haiku/Sonnet/Opus) line-up

    If force_tier is set (e.g. from difficulty-based routing), it overrides
    the default tier selection from llm_selector.

    `phase` (creative-mode only) is one of "diverge"/"discuss"/"converge".
    When set, phase-dependent sampling parameters (temperature/top_p/min_p/
    presence_penalty) are applied. When None, legacy behavior is preserved
    byte-for-byte — including LLM cache identity.

    ``max_tokens`` defaults to 8192 (Week 1 audit fix for H2).  Older
    callers and the prior 4096 default were a leftover from when
    frontier models capped completions at 4K.  All current premium
    tiers (gpt-5.x, claude sonnet 4.6, kimi-k2.6, gemini 2.5) support
    8K+ output, and the 2026-05-02 12:12 dispatch hit the 4K cap on
    its design phase, producing a truncated multi-file project that
    vetting (correctly) flagged as broken.  Callers that genuinely
    need a smaller cap (e.g. ``role="synthesis"`` for tiny skill
    distillations) still pass an explicit value.
    """
    # Q7: thread-local last model/tier tracking
    from app.llm_mode import get_mode
    settings = get_settings()
    mode = get_mode()

    from app.llm_selector import select_model

    # Auto-apply role-based recency floor when the caller didn't override.
    # Sentinel design: callers pass ``min_recency=None`` (default) to opt
    # into the role-keyed default; pass an explicit ``date`` to override;
    # pass ``min_recency=date.min`` to opt out entirely.
    if min_recency is None:
        if (days := _DEFAULT_RECENCY_DAYS_BY_ROLE.get(role)):
            min_recency = date.today() - timedelta(days=days)
    elif min_recency == date.min:
        min_recency = None  # explicit opt-out

    # ── Select the primary candidate ────────────────────────────────
    # Single call into the selector for every mode.  The mode-pool
    # invariant (free / budget / quality / insane / anthropic restrict
    # the allowed tiers + providers) is enforced inside
    # ``select_model`` via Step 5.7 (``model_in_mode_pool``); the old
    # ``_pool_constrained_select`` parallel path was deleted as
    # patchwork.
    #
    # ``budget_usd`` is the per-call USD ceiling that engages the
    # selector's Pareto demotion + budget enforcement steps
    # (llm_selector.py Steps 4b-4c).  We resolve through
    # ``_resolved_budget_usd`` so a caller can override per-call, an
    # operator can override globally via env, or — by default — the
    # per-role table fires.  Pass ``budget_usd=-1.0`` to opt out of
    # the budget gate entirely (the existing soft Pareto-by-quality-
    # gap still runs).
    effective_budget = _resolved_budget_usd(role, override=budget_usd)
    model_name = select_model(
        role, task_hint, force_tier=force_tier, min_recency=min_recency,
        budget_usd=effective_budget,
    )

    # ── Walk the chain ──────────────────────────────────────────────
    # The walker tries the primary first, then the bootstrap survivors
    # (premium → budget → local).  Each candidate is shape-validated,
    # health-cache-checked, API-key-checked, and provider-dispatched
    # uniformly — see ``_construct_from_entry`` for the per-candidate
    # contract.  A fully exhausted chain raises
    # :class:`NoWorkingModelAvailable` rather than fabricating a Claude
    # call that might itself fail.
    chain = _chain_for_role(role, primary=model_name)
    logger.info(
        "create_specialist_llm: role=%s mode=%s walking chain %r",
        role, mode, chain,
    )
    return _walk_chain(chain, max_tokens=max_tokens, role=role, phase=phase)


def create_vetting_llm() -> LLM:
    """Vetting gate — uses the resolver's pick for the ``vetting`` role.

    The ``VETTING_MODEL`` env var is NOT consulted — it was a piece of
    hand-curation that bypassed the resolver and the overlay. If you
    need to pin vetting to a specific model, install a row in
    ``control_plane.role_assignments`` (via Signal / governance
    approval). The resolver + overlay are the single source of truth.

    Construction is delegated to :func:`_walk_chain` — the resolver's
    pick goes first, then bootstrap survivors in standard order.
    Per-call budget engaged via ``_resolved_budget_usd("vetting")``.
    """
    from app.llm_selector import select_model

    model_name = select_model(
        "vetting", task_hint="",
        budget_usd=_resolved_budget_usd("vetting"),
    )
    chain = _chain_for_role("vetting", primary=model_name)
    logger.info(
        "create_vetting_llm: walking chain %r (resolver pick: %s)",
        chain, model_name,
    )
    return _walk_chain(chain, max_tokens=4096, role="vetting")


def create_cheap_vetting_llm() -> LLM:
    """Cheap verification gate — budget model for quick yes/no quality checks.

    Routed through ``select_model`` like the other three entry points so
    the cheap-vetting budget (``_DEFAULT_BUDGET_USD_BY_ROLE["cheap-vetting"]
    = $0.005``) actually engages the selector's Pareto demote +
    budget-enforcement steps.  ``force_tier="budget"`` keeps the
    deliberately-cheap intent — the selector picks the highest-scoring
    budget-tier model that fits the cap, and the chain walker falls
    through to premium / local survivors if the budget tier is dead.
    """
    from app.llm_selector import select_model
    model_name = select_model(
        "cheap-vetting", task_hint="",
        force_tier="budget",
        budget_usd=_resolved_budget_usd("cheap-vetting"),
    )
    chain = _chain_for_role("cheap-vetting", primary=model_name)
    logger.info(
        "create_cheap_vetting_llm: walking chain %r (resolver pick: %s)",
        chain, model_name,
    )
    return _walk_chain(chain, max_tokens=256, role="cheap-vetting")


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
    """Return (llm_kwargs, cache_key) for phase+provider. ({}, '') when phase is None.

    Reads the latest affect snapshot via `app.affect.core.latest_affect()`
    and forwards it to `build_llm_kwargs` so phase-aware
    temperature / top_p modulation actually fires on the LLM hot path.
    Affect import is lazy + exception-safe so the sampling path stays
    byte-identical to legacy behaviour when the affect layer is
    disabled or hasn't yet computed an affect frame.
    """
    if phase is None:
        return {}, ""
    from app.llm_sampling import build_llm_kwargs, sampling_cache_key

    affect_state: dict | None = None
    affect_key_part = ""
    try:
        from app.affect.core import latest_affect
        s = latest_affect()
        if s is not None:
            affect_state = s.to_dict()
            # Coarse cache-key bucket: round V/A to 0.1 so equivalent
            # affect states share kwargs cache entries instead of
            # producing per-call uniques. Attractor name is stable
            # within a band, so include it too.
            v = round(float(affect_state.get("valence", 0.0)), 1)
            a = round(float(affect_state.get("arousal", 0.0)), 1)
            attractor = str(affect_state.get("attractor", "neutral"))[:16]
            affect_key_part = f"|{attractor}|v={v}|a={a}"
    except Exception:
        # Affect layer not installed or first call before any
        # POST_LLM_CALL — fall through to legacy unmodulated path.
        pass

    base_key = sampling_cache_key(phase, provider)
    cache_key = base_key + affect_key_part if base_key else base_key
    return build_llm_kwargs(phase, provider, affect_state), cache_key


# ── Mode-pool enforcement lives in the selector ─────────────────────
# The previous ``_pool_constrained_select`` / ``_mode_pool`` /
# ``_entry_in_pool`` helpers were a second, parallel selection path
# the factory ran for non-balanced modes.  They were patchwork — two
# ways to pick a model is exactly the shape the user flagged as
# "not elegant".  Removed in favour of a single in-selector check at
# ``app.llm_selector.select_model`` Step 5.7 that calls
# ``app.llm_catalog.model_in_mode_pool`` and swaps out an out-of-pool
# pick before returning.  See ``llm_selector.py:select_model``.


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
        # Route the call through OpenRouter's LiteLLM provider rather
        # than CrewAI's native Anthropic provider.  ``derived_id`` with
        # ``route="openrouter"`` is the single source of truth for the
        # shape — Anthropic entries get translated (dash→dot version,
        # ``openrouter/anthropic/...`` prefix), OpenRouter entries are
        # identity.  Historical context: pre-2026-05-10 the code
        # string-mangled the prefix inline here, which silently mis-
        # routed Anthropic-provider entries to the native Anthropic
        # SDK against an OpenRouter base URL, surfacing as ``'str'
        # object has no attribute 'content'`` (T3.3 in the Ops anomaly
        # dashboard).  Centralising the transformation in
        # ``derived_id`` eliminates that class of bug.
        or_model_id = derived_id(entry, "openrouter")

        # Wrap the LiteLLM-routed LLM with the per-call budget gate.
        # This brings OpenRouter to per-call cap parity with the
        # Anthropic path (which gets per-call via
        # ``CreditAwareAnthropicCompletion``).  The wrapper is
        # provider-agnostic — see ``app/llms/budget_aware.py``.
        cost_in = float(entry.get("cost_input_per_m", 0.0) or 0.0)
        cost_out_per_m = float(
            entry.get("cost_output_per_m", 0.0) or 0.0
        )

        def _budget_aware_builder(mid: str, mt: int, **kw):
            from app.llms.budget_aware import BudgetAwareCompletion
            from app import llm_openrouter_budget
            # is_litellm=True forces crewai.LLM.__new__ to skip native-provider
            # dispatch (crewai/llm.py:361) and return our BudgetAwareCompletion
            # subclass via LiteLLM — which is what this OpenRouter path intends.
            # Without it __new__ returns a bare OpenAICompatibleCompletion and
            # the set_budget_module() call below raised AttributeError
            # (~2.6k failures/day → silent Ollama failover).
            llm = BudgetAwareCompletion(model=mid, max_tokens=mt, is_litellm=True, **kw)
            # 2000-token input heuristic, same as CreditAware's
            # estimator.  Per-call max output bounded by mt.
            def _estimate() -> float:
                if cost_out_per_m <= 0:
                    return 0.0
                return (
                    2000 * cost_in + mt * cost_out_per_m
                ) / 1_000_000.0
            llm.set_budget_module(llm_openrouter_budget)
            llm.set_estimated_cost_fn(_estimate)
            return llm

        return _cached_llm(
            or_model_id, max_tokens=max_tokens,
            sampling_key=key,
            llm_builder=_budget_aware_builder,
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key, **extra,
        )
    except Exception as exc:
        circuit_breaker.record_failure("openrouter")
        logger.warning(f"llm_factory: API {model_name} failed: {exc}")
        _set_last(None, None)
    return None


# ── Anthropic-direct LLM factory with credit-exhausted failover ─────────
#
# When the Anthropic API returns
#     400 invalid_request_error "Your credit balance is too low..."
# we fail over to the same Claude model served via OpenRouter.  Authoritative
# state lives in circuit_breaker["anthropic_credits"] (threshold 1, 3600s
# cooldown) — tripping is idempotent and visible to every LLM factory in the
# process; auto-recovery happens when the breaker transitions to HALF_OPEN
# and the next Anthropic probe succeeds.  No monkey-patching, no global
# mutable flags: just a typed subclass (CreditAwareAnthropicCompletion) and
# the existing circuit-breaker infrastructure.


def _build_claude_via_openrouter(
    model_name: str,
    model_id: str,
    max_tokens: int,
    *,
    role: str,
    phase: str | None,
    tier: str = "premium",
    cost_out: float = 15.0,
) -> "LLM":
    """Build a Claude LLM routed through OpenRouter.

    Used in two places:
      * Direct substitute when the anthropic_credits breaker is OPEN
      * Lazy fallback target built by CreditAwareAnthropicCompletion
        on the first mid-call 400 we see

    Model-id translation is delegated to :func:`derived_id` with
    ``route="openrouter"`` — the catalog is the single source of truth
    for shape transformations.  No regex lives in the factory.
    """
    or_key = get_openrouter_api_key()
    if not or_key:
        raise RuntimeError(
            "Anthropic credits exhausted AND OPENROUTER_API_KEY is unset — "
            "cannot serve Claude requests. Top up Anthropic or set "
            "OPENROUTER_API_KEY to enable the failover route."
        )
    entry_for_route = get_model(model_name) or {
        "model_id": model_id, "provider": "anthropic",
    }
    or_model_id = derived_id(entry_for_route, "openrouter")
    _set_last(f"{model_name} (via OpenRouter)", tier)
    logger.info(
        "llm_factory: role=%s → OPENROUTER %s (~$%.2f/Mo; anthropic_credits breaker=%s)",
        role, or_model_id, cost_out,
        circuit_breaker.get_breaker("anthropic_credits").state,
    )
    extra, sample_key = _sampling(phase, "openrouter")

    # Same per-call OR cap wrapping as ``_try_api`` — the Claude-via-
    # OpenRouter failover path is a regular OR call, so the OR daily
    # cap should govern it too.
    cost_in_or = float(entry_for_route.get("cost_input_per_m", 0.0) or 0.0)
    cost_out_or = float(entry_for_route.get("cost_output_per_m", cost_out) or cost_out)

    def _budget_aware_or_builder(mid: str, mt: int, **kw):
        from app.llms.budget_aware import BudgetAwareCompletion
        from app import llm_openrouter_budget
        # is_litellm=True: see _budget_aware_builder above — keeps the
        # BudgetAwareCompletion subclass (and its per-call budget gate) intact
        # instead of crewai returning a bare OpenAICompatibleCompletion.
        llm = BudgetAwareCompletion(model=mid, max_tokens=mt, is_litellm=True, **kw)
        def _estimate() -> float:
            if cost_out_or <= 0:
                return 0.0
            return (2000 * cost_in_or + mt * cost_out_or) / 1_000_000.0
        llm.set_budget_module(llm_openrouter_budget)
        llm.set_estimated_cost_fn(_estimate)
        return llm

    return _cached_llm(
        or_model_id, max_tokens=max_tokens, sampling_key=sample_key,
        llm_builder=_budget_aware_or_builder,
        base_url="https://openrouter.ai/api/v1", api_key=or_key, **extra,
    )


def _build_claude_llm(
    model_name: str,
    model_id: str,
    max_tokens: int,
    *,
    role: str,
    phase: str | None = None,
    tier: str = "premium",
    cost_out: float = 15.0,
) -> "LLM":
    """The single, elegant Claude factory for this module.

    Routing rule:
      * ``circuit_breaker["anthropic_credits"]`` OPEN
          → direct Anthropic is known-unavailable; build via OpenRouter now.
      * else
          → build a CreditAwareAnthropicCompletion (proper BaseLLM subclass,
            passes Agent Pydantic validation) with an injected fallback
            factory.  If the first call fails with credit-exhausted the
            subclass trips the breaker, builds the OR equivalent, and
            retries transparently.  All subsequent calls on that instance
            use the OR path directly.

    This is the only entry point for Anthropic-direct LLM construction
    in this module.  Every Anthropic candidate visited by
    :func:`_walk_chain` funnels through here via
    :func:`_construct_from_entry` so the credit-aware failover policy is
    applied uniformly.
    """
    # Lazy import: CreditAwareAnthropicCompletion depends on crewai.LLM
    # which we defer per the module's cold-boot discipline (see
    # `_get_LLM_class`).  Putting the import here keeps the llm_factory
    # import graph flat.
    from app.llms.credit_aware_anthropic import CreditAwareAnthropicCompletion

    # Derive the model-id form the native Anthropic SDK actually accepts.
    # The catalog stores ``model_id`` in the LiteLLM-canonical (provider-
    # prefixed) shape — ``anthropic/claude-sonnet-4-6`` — because every
    # other consumer of ``model_id`` (cost lookup, discovered_models PK,
    # governance remaps, telemetry tags, OpenRouter remap) keys on that
    # form.  But ``CreditAwareAnthropicCompletion`` extends CrewAI's
    # *native* ``AnthropicCompletion`` which forwards ``model=`` straight
    # to the Anthropic SDK; the SDK only knows the bare id and 404s on
    # the prefixed form.  See ``app/llm_catalog.py:derived_id`` for the
    # full per-route shape contract.
    entry_for_route = get_model(model_name) or {"model_id": model_id, "provider": "anthropic"}
    bare_id = derived_id(entry_for_route, "native_anthropic")

    def _or_fallback():
        return _build_claude_via_openrouter(
            model_name, model_id, max_tokens,
            role=role, phase=phase, tier=tier, cost_out=cost_out,
        )

    if not circuit_breaker.is_available("anthropic_credits"):
        logger.info(
            "llm_factory: role=%s → OpenRouter Claude (anthropic_credits "
            "breaker OPEN, %0.0fs to reprobe)",
            role,
            circuit_breaker.get_breaker("anthropic_credits").seconds_until_half_open(),
        )
        return _or_fallback()

    _set_last(model_name, tier)
    logger.info(
        "llm_factory: role=%s → ANTHROPIC %s ($%.2f/Mo) + credit-aware failover",
        role, model_name, cost_out,
    )
    extra, sample_key = _sampling(phase, "anthropic")

    # Go through _cached_llm with a CreditAware builder — entries get
    # keyed as (builder=CreditAware, model_id, max_tokens, ...) so they
    # don't collide with default crewai.LLM entries for the same model.
    # Cache-safe because the subclass consults the credit breaker on
    # every call (no sticky per-instance failover state that would
    # break auto-recovery after a shared cached hand-off).
    #
    # The builder closure captures ``bare_id`` and ignores the LiteLLM-
    # canonical id ``_cached_llm`` forwards (its first arg).  That
    # forwarded value is still used by ``_cached_llm`` for the cache
    # key — keeping the cache namespace aligned with every other code
    # path that looks up ``(builder, model_id, max_tokens, …)``.  The
    # construction parameter and the cache-key parameter are deliberately
    # separated to keep one consistent identity per logical model across
    # routes.
    # Capture catalog cost fields for the per-call pre-check estimate.
    # If the entry is missing (legacy callers passing model_id without
    # a catalog lookup), defaults to 0.0 — pre_check then degrades to
    # its previous "0.0 placeholder" behaviour, never blocking calls
    # on missing data.
    cost_in = float(entry_for_route.get("cost_input_per_m", 0.0) or 0.0)
    cost_out_per_m = float(entry_for_route.get("cost_output_per_m", cost_out) or cost_out)

    def _credit_aware_builder(
        _litellm_canonical_id: str,
        mt: int,
        **kw,
    ) -> CreditAwareAnthropicCompletion:
        llm = CreditAwareAnthropicCompletion(model=bare_id, max_tokens=mt, **kw)
        llm.set_cost_estimates(cost_in, cost_out_per_m)
        return llm.set_fallback_factory(_or_fallback)

    return _cached_llm(
        model_id, max_tokens,
        sampling_key=sample_key,
        llm_builder=_credit_aware_builder,
        api_key=get_anthropic_api_key(),
        **extra,
    )


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

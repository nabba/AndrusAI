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
  free, budget, balanced [default], quality, insane
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from crewai import LLM  # type hints only — no runtime import cost
from app.config import get_settings, get_openrouter_api_key
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


def _apply_openrouter_provider_exclusion(kwargs: dict) -> None:
    """Add OpenRouter's documented provider-routing ``ignore`` list to
    ``kwargs["extra_body"]`` in place.

    OpenRouter's anonymous "Stealth" sub-provider class periodically
    returns 502 ``Invalid URL: ''``; excluding it via OpenRouter's
    provider-routing API is a reliability gain with no functional loss
    (active role-assigned models all have non-Stealth routes).  Override
    via the ``OPENROUTER_IGNORE_PROVIDERS`` env var (CSV); set it empty
    to disable filtering.

    Shared by the CrewAI-LLM path (:func:`_cached_llm`) and the raw
    completion path (:class:`ChatCompletionHandle`) so the exclusion
    policy lives in exactly one place.
    """
    import os as _os
    env_ignore = _os.environ.get("OPENROUTER_IGNORE_PROVIDERS", "Stealth")
    ignore_list = [n.strip() for n in env_ignore.split(",") if n.strip()]
    if not ignore_list:
        return
    extra_body = dict(kwargs.pop("extra_body", {}) or {})
    provider_pref = dict(extra_body.get("provider", {}) or {})
    existing = list(provider_pref.get("ignore", []) or [])
    for name in ignore_list:
        if name not in existing:
            existing.append(name)
    provider_pref["ignore"] = existing
    extra_body["provider"] = provider_pref
    kwargs["extra_body"] = extra_body


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
        ``BudgetAwareCompletion``).  Called as
        ``llm_builder(model_id, max_tokens, **kwargs)``.  If omitted,
        the default ``crewai.LLM`` constructor is used.

        NOTE: cached instances must behave correctly under every call —
        no sticky per-instance state that would break auto-recovery /
        shared-state contracts.  Our ``BudgetAwareCompletion`` subclass
        satisfies this because it consults the per-call budget module on
        every ``call()``, so a cached instance always routes correctly
        even after the cap state changes.

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
            _apply_openrouter_provider_exclusion(kwargs)

        if llm_builder is not None:
            llm = llm_builder(model_id, max_tokens, **kwargs)
        else:
            # Force litellm dispatch for OpenRouter models. Without
            # ``is_litellm=True`` CrewAI's ``LLM.__new__`` routes
            # ``openrouter/…`` ids to its NATIVE ``OpenAICompatibleCompletion``
            # (a direct ``openai`` SDK call that bypasses ``litellm.completion``
            # and therefore the rate_throttle throttle + credit-failover +
            # token-recording wrapper). Routing through litellm makes that
            # wrapper the single, uniform network layer — and is what lets us
            # drop the separate openai-SDK monkeypatch. The BudgetAware builder
            # path above already sets this; here we cover the default path
            # (discovery probes, orchestrator fallback, model vetting, …).
            if (model_id or "").startswith("openrouter/"):
                kwargs.setdefault("is_litellm", True)
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
    :func:`_resolve_raw_target` (the raw ``chat_completion_for_role``
    path) so the two factory surfaces share validation logic rather
    than diverging.

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

    # Total-spend brake (paid providers only — OpenRouter is the only
    # paid provider now; Ollama is free, the computer-use island is the
    # only native-Anthropic surface and does not route through here).
    if provider == "openrouter":
        try:
            if get_idle_pause_due_to_budget():
                return ConstructionFailed(
                    "budget_paused",
                    "idle_pause_due_to_budget is engaged — skipping "
                    "openrouter candidate to honour the monthly cap; "
                    "chain walker will fall through to local Ollama",
                )
        except Exception:
            pass

    # Provider-specific API-key check
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
    # already passed inside ``_check_candidate_basics``.  There are
    # exactly two providers: OpenRouter (network) and Ollama (local).
    # Claude is reached via OpenRouter like any other model; the sole
    # native-Anthropic surface is the computer-use island, which never
    # routes through the factory.
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


# ── Raw OpenAI-compatible completion surface (OpenRouter + Ollama) ───
#
# ``chat_completion_for_role`` is the factory-supplied, provider-uniform
# replacement for ``anthropic_client_for_role``.  Where the Anthropic
# handle spoke the native Messages dialect (``.messages.create`` →
# ``msg.content[0].text``), this handle speaks the OpenAI
# chat-completions dialect that BOTH our providers understand:
# OpenRouter (cloud) and Ollama (local).  litellm is the single
# transport — it normalises ``openrouter/…`` and ``ollama_chat/…``
# model ids to one response shape.
#
# Selection reuses the exact same primitives as the CrewAI-LLM path
# (``select_model`` → ``_chain_for_role`` → ``_check_candidate_basics``
# → ``derived_id``) so the raw surface and the agent surface never
# diverge on which model a role resolves to.  Per-call budget (the
# OpenRouter daily cap) and the model-id health cache are applied with
# the same primitives the rest of the factory uses.
#
# Migration shape — before::
#
#     client = anthropic_client_for_role(role="cheap-vetting")
#     msg = client.messages.create(system=S, messages=[...], max_tokens=N)
#     text = "".join(b.text for b in msg.content if hasattr(b, "text"))
#
# after::
#
#     client = chat_completion_for_role(role="cheap-vetting")
#     resp = client.create(system=S, messages=[...], max_tokens=N)
#     text = resp.choices[0].message.content or ""


@dataclass
class _RawTarget:
    """A resolved, ready-to-call completion target for the raw path.

    ``provider`` is the *call* provider (``"openrouter"`` or
    ``"ollama"``) — for a catalog entry whose ``provider`` is
    ``"anthropic"`` we route via OpenRouter (``derived_id(.., "openrouter")``)
    so Claude is reachable uniformly even before the catalog flip.
    """
    catalog_key: str
    provider: str
    model_id: str          # litellm-canonical id to send
    api_key: str | None
    api_base: str | None
    cost_in: float
    cost_out: float
    # Health-cache identity.  Keyed by the *catalog* entry's provider +
    # its ``native_anthropic``-route bare id — the exact key
    # ``_check_candidate_basics`` reads — so a dead-mark recorded here
    # makes the next ``_resolve_raw_target`` skip the dead candidate.
    health_provider: str
    bare: str


def _resolve_raw_target(role: str, task_hint: str = "") -> "_RawTarget":
    """Walk *role*'s fallback chain; return the first constructible
    network/local target as a :class:`_RawTarget` descriptor.

    Raises :class:`NoWorkingModelAvailable` if every candidate fails —
    the same contract as :func:`_walk_chain`, so callers that already
    catch it for the CrewAI path keep working unchanged.

    Anthropic-provider entries (present only during the migration
    window, before the catalog flip) are served via OpenRouter rather
    than skipped, so a migrated caller gets the role's intended Claude
    model immediately.  Post-flip the catalog has no anthropic-provider
    entries and this branch is simply never taken.
    """
    from app.llm_selector import select_model

    primary = select_model(
        role, task_hint, budget_usd=_resolved_budget_usd(role),
    )
    chain = _chain_for_role(role, primary=primary)
    attempts: list[tuple[str, ConstructionFailed]] = []
    seen: set[str] = set()
    for name in chain:
        if name in seen:
            continue
        seen.add(name)
        entry = get_model(name)
        if entry is None:
            attempts.append((name, ConstructionFailed("not_in_catalog", "")))
            continue
        fail = _check_candidate_basics(name, entry)
        if fail is not None:
            attempts.append((name, fail))
            continue
        provider = entry["provider"]
        cost_in = float(entry.get("cost_input_per_m", 0.0) or 0.0)
        cost_out = float(entry.get("cost_output_per_m", 0.0) or 0.0)
        bare = derived_id(entry, "native_anthropic")
        if provider in ("openrouter", "anthropic"):
            or_key = get_openrouter_api_key()
            if not or_key:
                attempts.append(
                    (name, ConstructionFailed(
                        "missing_key", "OPENROUTER_API_KEY not set",
                    ))
                )
                continue
            return _RawTarget(
                catalog_key=name, provider="openrouter",
                model_id=derived_id(entry, "openrouter"),
                api_key=or_key, api_base=None,
                cost_in=cost_in, cost_out=cost_out,
                health_provider=provider, bare=bare,
            )
        if provider == "ollama":
            if not get_settings().local_llm_enabled:
                attempts.append(
                    (name, ConstructionFailed("disabled", "local_llm_enabled=False"))
                )
                continue
            try:
                from app.ollama_native import spawn_model
                url = spawn_model(name)
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    (name, ConstructionFailed("build_failed", f"ollama spawn: {exc!s}"))
                )
                continue
            if not url:
                attempts.append(
                    (name, ConstructionFailed("build_failed", "ollama spawn returned None"))
                )
                continue
            return _RawTarget(
                catalog_key=name, provider="ollama",
                model_id=entry["model_id"], api_key=None, api_base=url,
                cost_in=cost_in, cost_out=cost_out,
                health_provider=provider, bare=bare,
            )
        attempts.append(
            (name, ConstructionFailed("unknown_provider", f"provider={provider!r}"))
        )
    raise NoWorkingModelAvailable(role, attempts)


class ChatCompletionHandle:
    """Factory-managed raw completion handle over OpenRouter / Ollama.

    Exposes :meth:`create` with an OpenAI-chat shape.  Each call
    re-resolves the role's target (cheap — selection is cached) so the
    handle always reflects the latest health-cache and budget state,
    matching the per-call freshness the Anthropic handle provided.
    """

    def __init__(self, role: str, task_hint: str = ""):
        self._role = role
        self._task_hint = task_hint

    def create(
        self,
        *,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 4096,
        **kwargs,
    ):
        """Issue one chat completion and return the litellm
        ``ModelResponse`` (read ``resp.choices[0].message.content``).

        ``system`` is prepended as a ``role="system"`` message — the
        OpenAI-dialect equivalent of the Anthropic ``system=`` kwarg.
        Extra kwargs (``temperature``, ``top_p``, ``stop``, …) pass
        straight through to ``litellm.completion``.
        """
        target = _resolve_raw_target(self._role, self._task_hint)

        msgs: list[dict] = []
        if system is not None:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        # Mark the long system prompt for OpenRouter prompt caching
        # (replaces the retired prompt_cache_hook litellm monkeypatch).
        try:
            from app.llm_cache_control import inject_cache_control
            msgs = inject_cache_control(msgs, target.model_id)
        except Exception:
            pass

        # OpenRouter per-call daily-cap gate — parity with the agent
        # path's ``BudgetAwareCompletion``.  Failure-OPEN on anything
        # that isn't a typed cap-exceeded.
        if target.provider == "openrouter":
            try:
                from app.llm_openrouter_budget import pre_check as _or_pre_check
                est = (2000 * target.cost_in + max_tokens * target.cost_out) / 1_000_000.0
                _or_pre_check(estimated_cost_usd=est)
            except Exception as exc:  # noqa: BLE001
                from app.llm_cost_exceptions import CapExceededError
                if isinstance(exc, CapExceededError):
                    raise
            _apply_openrouter_provider_exclusion(kwargs)

        call_kwargs: dict = {
            "model": target.model_id,
            "messages": msgs,
            "max_tokens": max_tokens,
        }
        if target.api_key:
            call_kwargs["api_key"] = target.api_key
        if target.api_base:
            call_kwargs["api_base"] = target.api_base
        call_kwargs.update(kwargs)

        import litellm
        try:
            resp = litellm.completion(**call_kwargs)
        except BaseException as exc:
            reason = llm_factory_probe.classify_failure(exc)
            if reason:
                llm_factory_probe.mark_dead(
                    target.health_provider, target.bare, reason,
                )
            raise
        llm_factory_probe.mark_alive(target.health_provider, target.bare)
        return resp


def chat_completion_for_role(
    role: str,
    task_hint: str = "",
) -> ChatCompletionHandle:
    """Return a factory-managed raw completion handle for *role*.

    The single sanctioned way to make a raw, non-CrewAI LLM call —
    routed through OpenRouter (cloud) or Ollama (local) via litellm.
    Replaces :func:`anthropic_client_for_role`.  See
    :class:`ChatCompletionHandle` for the call surface.
    """
    return ChatCompletionHandle(role=role, task_hint=task_hint)


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

        # Wrap the LiteLLM-routed LLM with the per-call budget gate
        # (``BudgetAwareCompletion``) — the single per-call cap layer
        # for all network traffic.  See ``app/llms/budget_aware.py``.
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

    openrouter_ok = is_available("openrouter")
    ollama_ok = is_available("ollama")

    any_available = openrouter_ok or ollama_ok

    if not any_available and not _all_providers_exhausted:
        _all_providers_exhausted = True
        logger.warning(
            "All LLM circuit breakers OPEN — orchestrator will force-probe "
            "(openrouter=%s, ollama=%s)",
            "open", "open",
        )
    elif any_available and _all_providers_exhausted:
        _all_providers_exhausted = False
        logger.info("LLM provider recovered — circuit breakers back to normal")

    return any_available

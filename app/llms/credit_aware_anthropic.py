"""
credit_aware_anthropic.py — a subclass of CrewAI's native Anthropic LLM
provider that handles "Your credit balance is too low" failures cleanly.

Motivation
----------
When the Anthropic API returns::

    400 invalid_request_error
        "Your credit balance is too low to access the Anthropic API.
         Please go to Plans & Billing to upgrade or purchase credits."

the generic `circuit_breaker["anthropic"]` breaker (tuned for transient
glitches: threshold 8, 45s cooldown) is wrong — credit exhaustion is
authoritative on the first occurrence and takes the operator's action
to resolve.  A dedicated `circuit_breaker["anthropic_credits"]` breaker
(threshold 1, 3600s cooldown) represents that semantic precisely.

Design
------
`CreditAwareAnthropicCompletion` is a *proper* subclass of
`crewai.llms.providers.anthropic.completion.AnthropicCompletion` — it
passes every interface-level check Pydantic runs against an
``Agent.llm: str | BaseLLM`` field (which a wrapper class doesn't — that
was the previous attempt's bug: a wrapper class fails Pydantic
validation with "Input should be a valid string").

Behaviour is a single, standard override of ``call()``:

  1. If an OpenRouter-backed fallback has already been built on this
     instance, every subsequent call goes straight to it.
  2. Otherwise: delegate to the parent ``AnthropicCompletion.call()``.
  3. On a 400 whose message matches the credit-exhausted signature:
       - Trip ``circuit_breaker["anthropic_credits"]`` (process-wide
         authoritative state; all other LLM factories read this).
       - Build the fallback LLM via the injected factory and retry the
         same call through it transparently.
  4. All other exceptions propagate unchanged — we don't catch generic
     400s, rate-limits, or network errors, those belong to the existing
     circuit breaker and retry layers.

Thread safety
-------------
``_fallback_build_lock`` serialises the one-shot failover build so two
concurrent calls that both see the 400 don't race to construct the
fallback LLM twice.

No monkey-patching.  No global mutable flags.  All state is
instance-scoped (the fallback LLM) or circuit-breaker-scoped (the
authoritative "credits exhausted" boolean).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from pydantic import PrivateAttr

from crewai.llms.providers.anthropic.completion import AnthropicCompletion

logger = logging.getLogger(__name__)


# Phrases whose presence in an Anthropic 400 body proves the account is
# out of credits rather than any other class of BadRequest.  We match
# case-insensitively and require both substrings AND-together to avoid
# false positives on unrelated "too low" copy (e.g. temperature error).
_CREDIT_EXHAUSTED_MARKERS = ("credit balance", "too low")


def is_credit_exhausted_error(exc: BaseException) -> bool:
    """Return True iff the exception represents the Anthropic 400
    'Your credit balance is too low' response.

    Exposed as a module-level helper so the factory layer can reuse
    the same detection when deciding whether to trip the breaker from
    outside a CreditAware LLM (e.g. if some other code path catches
    the exception first).
    """
    msg = str(exc).lower()
    return all(marker in msg for marker in _CREDIT_EXHAUSTED_MARKERS)


class CreditAwareAnthropicCompletion(AnthropicCompletion):
    """Anthropic-direct LLM that fails over to a caller-supplied
    OpenRouter equivalent on credit-exhausted 400.

    The fallback factory must be injected *after* construction via
    :meth:`set_fallback_factory`.  (We avoid declaring it as a
    constructor argument because CrewAI's ``AnthropicCompletion.__init__``
    has a rigid signature and injecting non-standard kwargs there is
    fragile across library upgrades.)

    The fallback is built lazily on first credit-exhausted error.  Once
    built, all subsequent ``call()`` invocations on this instance skip
    the direct Anthropic path entirely.
    """

    # Pydantic private attrs — not part of the public schema, not
    # validated, but supported by `model_config.arbitrary_types_allowed`
    # inherited from the parent.
    _fallback_factory: Optional[Callable[[], Any]] = PrivateAttr(default=None)
    _fallback_llm: Optional[Any] = PrivateAttr(default=None)
    _fallback_build_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def set_fallback_factory(
        self, factory: Callable[[], Any]
    ) -> "CreditAwareAnthropicCompletion":
        """Attach the factory that builds an OpenRouter-backed
        equivalent on credit exhaustion.  Returns self for chaining so
        the factory call site stays a single expression.
        """
        self._fallback_factory = factory
        return self

    # ── Overrides ───────────────────────────────────────────────────
    #
    # Routing rule on every call (not just "first miss"):
    #   * anthropic_credits breaker OPEN  → skip direct Anthropic, use
    #     the already-built fallback LLM.  The breaker transitions
    #     OPEN→HALF_OPEN after its cooldown, at which point the next
    #     call probes direct Anthropic again.
    #   * breaker CLOSED or HALF_OPEN     → try direct Anthropic.  On a
    #     credit-exhausted 400, trip the breaker, lazily build the
    #     fallback (one-shot), and retry through it.
    #
    # Checking the breaker per-call — rather than caching a sticky
    # "failed over" flag on the instance — is what lets
    # ``_cached_llm`` cache instances of this class safely: a cached
    # instance always observes current breaker state, so auto-recovery
    # after credits are restored works without invalidating caches.

    def call(self, *args, **kwargs):
        from app import circuit_breaker
        if not circuit_breaker.is_available("anthropic_credits"):
            # Breaker already open — use fallback directly, no direct probe.
            fallback = self._ensure_fallback()
            if fallback is not None:
                return fallback.call(*args, **kwargs)
            # No fallback configured: try direct and let the error speak.

        try:
            return super().call(*args, **kwargs)
        except Exception as exc:
            if not is_credit_exhausted_error(exc):
                raise
            # FRESH credit-exhausted signal: trip the breaker once (here,
            # not inside _ensure_fallback, so we don't reset the cooldown
            # clock on instances that encounter an already-open breaker).
            circuit_breaker.record_failure("anthropic_credits")
            fallback = self._ensure_fallback()
            if fallback is None:
                raise
            logger.warning(
                "CreditAwareAnthropicCompletion: credit-exhausted 400 from "
                "Anthropic — failing over mid-call to OpenRouter Claude."
            )
            return fallback.call(*args, **kwargs)

    async def acall(self, *args, **kwargs):
        from app import circuit_breaker
        if not circuit_breaker.is_available("anthropic_credits"):
            fallback = self._ensure_fallback()
            if fallback is not None:
                return await fallback.acall(*args, **kwargs)

        try:
            return await super().acall(*args, **kwargs)
        except Exception as exc:
            if not is_credit_exhausted_error(exc):
                raise
            circuit_breaker.record_failure("anthropic_credits")
            fallback = self._ensure_fallback()
            if fallback is None:
                raise
            logger.warning(
                "CreditAwareAnthropicCompletion: credit-exhausted 400 from "
                "Anthropic (async) — failing over mid-call to OpenRouter Claude."
            )
            return await fallback.acall(*args, **kwargs)

    # ── Internals ───────────────────────────────────────────────────

    def _ensure_fallback(self):
        """Build the OR fallback under a lock (one-shot, thread-safe).

        Breaker tripping is the caller's responsibility — this method
        only builds the LLM.  Keeping breaker manipulation at the call
        site prevents the cooldown timer from being reset when an
        already-open breaker is encountered by a fresh cached instance.
        """
        if self._fallback_llm is not None:
            return self._fallback_llm
        with self._fallback_build_lock:
            if self._fallback_llm is not None:
                return self._fallback_llm
            if self._fallback_factory is None:
                return None
            self._fallback_llm = self._fallback_factory()
            return self._fallback_llm

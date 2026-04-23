"""Tests for the credit-error → local-Ollama failover in rate_throttle.

Proves:

* A credit error (402 / "insufficient credits") routes to the local
  failover path and returns the local response on success.
* A non-credit error (network timeout, 500, etc.) does NOT trigger
  failover and propagates normally.
* The failover is **one-shot** — if the local retry also fails, the
  original error is propagated (no infinite loop).
* The ContextVar re-entrance guard prevents a failover from itself
  being wrapped in another failover.
* When no local model is available, failover short-circuits and the
  original error propagates.
* ``max_tokens`` is capped to the failover limit so local models
  don't get asked for more than they can produce.
* The ``num_retries`` field is cleared on failover so LiteLLM's retry
  loop doesn't hang the fallback call.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import rate_throttle as rt


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_failover_guard():
    """Every test starts with the re-entrance guard cleared."""
    token = rt._failover_in_progress.set(False)
    yield
    rt._failover_in_progress.reset(token)


@pytest.fixture
def credit_exc():
    return Exception(
        "OpenAI API call failed: Error code: 402 - {'error': {'message': "
        "'This request requires more credits, or fewer max_tokens. "
        "You requested up to 8192 tokens, but can only afford 1506. "
        "To increase, visit openrouter.ai/credits'}}"
    )


@pytest.fixture
def non_credit_exc():
    return Exception("ConnectionTimeout: read timed out after 30s")


# ══════════════════════════════════════════════════════════════════════
# Helper selection
# ══════════════════════════════════════════════════════════════════════

class TestLocalModelSelection:

    def test_selector_returns_ollama_model(self):
        with patch(
            "app.llm_selector.select_model", return_value="ollama/llama3.1:8b",
        ):
            assert (
                rt._select_local_failover_model("anthropic/claude-opus-4.7")
                == "ollama/llama3.1:8b"
            )

    def test_selector_returns_non_ollama_falls_back_to_default(self):
        with patch(
            "app.llm_selector.select_model",
            return_value="anthropic/claude-haiku",  # API-tier, not local
        ):
            out = rt._select_local_failover_model("foo")
        assert out == rt._FAILOVER_DEFAULT_LOCAL_MODEL

    def test_selector_raises_falls_back_to_default(self):
        with patch(
            "app.llm_selector.select_model", side_effect=RuntimeError("boom"),
        ):
            out = rt._select_local_failover_model("foo")
        assert out == rt._FAILOVER_DEFAULT_LOCAL_MODEL


class TestPrepareKwargs:

    def test_model_swapped(self):
        out = rt._prepare_failover_kwargs(
            "anthropic/claude-opus-4.7",
            {"model": "anthropic/claude-opus-4.7", "max_tokens": 8192},
            "ollama/llama3.1:8b",
        )
        assert out["model"] == "ollama/llama3.1:8b"

    def test_max_tokens_capped(self):
        out = rt._prepare_failover_kwargs(
            "anthropic/claude-opus-4.7",
            {"model": "anthropic/claude-opus-4.7", "max_tokens": 8192},
            "ollama/llama3.1:8b",
        )
        assert out["max_tokens"] == rt._FAILOVER_MAX_TOKENS

    def test_max_tokens_below_cap_preserved(self):
        out = rt._prepare_failover_kwargs(
            "anthropic/claude-opus-4.7",
            {"max_tokens": 1024},
            "ollama/llama3.1:8b",
        )
        assert out["max_tokens"] == 1024

    def test_retries_cleared(self):
        out = rt._prepare_failover_kwargs(
            "anthropic/claude-opus-4.7",
            {"num_retries": 3},
            "ollama/llama3.1:8b",
        )
        assert out["num_retries"] == 0

    def test_api_base_and_base_url_stripped(self):
        out = rt._prepare_failover_kwargs(
            "anthropic/claude-opus-4.7",
            {
                "api_base": "https://openrouter.ai/api/v1",
                "base_url": "https://openrouter.ai/api/v1",
            },
            "ollama/llama3.1:8b",
        )
        assert "api_base" not in out
        assert "base_url" not in out


# ══════════════════════════════════════════════════════════════════════
# Sync failover
# ══════════════════════════════════════════════════════════════════════

class TestSyncFailover:

    def test_credit_error_triggers_failover(self, credit_exc):
        """Happy path: 402 → local retry → local response."""
        fake_local_response = MagicMock(_is_local_response=True)
        mock_completion = MagicMock(return_value=fake_local_response)

        with patch(
            "app.rate_throttle._select_local_failover_model",
            return_value="ollama/llama3.1:8b",
        ):
            response = rt._try_credit_failover_sync(
                exc=credit_exc,
                model="anthropic/claude-opus-4.7",
                kwargs={"messages": [], "max_tokens": 8192},
                original_completion=mock_completion,
            )

        assert response is fake_local_response
        # Verify the local call used the local model + capped tokens
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["model"] == "ollama/llama3.1:8b"
        assert call_kwargs["max_tokens"] == rt._FAILOVER_MAX_TOKENS
        assert call_kwargs["num_retries"] == 0

    def test_non_credit_error_does_not_failover(self, non_credit_exc):
        mock_completion = MagicMock()
        response = rt._try_credit_failover_sync(
            exc=non_credit_exc,
            model="anthropic/claude-opus-4.7",
            kwargs={"messages": []},
            original_completion=mock_completion,
        )
        assert response is None
        mock_completion.assert_not_called()

    def test_local_retry_failure_returns_none(self, credit_exc):
        """If local retry also fails, return None so caller propagates
        the ORIGINAL credit error (not the local one)."""
        mock_completion = MagicMock(side_effect=RuntimeError("ollama down"))

        with patch(
            "app.rate_throttle._select_local_failover_model",
            return_value="ollama/llama3.1:8b",
        ):
            response = rt._try_credit_failover_sync(
                exc=credit_exc,
                model="anthropic/claude-opus-4.7",
                kwargs={"messages": []},
                original_completion=mock_completion,
            )
        assert response is None

    def test_reentrance_guard_blocks_second_failover(self, credit_exc):
        """A failover call inside a failover must not trigger another
        failover.  Simulate by pre-setting the guard to True."""
        token = rt._failover_in_progress.set(True)
        try:
            mock_completion = MagicMock()
            response = rt._try_credit_failover_sync(
                exc=credit_exc,
                model="anthropic/claude-opus-4.7",
                kwargs={"messages": []},
                original_completion=mock_completion,
            )
        finally:
            rt._failover_in_progress.reset(token)
        assert response is None
        mock_completion.assert_not_called()

    def test_guard_clears_after_failover(self, credit_exc):
        """After a failover call (success or fail), the guard must be
        back to False so the NEXT top-level call can failover if
        needed."""
        mock_completion = MagicMock(return_value=MagicMock())
        assert rt._failover_in_progress.get() is False
        with patch(
            "app.rate_throttle._select_local_failover_model",
            return_value="ollama/llama3.1:8b",
        ):
            rt._try_credit_failover_sync(
                exc=credit_exc,
                model="anthropic/claude-opus-4.7",
                kwargs={"messages": []},
                original_completion=mock_completion,
            )
        assert rt._failover_in_progress.get() is False

    def test_no_local_model_available_returns_none(self, credit_exc):
        """If select_local_failover_model returns None, don't failover."""
        mock_completion = MagicMock()
        with patch(
            "app.rate_throttle._select_local_failover_model",
            return_value=None,
        ):
            response = rt._try_credit_failover_sync(
                exc=credit_exc,
                model="anthropic/claude-opus-4.7",
                kwargs={"messages": []},
                original_completion=mock_completion,
            )
        assert response is None
        mock_completion.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
# Async failover — same contract as sync
# ══════════════════════════════════════════════════════════════════════

class TestAsyncFailover:

    @pytest.mark.asyncio
    async def test_credit_error_triggers_async_failover(self, credit_exc):
        fake_local_response = MagicMock(_is_local_response=True)

        async def mock_acompletion(**kwargs):
            return fake_local_response

        with patch(
            "app.rate_throttle._select_local_failover_model",
            return_value="ollama/llama3.1:8b",
        ):
            response = await rt._try_credit_failover_async(
                exc=credit_exc,
                model="anthropic/claude-opus-4.7",
                kwargs={"messages": [], "max_tokens": 8192},
                original_acompletion=mock_acompletion,
            )
        assert response is fake_local_response

    @pytest.mark.asyncio
    async def test_non_credit_error_does_not_async_failover(
        self, non_credit_exc,
    ):
        async def mock_acompletion(**kwargs):
            raise RuntimeError("should-not-be-called")

        response = await rt._try_credit_failover_async(
            exc=non_credit_exc,
            model="anthropic/claude-opus-4.7",
            kwargs={"messages": []},
            original_acompletion=mock_acompletion,
        )
        assert response is None

    @pytest.mark.asyncio
    async def test_async_local_retry_failure_returns_none(self, credit_exc):
        async def mock_acompletion(**kwargs):
            raise RuntimeError("ollama down")

        with patch(
            "app.rate_throttle._select_local_failover_model",
            return_value="ollama/llama3.1:8b",
        ):
            response = await rt._try_credit_failover_async(
                exc=credit_exc,
                model="anthropic/claude-opus-4.7",
                kwargs={"messages": []},
                original_acompletion=mock_acompletion,
            )
        assert response is None


# ══════════════════════════════════════════════════════════════════════
# detect_credit_error coverage — the pattern match this relies on
# ══════════════════════════════════════════════════════════════════════

class TestCreditErrorDetection:
    """These aren't new code, but they're load-bearing for the failover
    decision — regression-guard the pattern matching."""

    def test_402_is_detected(self):
        from app.firebase.publish import detect_credit_error
        assert detect_credit_error(
            "Error code: 402 - insufficient credits"
        ) is not None

    def test_openrouter_afford_phrase_is_detected(self):
        from app.firebase.publish import detect_credit_error
        assert detect_credit_error(
            "You requested up to 8192 tokens, but can only afford 1506."
        ) is not None

    def test_insufficient_quota_is_detected(self):
        from app.firebase.publish import detect_credit_error
        assert detect_credit_error(
            "openai.InsufficientQuotaError: insufficient_quota"
        ) is not None

    def test_plain_network_error_not_detected(self):
        from app.firebase.publish import detect_credit_error
        assert detect_credit_error("Connection refused") is None

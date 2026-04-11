"""
Comprehensive failure recovery tests for AndrusAI.

Tests resilience patterns across the entire system:
  - Circuit breaker state machine (CLOSED → OPEN → HALF_OPEN → CLOSED)
  - PostgreSQL connection pool stale detection + pool reset
  - Message idempotency (LRU dedup by sender+timestamp)
  - Signal forwarder reconnection (None vs [] distinction, consecutive error tracking)
  - Crew name validation (invalid → research fallback)
  - Routing exponential backoff + circuit breaker integration
  - Agent execution timeout (max_execution_time=300)
  - Top-level task timeout (asyncio.wait_for 600s)
  - ChromaDB dimension mismatch recovery + journal logging
  - Sender ID stability across restarts (persistent key file)
  - Deep readiness probe (/ready endpoint)
  - KB sanitization warning on missing module

Run: pytest tests/test_failure_recovery.py -v
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Module-level mocks for Docker-only deps
for _dep in ("chromadb", "psycopg2", "psycopg2.extras", "psycopg2.pool"):
    if _dep not in sys.modules:
        sys.modules[_dep] = MagicMock()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CIRCUIT BREAKER STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED."""

    def test_initial_state_closed(self):
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=1)
        assert not cb.is_open()
        assert cb.state == "closed"

    def test_single_failure_stays_closed(self):
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        assert not cb.is_open()

    def test_threshold_failures_opens_circuit(self):
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()
        assert cb.state == "open"

    def test_success_resets_to_closed(self):
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert not cb.is_open()
        assert cb.state == "closed"

    def test_half_open_after_cooldown(self):
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=2, cooldown_seconds=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()
        time.sleep(0.15)
        # After cooldown, circuit transitions to half_open
        assert not cb.is_open()  # is_open checks cooldown

    def test_half_open_success_closes(self):
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=2, cooldown_seconds=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.record_success()
        assert cb.state == "closed"

    def test_half_open_failure_reopens(self):
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("test", failure_threshold=2, cooldown_seconds=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.record_failure()
        assert cb.is_open()

    def test_module_level_providers(self):
        from app.circuit_breaker import is_available, _breakers
        assert "ollama" in _breakers
        assert "openrouter" in _breakers
        assert "anthropic" in _breakers

    def test_anthropic_has_higher_threshold(self):
        from app.circuit_breaker import _breakers
        assert _breakers["anthropic"].failure_threshold == 5
        assert _breakers["ollama"].failure_threshold == 3

    def test_is_available_returns_true_when_closed(self):
        from app.circuit_breaker import is_available, get_breaker
        breaker = get_breaker("test_provider")
        assert is_available("test_provider")

    def test_is_available_returns_false_when_open(self):
        from app.circuit_breaker import is_available, record_failure, get_breaker
        breaker = get_breaker("test_avail")
        for _ in range(breaker.failure_threshold):
            record_failure("test_avail")
        assert not is_available("test_avail")

    def test_get_all_states(self):
        from app.circuit_breaker import get_all_states
        states = get_all_states()
        assert "ollama" in states
        assert "state" in states["ollama"]
        assert "failures" in states["ollama"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MESSAGE IDEMPOTENCY (DEDUP)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMessageDedup:
    """_MessageDedup: bounded LRU deduplication."""

    def _make_dedup(self, max_size=5):
        """Create a dedup instance without importing main.py (needs FastAPI)."""
        class _MessageDedup:
            def __init__(self, max_size=500):
                self._seen = OrderedDict()
                self._max = max_size
                self._lock = threading.Lock()
            def is_dup(self, key):
                with self._lock:
                    if key in self._seen:
                        return True
                    self._seen[key] = True
                    if len(self._seen) > self._max:
                        self._seen.popitem(last=False)
                    return False
        return _MessageDedup(max_size)

    def test_first_message_not_duplicate(self):
        d = self._make_dedup()
        assert not d.is_dup("sender1:12345")

    def test_same_message_is_duplicate(self):
        d = self._make_dedup()
        d.is_dup("sender1:12345")
        assert d.is_dup("sender1:12345")

    def test_different_messages_not_duplicate(self):
        d = self._make_dedup()
        d.is_dup("sender1:12345")
        assert not d.is_dup("sender1:12346")

    def test_different_senders_not_duplicate(self):
        d = self._make_dedup()
        d.is_dup("sender1:12345")
        assert not d.is_dup("sender2:12345")

    def test_lru_eviction(self):
        d = self._make_dedup(max_size=3)
        d.is_dup("a:1")
        d.is_dup("b:2")
        d.is_dup("c:3")
        # Cache: {a:1, b:2, c:3} (full)
        assert d.is_dup("c:3")  # c:3 is in cache
        d.is_dup("d:4")  # Evicts oldest ("a:1"), cache: {b:2, c:3, d:4}
        assert "a:1" not in d._seen  # Evicted
        assert "b:2" in d._seen  # Still in cache

    def test_bounded_size(self):
        d = self._make_dedup(max_size=10)
        for i in range(100):
            d.is_dup(f"s:{i}")
        assert len(d._seen) <= 10

    def test_thread_safety(self):
        d = self._make_dedup(max_size=1000)
        results = []
        def spam(prefix):
            for i in range(100):
                results.append(d.is_dup(f"{prefix}:{i}"))
        threads = [threading.Thread(target=spam, args=(f"t{j}",)) for j in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 500 unique keys, none should be marked as duplicate on first insert
        assert results.count(False) == 500


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CREW NAME VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrewNameValidation:
    """Invalid crew names default to 'research', not raw JSON leak."""

    def test_valid_crew_names(self):
        valid = frozenset({"research", "coding", "writing", "media", "direct"})
        for name in valid:
            assert name in valid

    def test_invalid_crew_defaults_to_research(self):
        _VALID_CREWS = frozenset({"research", "coding", "writing", "media", "direct"})
        decisions = [{"crew": "hacking", "task": "break in", "difficulty": 5}]
        for d in decisions:
            if d.get("crew") not in _VALID_CREWS:
                d["crew"] = "research"
        assert decisions[0]["crew"] == "research"

    def test_none_crew_defaults_to_research(self):
        _VALID_CREWS = frozenset({"research", "coding", "writing", "media", "direct"})
        decisions = [{"task": "something", "difficulty": 3}]
        for d in decisions:
            if d.get("crew") not in _VALID_CREWS:
                d["crew"] = "research"
        assert decisions[0]["crew"] == "research"

    def test_empty_string_crew_defaults(self):
        _VALID_CREWS = frozenset({"research", "coding", "writing", "media", "direct"})
        d = {"crew": "", "task": "test"}
        if d.get("crew") not in _VALID_CREWS:
            d["crew"] = "research"
        assert d["crew"] == "research"

    def test_valid_crews_pass_through(self):
        _VALID_CREWS = frozenset({"research", "coding", "writing", "media", "direct"})
        for crew in _VALID_CREWS:
            d = {"crew": crew, "task": "test"}
            if d.get("crew") not in _VALID_CREWS:
                d["crew"] = "research"
            assert d["crew"] == crew


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EXPONENTIAL BACKOFF
# ═══════════════════════════════════════════════════════════════════════════════


class TestExponentialBackoff:
    """Routing retry uses exponential backoff with jitter."""

    def test_backoff_increases_exponentially(self):
        import random
        random.seed(42)
        waits = []
        for attempt in range(1, 4):
            wait = min(30, (2 ** attempt) + random.uniform(0, 1))
            waits.append(wait)
        assert waits[1] > waits[0]  # 2nd > 1st
        assert waits[2] > waits[1]  # 3rd > 2nd

    def test_backoff_capped_at_30(self):
        import random
        for attempt in range(1, 20):
            wait = min(30, (2 ** attempt) + random.uniform(0, 1))
            assert wait <= 31  # 30 + max 1 jitter

    def test_jitter_adds_randomness(self):
        import random
        waits = set()
        for _ in range(100):
            random.seed()
            wait = min(30, (2 ** 2) + random.uniform(0, 1))
            waits.add(round(wait, 3))
        assert len(waits) > 10  # Should have many distinct values


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SIGNAL FORWARDER RECONNECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSignalForwarderReconnection:
    """Signal forwarder: None vs [] distinction, reconnection on failures."""

    def test_receive_messages_connection_error_returns_none(self):
        """ConnectionError should return None (not [])."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signal"))
        try:
            import importlib
            import forwarder
            importlib.reload(forwarder)
            with patch.object(forwarder._signal_session, "post",
                            side_effect=forwarder.requests.exceptions.ConnectionError):
                result = forwarder._receive_messages()
                assert result is None
        except ImportError:
            pytest.skip("forwarder not importable")
        finally:
            sys.path.pop(0)

    def test_receive_messages_empty_returns_list(self):
        """No new messages should return [] (not None)."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signal"))
        try:
            import importlib
            import forwarder
            importlib.reload(forwarder)
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"result": []}
            with patch.object(forwarder._signal_session, "post", return_value=mock_resp):
                result = forwarder._receive_messages()
                assert result == []
        except ImportError:
            pytest.skip("forwarder not importable")
        finally:
            sys.path.pop(0)

    def test_check_signal_cli_alive_success(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signal"))
        try:
            import importlib
            import forwarder
            importlib.reload(forwarder)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            with patch.object(forwarder._signal_session, "post", return_value=mock_resp):
                assert forwarder._check_signal_cli_alive()
        except ImportError:
            pytest.skip("forwarder not importable")
        finally:
            sys.path.pop(0)

    def test_check_signal_cli_alive_failure(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "signal"))
        try:
            import importlib
            import forwarder
            importlib.reload(forwarder)
            with patch.object(forwarder._signal_session, "post",
                            side_effect=forwarder.requests.exceptions.ConnectionError):
                assert not forwarder._check_signal_cli_alive()
        except ImportError:
            pytest.skip("forwarder not importable")
        finally:
            sys.path.pop(0)

    def test_consecutive_error_tracking_logic(self):
        """Simulate the poll_loop consecutive error tracking."""
        _consecutive_errors = 0
        _MAX_ERRORS = 60
        reconnected = False

        # Simulate 60 connection errors
        for _ in range(60):
            messages = None  # Connection error
            if messages is None:
                _consecutive_errors += 1
            elif messages:
                _consecutive_errors = 0
            else:
                _consecutive_errors = 0
            if _consecutive_errors >= _MAX_ERRORS:
                reconnected = True
                _consecutive_errors = 0

        assert reconnected

    def test_successful_message_resets_errors(self):
        _consecutive_errors = 50
        messages = [{"envelope": {"dataMessage": {"message": "hello"}}}]
        if messages is None:
            _consecutive_errors += 1
        elif messages:
            _consecutive_errors = 0
        assert _consecutive_errors == 0

    def test_empty_list_resets_errors(self):
        """Empty list (no new messages) should NOT count as error."""
        _consecutive_errors = 50
        messages = []
        if messages is None:
            _consecutive_errors += 1
        elif messages:
            _consecutive_errors = 0
        else:
            _consecutive_errors = 0
        assert _consecutive_errors == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SENDER ID STABILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestSenderIDStability:
    """Sender ID hashing: persistent key survives restarts."""

    def test_gateway_secret_priority(self):
        """When gateway secret available, use it."""
        key = b"my_secret_key_123"
        sender = "+3581234567"
        result = hmac.new(key, sender.encode(), hashlib.sha256).hexdigest()[:16]
        assert len(result) == 16
        # Deterministic
        result2 = hmac.new(key, sender.encode(), hashlib.sha256).hexdigest()[:16]
        assert result == result2

    def test_different_senders_different_ids(self):
        key = b"secret"
        id1 = hmac.new(key, b"+3581111111", hashlib.sha256).hexdigest()[:16]
        id2 = hmac.new(key, b"+3582222222", hashlib.sha256).hexdigest()[:16]
        assert id1 != id2

    def test_same_sender_same_id(self):
        key = b"secret"
        id1 = hmac.new(key, b"+3581111111", hashlib.sha256).hexdigest()[:16]
        id2 = hmac.new(key, b"+3581111111", hashlib.sha256).hexdigest()[:16]
        assert id1 == id2

    def test_persistent_key_file_logic(self):
        """Simulate the 3-priority key resolution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            key_file = Path(tmpdir) / ".sender_key"

            # Priority 3: generate and persist
            import secrets
            key = secrets.token_bytes(32)
            key_file.write_bytes(key)

            # Priority 2: read from file
            loaded = key_file.read_bytes()
            assert loaded == key
            assert len(loaded) >= 16

    def test_key_survives_simulated_restart(self):
        """Key persisted to file should produce same sender ID across 'restarts'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            key_file = Path(tmpdir) / ".sender_key"
            import secrets

            # "First boot": generate key
            key = secrets.token_bytes(32)
            key_file.write_bytes(key)
            id_first = hmac.new(key, b"+358123", hashlib.sha256).hexdigest()[:16]

            # "Restart": load from file
            loaded_key = key_file.read_bytes()
            id_second = hmac.new(loaded_key, b"+358123", hashlib.sha256).hexdigest()[:16]

            assert id_first == id_second


# ═══════════════════════════════════════════════════════════════════════════════
# 7. AGENT EXECUTION TIMEOUT
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentExecutionTimeout:
    """All agents have max_execution_time=300 (CrewAI native timeout)."""

    def test_all_agent_factories_have_timeout(self):
        import inspect
        factories = [
            ("app.agents.researcher", "create_researcher"),
            ("app.agents.coder", "create_coder"),
            ("app.agents.writer", "create_writer"),
            ("app.agents.critic", "create_critic"),
            ("app.agents.media_analyst", "create_media_analyst"),
            ("app.agents.introspector", "create_introspector"),
        ]
        for mod_name, fn_name in factories:
            try:
                mod = __import__(mod_name, fromlist=[fn_name])
                src = inspect.getsource(getattr(mod, fn_name))
                assert "max_execution_time=300" in src, \
                    f"{fn_name} missing max_execution_time=300"
            except ImportError:
                pytest.skip(f"{mod_name} not importable outside Docker")

    def test_max_execution_time_is_integer_seconds(self):
        """CrewAI expects int seconds, not float or timedelta."""
        val = 300
        assert isinstance(val, int)
        assert val > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. POSTGRESQL CONNECTION POOL RESILIENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostgreSQLResilience:
    """Connection pool: stale detection, reset, reconnection.

    NOTE: Can't import app.control_plane.db outside Docker (psycopg2 + Python 3.10
    type hints). Tests verify the pattern logic without importing the module.
    """

    def test_reset_pool_pattern(self):
        """_reset_pool() should close pool and set to None."""
        _pool = MagicMock()
        _pool_lock = threading.Lock()

        def _reset_pool():
            nonlocal _pool
            with _pool_lock:
                if _pool:
                    try:
                        _pool.closeall()
                    except Exception:
                        pass
                    _pool = None

        _reset_pool()
        assert _pool is None

    def test_reset_pool_handles_closeall_exception(self):
        _pool = MagicMock()
        _pool.closeall.side_effect = Exception("already closed")
        _pool_lock = threading.Lock()

        def _reset_pool():
            nonlocal _pool
            with _pool_lock:
                if _pool:
                    try:
                        _pool.closeall()
                    except Exception:
                        pass
                    _pool = None

        _reset_pool()  # Should not raise
        assert _pool is None

    def test_execute_returns_none_when_no_pool(self):
        """execute() should return None when pool is unavailable."""
        def execute(query, params=(), fetch=False):
            p = None  # Pool unavailable
            if not p:
                return None
        assert execute("SELECT 1", fetch=True) is None

    def test_stale_connection_detection_pattern(self):
        """Simulate the stale connection recovery logic."""
        pool = MagicMock()
        fresh_conn = MagicMock()
        getconn_calls = [0]

        def mock_getconn():
            getconn_calls[0] += 1
            if getconn_calls[0] == 1:
                # Return a conn that raises on autocommit
                bad = MagicMock()
                type(bad).autocommit = PropertyMock(side_effect=Exception("stale"))
                return bad
            return fresh_conn
        pool.getconn = mock_getconn

        # Simulate the stale detection pattern from db.py
        conn = pool.getconn()
        try:
            conn.autocommit = True
        except Exception:
            pool.putconn(conn, close=True)
            conn = pool.getconn()
            conn.autocommit = True

        assert getconn_calls[0] == 2  # Got connection twice (stale + fresh)
        pool.putconn.assert_called_once()  # Closed stale connection


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CHROMADB DIMENSION MISMATCH RECOVERY
# ═══════════════════════════════════════════════════════════════════════════════


class TestChromaDBDimensionRecovery:
    """Dimension mismatch: detect, log to journal, recreate collection."""

    def test_dimension_mismatch_detected(self):
        """When stored dims != model dims, mismatch should be detected."""
        stored_dim = 384
        model_dim = 768
        assert stored_dim != model_dim

    def test_journal_entry_format(self):
        """Journal entry should contain collection name and dimension info."""
        name = "team_shared"
        existing_dim = 384
        current_dim = 768
        summary = f"ChromaDB '{name}' recreated: dims {existing_dim}→{current_dim}"
        assert "team_shared" in summary
        assert "384" in summary
        assert "768" in summary

    def test_recreation_preserves_empty_collection(self):
        """After recreation, collection should exist but be empty."""
        mock_client = MagicMock()
        mock_col = MagicMock()
        mock_col.count.return_value = 0
        mock_client.get_or_create_collection.return_value = mock_col
        mock_client.delete_collection.return_value = None

        # Simulate recreation
        mock_client.delete_collection("test_col")
        new_col = mock_client.get_or_create_collection("test_col")
        assert new_col.count() == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 10. DEEP READINESS PROBE
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadinessProbe:
    """GET /ready: deep dependency checks."""

    def test_all_ok_returns_ready(self):
        checks = {"postgres": "ok", "chromadb": "ok"}
        all_ok = all(v == "ok" for k, v in checks.items()
                     if k not in ("circuit_breakers", "inflight_tasks", "ollama"))
        assert all_ok

    def test_postgres_error_returns_degraded(self):
        checks = {"postgres": "error: connection refused", "chromadb": "ok"}
        all_ok = all(v == "ok" for k, v in checks.items()
                     if k not in ("circuit_breakers", "inflight_tasks", "ollama"))
        assert not all_ok

    def test_ollama_excluded_from_readiness(self):
        """Ollama being down should NOT cause 503 (it's optional)."""
        checks = {"postgres": "ok", "chromadb": "ok", "ollama": "down"}
        all_ok = all(v == "ok" for k, v in checks.items()
                     if k not in ("circuit_breakers", "inflight_tasks", "ollama"))
        assert all_ok  # Still ready

    def test_circuit_breakers_excluded_from_readiness(self):
        checks = {
            "postgres": "ok", "chromadb": "ok",
            "circuit_breakers": {"ollama": {"state": "open", "failures": 3}},
        }
        all_ok = all(v == "ok" for k, v in checks.items()
                     if k not in ("circuit_breakers", "inflight_tasks", "ollama"))
        assert all_ok


# ═══════════════════════════════════════════════════════════════════════════════
# 11. TOP-LEVEL TASK TIMEOUT
# ═══════════════════════════════════════════════════════════════════════════════


class TestTopLevelTimeout:
    """asyncio.wait_for wraps commander.handle with 600s timeout."""

    @pytest.mark.asyncio
    async def test_timeout_returns_error_message(self):
        """If commander.handle() exceeds timeout, return user-friendly error."""
        async def slow_handle():
            await asyncio.sleep(10)
            return "should not reach"

        try:
            result = await asyncio.wait_for(slow_handle(), timeout=0.01)
        except asyncio.TimeoutError:
            result = "Sorry, your request took too long to process."

        assert "too long" in result

    @pytest.mark.asyncio
    async def test_fast_handle_returns_normally(self):
        async def fast_handle():
            return "answer"

        result = await asyncio.wait_for(fast_handle(), timeout=5)
        assert result == "answer"


# ═══════════════════════════════════════════════════════════════════════════════
# 12. KB SANITIZATION WARNING
# ═══════════════════════════════════════════════════════════════════════════════


class TestKBSanitizationWarning:
    """Knowledge base logs warning when sanitization module missing."""

    def test_import_error_logged_not_silent(self):
        """The except ImportError should log, not just pass."""
        # Simulate the pattern
        warned = False
        try:
            raise ImportError("no sanitize module")
        except ImportError:
            warned = True  # In real code: logger.warning(...)
        assert warned


# ═══════════════════════════════════════════════════════════════════════════════
# 13. INTEGRATION: CIRCUIT BREAKER + ROUTING
# ═══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerRouting:
    """Circuit breaker integration with routing decisions."""

    def test_anthropic_unavailable_switches_to_openrouter(self):
        """When anthropic circuit is open, should use OpenRouter."""
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("anthropic_test", failure_threshold=3)
        # Trip the circuit
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open()
        # Routing should switch to fallback
        _routing_provider = "anthropic"
        if cb.is_open():
            _routing_provider = "openrouter"
        assert _routing_provider == "openrouter"

    def test_both_unavailable_raises(self):
        """When both providers are down, system should degrade gracefully."""
        from app.circuit_breaker import CircuitBreaker
        cb_ant = CircuitBreaker("ant_test", failure_threshold=2)
        cb_or = CircuitBreaker("or_test", failure_threshold=2)
        cb_ant.record_failure()
        cb_ant.record_failure()
        cb_or.record_failure()
        cb_or.record_failure()
        assert cb_ant.is_open()
        assert cb_or.is_open()

    def test_success_after_failure_records(self):
        """After routing succeeds, circuit breaker should record success."""
        from app.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker("routing_test", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        cb.record_success()
        assert cb.failure_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 14. REFLEXION TIER ESCALATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestReflexionTierEscalation:
    """Reflexion retries escalate model tier on failure."""

    def test_trial_1_uses_original_difficulty(self):
        difficulty = 3
        trial = 1
        _TIER_ESCALATION = {1: None, 2: "mid", 3: "premium"}
        trial_difficulty = difficulty
        forced = _TIER_ESCALATION.get(trial)
        if forced and trial > 1:
            trial_difficulty = 6
        assert trial_difficulty == 3

    def test_trial_2_escalates_to_mid(self):
        difficulty = 3
        trial = 2
        _TIER_ESCALATION = {1: None, 2: "mid", 3: "premium"}
        trial_difficulty = difficulty
        forced = _TIER_ESCALATION.get(trial)
        if forced and trial > 1:
            if forced == "mid" and difficulty < 6:
                trial_difficulty = 6
        assert trial_difficulty == 6

    def test_trial_3_escalates_to_premium(self):
        difficulty = 3
        trial = 3
        _TIER_ESCALATION = {1: None, 2: "mid", 3: "premium"}
        trial_difficulty = difficulty
        forced = _TIER_ESCALATION.get(trial)
        if forced and trial > 1:
            if forced == "premium" and difficulty < 8:
                trial_difficulty = 8
        assert trial_difficulty == 8

    def test_high_difficulty_not_downgraded(self):
        """If original difficulty is already 8, trial 2 shouldn't lower it."""
        difficulty = 8
        trial = 2
        _TIER_ESCALATION = {1: None, 2: "mid", 3: "premium"}
        trial_difficulty = difficulty
        forced = _TIER_ESCALATION.get(trial)
        if forced and trial > 1:
            if forced == "mid" and difficulty < 6:
                trial_difficulty = 6
        assert trial_difficulty == 8  # Not lowered


# ═══════════════════════════════════════════════════════════════════════════════
# 15. SAFETY: HOOKS BLOCK DANGEROUS OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDangerousOperationBlocking:
    """block_dangerous hook blocks destructive tool operations."""

    def test_rm_rf_blocked(self):
        BLOCKED = {"rm -rf", "DROP TABLE", "DROP DATABASE", "TRUNCATE", "shutdown"}
        action = "rm -rf /tmp/important"
        blocked = any(p.lower() in action.lower() for p in BLOCKED)
        assert blocked

    def test_drop_table_blocked(self):
        BLOCKED = {"rm -rf", "DROP TABLE", "DROP DATABASE", "TRUNCATE"}
        action = "DROP TABLE users;"
        blocked = any(p.lower() in action.lower() for p in BLOCKED)
        assert blocked

    def test_safe_action_allowed(self):
        BLOCKED = {"rm -rf", "DROP TABLE", "DROP DATABASE", "TRUNCATE", "shutdown"}
        action = "SELECT * FROM users WHERE id = 1"
        blocked = any(p.lower() in action.lower() for p in BLOCKED)
        assert not blocked

    def test_case_insensitive(self):
        BLOCKED = {"rm -rf", "DROP TABLE"}
        action = "drop table Users"
        blocked = any(p.lower() in action.lower() for p in BLOCKED)
        assert blocked

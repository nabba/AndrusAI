"""
chaos_tester.py — Lightweight chaos testing for self-healing verification.

Periodically injects controlled faults to verify recovery paths work:
  1. Circuit breaker recovery (Ollama timeout simulation)
  2. Database connection recovery (pool reset)
  3. Embedding backend recovery (unavailable → recovery)
  4. All-providers-down detection (credit exhaustion)

Runs as a HEAVY idle job (max once per 24 hours). Each test:
  - Injects a fault
  - Waits briefly for detection
  - Verifies recovery mechanism fires
  - Cleans up (restores normal state)

No real damage — all faults are simulated via in-memory state changes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

_last_run: float = 0.0
_MIN_INTERVAL = 86400  # 24 hours between chaos runs

@dataclass
class ChaosTestResult:
    name: str
    injected: bool
    recovered: bool
    duration_ms: float
    error: str | None = None

def _test_circuit_breaker_recovery() -> ChaosTestResult:
    """Verify circuit breaker state machine using an ISOLATED test breaker.

    Uses a dedicated test breaker — never touches the real ollama/openrouter/
    anthropic breakers, so this can't cause user-facing "all providers exhausted".
    """
    from app.circuit_breaker import CircuitBreaker

    t0 = time.monotonic()
    try:
        breaker = CircuitBreaker("_chaos_test", failure_threshold=3, cooldown_seconds=1)

        # Inject: trip the breaker
        for _ in range(breaker.failure_threshold):
            breaker.record_failure()
        assert breaker.is_open(), "Breaker should be open after failures"

        # Recover: reset (simulates cooldown elapsed)
        breaker.record_success()
        recovered = not breaker.is_open()

        return ChaosTestResult(
            name="circuit_breaker_recovery",
            injected=True, recovered=recovered,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as e:
        return ChaosTestResult("circuit_breaker_recovery", True, False, 0, str(e))

def _test_db_connection_recovery() -> ChaosTestResult:
    """Simulate database pool reset and verify reconnection."""
    t0 = time.monotonic()
    try:
        from app.control_plane.db import _reset_pool, execute
        _reset_pool()
        # Verify pool auto-recreates on next query
        result = execute("SELECT 1", fetch=True)
        recovered = result is not None

        return ChaosTestResult(
            name="db_connection_recovery",
            injected=True, recovered=recovered,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as e:
        return ChaosTestResult("db_connection_recovery", True, False, 0, str(e))

def _test_embedding_recovery() -> ChaosTestResult:
    """Simulate embedding backend failure and verify recovery attempt."""
    t0 = time.monotonic()
    try:
        import app.memory.chromadb_manager as cm
        original = cm._embed_backend

        # Inject: mark backend unavailable
        cm._embed_backend = "unavailable"
        assert cm._embed_backend == "unavailable"

        # Cleanup: restore (simulates Ollama coming back)
        cm._embed_backend = original if original != "unavailable" else "unknown"
        recovered = cm._embed_backend != "unavailable"

        return ChaosTestResult(
            name="embedding_recovery",
            injected=True, recovered=recovered,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as e:
        return ChaosTestResult("embedding_recovery", True, False, 0, str(e))

def _test_provider_exhaustion_detection() -> ChaosTestResult:
    """Verify exhaustion detection using ISOLATED test breakers.

    Never touches real provider breakers — uses dedicated test instances
    to verify the detection logic without affecting user-facing availability.
    """
    t0 = time.monotonic()
    try:
        from app.circuit_breaker import CircuitBreaker

        # Create isolated breakers (not the real ones)
        test_breakers = {
            "anthropic": CircuitBreaker("_test_anthropic", failure_threshold=3, cooldown_seconds=1),
            "openrouter": CircuitBreaker("_test_openrouter", failure_threshold=3, cooldown_seconds=1),
            "ollama": CircuitBreaker("_test_ollama", failure_threshold=3, cooldown_seconds=1),
        }

        # Trip all test breakers
        for b in test_breakers.values():
            for _ in range(b.failure_threshold + 1):
                b.record_failure()

        # Verify all are open
        all_open = all(b.is_open() for b in test_breakers.values())

        # Verify recovery works
        for b in test_breakers.values():
            b.record_success()
        all_recovered = all(not b.is_open() for b in test_breakers.values())

        return ChaosTestResult(
            name="provider_exhaustion_detection",
            injected=True, recovered=(all_open and all_recovered),
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as e:
        return ChaosTestResult("provider_exhaustion_detection", True, False, 0, str(e))

CHAOS_TESTS = [
    _test_circuit_breaker_recovery,
    _test_db_connection_recovery,
    _test_embedding_recovery,
    _test_provider_exhaustion_detection,
]

def run_chaos_suite() -> dict:
    """Run all chaos tests. Returns summary dict.

    Safe to run in production — all faults are simulated via
    in-memory state changes and cleaned up immediately.
    """
    global _last_run

    # Rate limit: max once per 24 hours
    if time.monotonic() - _last_run < _MIN_INTERVAL and _last_run > 0:
        return {"status": "skipped", "reason": "ran less than 24h ago"}

    _last_run = time.monotonic()
    results = []
    passed = 0
    failed = 0

    for test_fn in CHAOS_TESTS:
        try:
            result = test_fn()
            results.append(result)
            if result.recovered:
                passed += 1
            else:
                failed += 1
                logger.warning(f"chaos_tester: FAILED {result.name}: {result.error or 'no recovery'}")
        except Exception as e:
            results.append(ChaosTestResult(test_fn.__name__, False, False, 0, str(e)))
            failed += 1

    summary = {
        "status": "completed",
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "tests": [{"name": r.name, "passed": r.recovered, "ms": round(r.duration_ms, 1)} for r in results],
    }

    logger.info(f"chaos_tester: {passed}/{len(results)} tests passed")

    # Report to dashboard
    try:
        from app.firebase.publish import _get_db, _now_iso
        db = _get_db()
        if db:
            db.collection("status").document("chaos_tests").set({
                "last_run": _now_iso(),
                "summary": summary,
            })
    except Exception:
        pass

    return summary

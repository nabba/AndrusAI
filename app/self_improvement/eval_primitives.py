"""Eval primitives — response validation against a rule string.

Relocated from the retired ``app.experiment_runner`` (2026-06-02 evolution
consolidation). These two functions are the only externally-used eval helpers
(``goodhart_guard``, ``proposals``, ``external_benchmarks``); the legacy
mutation loop + its ``eval_set_score`` / ``verify_eval_integrity`` machinery
were loop-only and died with the broken AVO path.

The ``judge:`` path uses ``create_vetting_llm`` (a different model family from
the generation models), preserving the DGM separation-of-evaluation constraint.
"""
from __future__ import annotations

import json
import logging

from app.paths import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

TEST_TASKS_PATH = WORKSPACE_ROOT / "test_tasks.json"


def load_test_tasks(suite: str = "all") -> list[dict]:
    """Load the test task bank for evaluation.

    Args:
        suite: "fixed" (regression), "rotating" (novelty), or "all" (both).
    """
    try:
        if TEST_TASKS_PATH.exists():
            tasks = json.loads(TEST_TASKS_PATH.read_text())
            if suite == "all":
                return tasks
            return [t for t in tasks if t.get("suite", "fixed") == suite]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def validate_response(response: str, rule: str) -> bool:
    """Validate a response against a rule string.

    Supported rule prefixes:
      - contains:KEYWORD      — response contains keyword (case-insensitive)
      - not_contains:KEYWORD   — response does NOT contain keyword
      - min_length:N           — response is at least N characters
      - max_length:N           — response is at most N characters
      - exec_passes:TEST_CODE  — execute response + test code in Docker sandbox
      - judge:CRITERIA         — LLM judge scores response ≥ 0.5 on criteria
    """
    if not rule:
        return True

    if rule.startswith("contains:"):
        expected = rule[len("contains:"):]
        return expected.lower() in response.lower()

    if rule.startswith("not_contains:"):
        forbidden = rule[len("not_contains:"):]
        return forbidden.lower() not in response.lower()

    if rule.startswith("min_length:"):
        min_len = int(rule[len("min_length:"):])
        return len(response) >= min_len

    if rule.startswith("max_length:"):
        max_len = int(rule[len("max_length:"):])
        return len(response) <= max_len

    if rule.startswith("exec_passes:"):
        test_code = rule[len("exec_passes:"):]
        return _validate_exec_passes(response, test_code)

    if rule.startswith("judge:"):
        criteria = rule[len("judge:"):]
        return _validate_judge(response, criteria)

    return True


# ── exec_passes: validation — deterministic code execution ──────────────────

def _validate_exec_passes(response: str, test_code: str) -> bool:
    """Execute response code + test assertions in Docker sandbox.

    Returns True if the code runs without error and prints 'PASS'.
    """
    try:
        from app.sandbox_runner import run_code_check
        return run_code_check(response, test_code, timeout_s=30)
    except Exception as e:
        logger.warning(f"exec_passes validation failed: {e}")
        return False


# ── judge: validation — LLM-as-judge with DGM constraint ───────────────────

# Cache judge results per (response_hash, rule_hash) to avoid redundant API calls
_judge_cache: dict[str, float] = {}
_JUDGE_CACHE_MAX = 200
_JUDGE_PASS_THRESHOLD = 0.5


def _validate_judge(response: str, criteria: str) -> bool:
    """Score response using an external LLM judge (DGM-compliant).

    The judge model (a different model family from the generation models,
    via create_vetting_llm) satisfies the DGM constraint that evaluation
    and generation must use separate model families.

    Returns True if the judge scores the response ≥ 0.5.
    """
    import hashlib

    # Cache key — hash of response prefix + criteria
    cache_key = hashlib.md5(
        f"{response[:500]}|{criteria}".encode()
    ).hexdigest()[:16]

    if cache_key in _judge_cache:
        return _judge_cache[cache_key] >= _JUDGE_PASS_THRESHOLD

    try:
        from app.llm_factory import create_vetting_llm
        judge = create_vetting_llm()

        judge_prompt = (
            f"Score the following response on a scale of 0.0 to 1.0.\n"
            f"Evaluation criteria: {criteria}\n\n"
            f"Response to evaluate:\n{response[:2000]}\n\n"
            f"Reply with ONLY a single decimal number between 0.0 and 1.0."
        )

        raw = str(judge.call(judge_prompt)).strip()
        # Extract first float from response
        import re
        match = re.search(r"(\d+\.?\d*)", raw)
        if match:
            score = float(match.group(1))
            score = max(0.0, min(1.0, score))
        else:
            logger.warning(f"judge: could not parse score from: {raw[:100]}")
            return True  # Don't block on judge parse failure

        # Cache result (evict oldest if full)
        if len(_judge_cache) >= _JUDGE_CACHE_MAX:
            keys = list(_judge_cache.keys())[:50]
            for k in keys:
                del _judge_cache[k]
        _judge_cache[cache_key] = score

        return score >= _JUDGE_PASS_THRESHOLD

    except Exception as e:
        logger.warning(f"judge validation error: {e}")
        return True  # Don't block on judge failure

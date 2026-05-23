"""Scoring functions for benchmark tasks (Phase C.3, 2026-05-22).

A scorer is ``Callable[[str, expected, **kwargs], float]`` returning a
score in [0.0, 1.0]. Each scorer is a small pure function. New scorers
register themselves by appearing in :data:`SCORER_REGISTRY`.

Design constraints
──────────────────

  * **Pure** — no I/O, no LLM calls, no time-dependent behavior. The
    same input must produce the same output forever. That's the contract
    the catalog relies on when it says "task X scored Y".
  * **Total** — no exceptions for "I don't know how to score this".
    Bad input still returns a float (0.0). Exceptional cases are
    captured by ``BenchmarkRun.error`` upstream, not by raising here.
  * **Cheap** — runs on every benchmark execution. Each scorer should
    finish in well under a millisecond on typical inputs.

The registry is the public surface. The catalog validates that every
``BenchmarkTask.scorer`` exists in this registry at load time, so a
typo can never reach the runner.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable


def exact_match(
    output: str,
    expected: str,
    *,
    case_sensitive: bool = True,
    strip: bool = True,
) -> float:
    """1.0 iff ``output`` equals ``expected`` after optional stripping
    + case-folding.

    The strict-equality scorer. Use this for tasks with a single
    canonical answer ("What is 2+2?" → "4"). For multi-line outputs
    where leading/trailing whitespace shouldn't penalise, ``strip=True``
    (default) is what you want.
    """
    if not isinstance(output, str) or not isinstance(expected, str):
        return 0.0
    a, b = output, expected
    if strip:
        a, b = a.strip(), b.strip()
    if not case_sensitive:
        a, b = a.casefold(), b.casefold()
    return 1.0 if a == b else 0.0


def contains(
    output: str,
    expected: list[str],
    *,
    case_sensitive: bool = False,
    all_required: bool = True,
) -> float:
    """Substring scorer — checks how many of ``expected`` appear in
    ``output``.

    ``all_required=True`` (default): score is the fraction matched
    (3 of 4 → 0.75). Use this when the operator wants partial credit
    for partial coverage.

    ``all_required=False``: 1.0 if ANY expected substring appears, else
    0.0. Use this for "either of these answers is acceptable".
    """
    if not isinstance(output, str) or not isinstance(expected, list):
        return 0.0
    if not expected:
        return 0.0
    target = output if case_sensitive else output.casefold()
    hits = 0
    for item in expected:
        if not isinstance(item, str):
            continue
        probe = item if case_sensitive else item.casefold()
        if probe in target:
            hits += 1
    if all_required:
        return hits / len(expected)
    return 1.0 if hits >= 1 else 0.0


def regex_match(
    output: str,
    expected: str,
    *,
    ignore_case: bool = False,
    multiline: bool = False,
) -> float:
    """1.0 iff ``re.search(expected, output)`` finds a match.

    Use for "any timestamp" / "valid email" / "starts with digit" type
    checks. A malformed regex returns 0.0 (failure-isolated — we never
    raise out to the runner).
    """
    if not isinstance(output, str) or not isinstance(expected, str):
        return 0.0
    flags = 0
    if ignore_case:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE
    try:
        return 1.0 if re.search(expected, output, flags) else 0.0
    except re.error:
        return 0.0


def json_keys_present(
    output: str,
    expected: list[str],
    *,
    require_all: bool = True,
) -> float:
    """Parses ``output`` as JSON; scores by fraction of ``expected`` keys
    present at the top level.

    The most common LLM-task category: "produce JSON with these fields".
    A non-parseable output scores 0.0. A parseable output with N of M
    keys returns N/M (or 1.0/0.0 for ``require_all=False``).

    Strips code-fence wrappers (```json … ```) before parsing because
    that's what real LLM output looks like in practice.
    """
    if not isinstance(output, str) or not isinstance(expected, list):
        return 0.0
    if not expected:
        return 0.0
    cleaned = output.strip()
    # Strip leading/trailing fences if present
    if cleaned.startswith("```"):
        # Skip first line + drop trailing fence
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            if lines[-1].strip().startswith("```"):
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            cleaned = "\n".join(lines)
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return 0.0
    if not isinstance(parsed, dict):
        return 0.0
    hits = sum(1 for key in expected if isinstance(key, str) and key in parsed)
    if require_all:
        return hits / len(expected)
    return 1.0 if hits >= 1 else 0.0


def length_within(
    output: str,
    expected: dict[str, int],
    **_unused: Any,
) -> float:
    """1.0 iff ``min_chars <= len(output.strip()) <= max_chars``.

    ``expected`` is a dict ``{"min": int, "max": int}``. Use for
    summarisation tasks ("summarise in 100-200 chars") and refusal
    detection ("don't output more than 50 chars when refusing").

    Either bound is optional — omit "min" for upper-only, omit "max"
    for lower-only.
    """
    if not isinstance(output, str) or not isinstance(expected, dict):
        return 0.0
    n = len(output.strip())
    lo = expected.get("min", 0)
    hi = expected.get("max", 10**9)
    try:
        lo_i, hi_i = int(lo), int(hi)
    except (TypeError, ValueError):
        return 0.0
    return 1.0 if (lo_i <= n <= hi_i) else 0.0


# ── Registry ────────────────────────────────────────────────────────


# Scorer name → callable. The catalog validates ``BenchmarkTask.scorer``
# against this map at load time so a YAML typo never reaches the runner.
SCORER_REGISTRY: dict[str, Callable[..., float]] = {
    "exact_match": exact_match,
    "contains": contains,
    "regex_match": regex_match,
    "json_keys_present": json_keys_present,
    "length_within": length_within,
}


def score(
    scorer_name: str,
    output: str,
    expected: Any,
    *,
    scorer_args: dict[str, Any] | None = None,
) -> float:
    """Dispatch to the named scorer. Unknown scorer → 0.0.

    The catalog should have rejected unknown scorers at load time;
    this guard exists for runtime-dynamic callers (tests, ad-hoc
    invocations).
    """
    fn = SCORER_REGISTRY.get(scorer_name)
    if fn is None:
        return 0.0
    kwargs = scorer_args or {}
    try:
        result = fn(output, expected, **kwargs)
    except Exception:
        # Total contract — scorer must not propagate exceptions.
        return 0.0
    # Clamp to [0.0, 1.0] in case a custom scorer misbehaves.
    if not isinstance(result, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(result)))


__all__ = [
    "SCORER_REGISTRY",
    "contains",
    "exact_match",
    "json_keys_present",
    "length_within",
    "regex_match",
    "score",
]

"""
retrieval/decomposer.py — LLM-powered query decomposition.

Complex queries like "What are the ethical implications of autonomous AI
drawing on Stoic principles?" benefit from being split into sub-queries:
  1. "Stoic principles on autonomy and duty"
  2. "Ethics of autonomous AI systems"
  3. "Stoic ethics applied to technology"

Each sub-query is sent to the vector store independently, and results are
merged and deduplicated before re-ranking.

Uses the cheap vetting LLM to keep cost negligible (~$0.001 per decomposition).
Falls back to [query] on any error — never blocks retrieval.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import functools
import json
import logging
import re

from app.retrieval import config as cfg

logger = logging.getLogger(__name__)

_DECOMPOSITION_PROMPT = """You are a search query decomposer. Given a complex query, break it into 2-{max_sub} independent sub-queries that together cover the full information need.

Rules:
- Each sub-query should target a different aspect of the original query.
- Sub-queries should be self-contained (understandable without the original).
- If the query is already simple and focused, return it as-is in a single-element array.
- Return ONLY a JSON array of strings, nothing else.

Query: {query}

JSON array:"""


@functools.lru_cache(maxsize=128)
def _decompose_cached(query: str, max_sub: int) -> tuple[str, ...]:
    """Cached decomposition (returns tuple for hashability)."""
    try:
        from app.llm.factory import create_cheap_vetting_llm

        llm = create_cheap_vetting_llm()
        prompt = _DECOMPOSITION_PROMPT.format(query=query, max_sub=max_sub)
        response = llm.invoke(prompt)

        # Extract text from response (LangChain AIMessage or plain str).
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()

        # Robustly extract JSON array even if wrapped in markdown fences.
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
                # Cap at max_sub and remove empty strings.
                result = [s.strip() for s in parsed if s.strip()][:max_sub]
                if result:
                    return tuple(result)

        logger.debug("retrieval.decomposer: could not parse LLM response: %s", text[:200])
    except Exception as exc:
        logger.debug("retrieval.decomposer: decomposition failed: %s", exc)

    return (query,)


def decompose_query(
    query: str,
    max_subqueries: int = cfg.DECOMPOSITION_MAX_SUBQUERIES,
    min_length: int = cfg.DECOMPOSITION_MIN_LENGTH,
) -> list[str]:
    """Decompose a complex query into independent sub-queries.

    Parameters
    ----------
    query : str
        The original query text.
    max_subqueries : int
        Maximum number of sub-queries to produce.
    min_length : int
        Queries shorter than this are returned as-is (no LLM call).

    Returns
    -------
    list[str]
        One or more sub-queries. Always contains at least ``[query]``.
    """
    if not query or len(query) < min_length:
        return [query]

    if not cfg.DECOMPOSITION_ENABLED:
        return [query]

    return list(_decompose_cached(query.strip(), max_subqueries))

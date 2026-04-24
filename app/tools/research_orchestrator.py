"""
research_orchestrator — Structured, partial-streaming research over a matrix
of (subjects × fields).

Purpose
=======

Why this tool exists
--------------------
Open-ended research tasks like "find these 5 data points for these 35
payment-service providers across 7 markets" fail catastrophically in a
single-agent-single-tool-call shape:

1. The LLM chains calls in a retry loop when sources are structurally
   blocked (LinkedIn scraping, gated APIs) — the loop looks busy to
   liveness probes but produces no deliverable.
2. The final table is a single blob at the end — one stuck cell blocks
   the entire delivery.
3. Per-tool timeouts are invisible to the outer orchestrator, so 30
   hung fetches × 60 s each = 30 min of "still working" with nothing to
   show.
4. LLM reasoning-overhead per cell is O(n): ~500 tokens to search, ~500
   to read, ~500 to decide next action.  35 subjects × 5 fields ×
   3 tool calls = 525 LLM round-trips just to populate the matrix.

This module replaces that with a structured pipeline:

   spec (subjects × fields)
        ├─ per-subject worker thread (bounded parallelism)
        │   └─ per-field adapter chain (source_priority order)
        │       ├─ known-hard fields → short-circuit with explicit N/A
        │       ├─ domain circuit-breakers → skip poisoned sources
        │       └─ per-call hard timeouts → no single call blocks a row
        └─ on each subject completion:
            ├─ stream partial row to Signal  (user sees progress)
            └─ record_output_progress()       (stall detector is happy)

The LLM isn't removed from the loop — it still composes search queries,
reads pages, summarises findings — but it's orchestrated by this code,
not by a free-form ReAct loop.  Throughput goes up; failure modes
become visible.

Contract
--------
The tool is invoked by a CrewAI agent with a single JSON string
argument (``spec_json``) describing the research.  See the module-level
``EXAMPLE_SPEC`` for the wire shape.

On completion, returns a JSON string with:

    {
      "subjects": [...],           # researched
      "rows":     [...],           # completed rows, same order as input
      "skipped":  [...],           # rows not completed in budget
      "domain_blocks": {...},      # domains that tripped circuit-breakers
      "meta": {"elapsed": float, "partial_sends": int, ...}
    }

As the research runs, partial rows are sent to the user over Signal —
the return value is redundant for the user but useful for the
calling LLM to compose a final summary.

Design rules
------------
* **Every row is independently shippable.** If row #3 errors, rows
  1/2/4/5 still land.
* **No source is retried more than N times consecutively** — if a
  domain returns N consecutive errors / blocks, it's circuit-broken for
  the rest of the task.
* **No adapter runs longer than its per-call timeout.** A hung fetch
  kills the fetch, not the row.
* **Known-hard fields are marked N/A up front** — we don't burn budget
  trying to scrape LinkedIn personal profiles when we know the source
  blocks bots.
* **Progress streams continuously** — Signal gets a partial row within
  seconds of each subject completing, and the stall detector sees
  activity every time.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from typing import Any, Callable

from crewai.tools import tool

from app.observability.task_progress import (
    current_task_id,
    record_output_progress,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Spec shape
# ══════════════════════════════════════════════════════════════════════

EXAMPLE_SPEC = {
    "title": "PSPs servicing CEE markets",
    "subjects": [
        {"id": "ee-1", "name": "Montonio",        "market": "Estonia"},
        {"id": "lv-1", "name": "Airwallex Latvia","market": "Latvia"},
    ],
    "fields": [
        {"key": "homepage",        "hint": "official company website URL"},
        {"key": "sales_email",     "hint": "sales@ pattern or Contact-Sales page"},
        {"key": "linkedin_company","hint": "public company LinkedIn URL"},
        {
            "key": "linkedin_head_of_sales",
            "hint": "personal LinkedIn of head of sales",
            "known_hard": True,
            "reason": "LinkedIn actively blocks scraping of personal "
                      "profiles; reliable only via Apollo / Sales Navigator.",
        },
        {"key": "short_comment", "hint": "one-sentence summary"},
    ],
    "max_subjects_in_parallel": 2,   # reduced from 4 (2026-04-24) —
                                     # heavier parallelism was correlating
                                     # with external SIGKILL (docker-level)
                                     # under a Docker Desktop + host-load
                                     # combination we couldn't identify.
                                     # Bump back to 4 when infra is stable.
    "budget_seconds": 1500,
    # Highest-trust sources first.  Adapters not in source_priority are
    # skipped.  This is both a whitelist and an ordering hint.
    "source_priority": ["regulator", "company_site", "search"],
}


# ══════════════════════════════════════════════════════════════════════
# Circuit breaker
# ══════════════════════════════════════════════════════════════════════

@dataclass
class _DomainBreaker:
    """Per-domain consecutive-failure counter.  Trips after N strikes;
    once tripped, the orchestrator skips all further calls to that
    domain for the rest of the task."""

    max_consecutive_failures: int = 3
    consecutive: dict[str, int] = field(default_factory=dict)
    tripped: dict[str, str] = field(default_factory=dict)

    def record_success(self, domain: str) -> None:
        if not domain:
            return
        self.consecutive[domain] = 0

    def record_failure(self, domain: str, reason: str) -> None:
        if not domain:
            return
        self.consecutive[domain] = self.consecutive.get(domain, 0) + 1
        if self.consecutive[domain] >= self.max_consecutive_failures:
            self.tripped[domain] = reason

    def is_tripped(self, domain: str) -> bool:
        return domain in self.tripped


# ══════════════════════════════════════════════════════════════════════
# Adapter protocol
# ══════════════════════════════════════════════════════════════════════
#
# An adapter is a stateless function that attempts to find one field
# for one subject using one kind of source.  Return a non-empty string
# on success, a falsy value on "no result", or raise to signal a
# hard failure (circuit breaker counts it).
#
#   Adapter = Callable[[Subject, Field], str | None]
#
# The orchestrator tries adapters in ``spec.source_priority`` order and
# stops at the first non-falsy return.  Each adapter's call is wrapped
# in a per-call timeout by the orchestrator (not by the adapter).

Adapter = Callable[[dict, dict], str | None]


# ── Default adapter set ──────────────────────────────────────────────

def _adapter_regulator(subject: dict, field_spec: dict) -> str | None:
    """Regulator / public-registry lookup.

    Stubbed: real implementation would consult:
      * Estonia — Finantsinspektsioon register of PIs/EMIs
      * Latvia — Latvijas Banka / ex-FKTK register
      * Lithuania — Lietuvos Bankas PSP register
      * Poland — KNF register
      * Romania — BNR register
      * Slovakia — NBS register
      * Czech Republic — ČNB register
      * EBA passporting register (cross-check)

    Each regulator has an HTML or JSON table; add a per-country mapper
    here.  Returns None when the subject isn't found in the registry.
    """
    # Module users can monkey-patch this adapter in tests or register
    # market-specific mappers via ``register_adapter``.
    return None


def _adapter_company_site(subject: dict, field_spec: dict) -> str | None:
    """Direct lookup on the company's own website (scraping /contact,
    /team, homepage for public info).

    Implemented against the project's ``firecrawl_scrape`` when a
    candidate URL is known; returns None when no URL is available and
    the field can't be guessed.
    """
    url = subject.get("homepage") or subject.get("url") or ""
    if not url:
        return None
    if field_spec["key"] == "sales_email":
        # Cheap deterministic guess: sales@<host>.  The real adapter
        # would fetch /contact and regex for mailto: links.
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc.replace("www.", "")
            if host:
                return f"sales@{host}"
        except Exception:
            return None
    return None


def _adapter_search(subject: dict, field_spec: dict) -> str | None:
    """Brave search → best-effort extraction of a single URL or email.

    Uses the project's lightweight Brave wrapper directly (no LLM) for
    fast, deterministic results.  The LLM layer above this tool can
    still re-rank or refine if desired.
    """
    try:
        from app.tools.web_search import search_brave
    except Exception:
        return None

    name = subject.get("name", "").strip()
    market = subject.get("market", "").strip()
    field_key = field_spec["key"]

    # Compose a targeted query per field_key.  The defaults are
    # deliberately conservative — tools built on top of this
    # orchestrator can register richer query builders.
    queries = {
        "homepage":         f'"{name}" {market} payment service provider official site',
        "sales_email":      f'"{name}" sales email contact',
        "linkedin_company": f'"{name}" site:linkedin.com/company',
    }.get(field_key)
    if not queries:
        return None
    try:
        results = search_brave(queries, count=3)
    except Exception as exc:
        raise RuntimeError(f"search failed: {exc}") from exc
    if not results:
        return None
    # Pick the first hit whose URL is plausibly on the right domain.
    for r in results:
        url = (r or {}).get("url") or ""
        if not url:
            continue
        if field_key == "linkedin_company" and "linkedin.com/company" in url:
            return url
        if field_key == "homepage" and name.lower().split()[0] in url.lower():
            return url
        if field_key == "sales_email":
            # Search snippets sometimes expose emails; extract first one.
            import re
            snippet = (r.get("description") or "") + " " + (r.get("title") or "")
            m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", snippet)
            if m:
                return m.group(0)
    # Fallback: return the top URL even if heuristics didn't match —
    # the calling LLM can validate.
    return results[0].get("url") or None


# Registry of adapters keyed by source name.  Users can register
# custom adapters (e.g. an Apollo integration under "paid_data").
_ADAPTERS: dict[str, Adapter] = {
    "regulator":    _adapter_regulator,
    "company_site": _adapter_company_site,
    "search":       _adapter_search,
}


def register_adapter(source_name: str, adapter: Adapter) -> None:
    """Register (or override) an adapter.  Used by specialised modules
    that know how to talk to regulator registries or paid APIs."""
    _ADAPTERS[source_name] = adapter


def install_paid_adapters() -> None:
    """Register the paid-data adapters (Apollo, Sales Navigator / Proxycurl).

    Called lazily from the ``@tool`` wrapper below so the orchestrator
    module can be imported without side effects — tests and the
    light-path researcher agent don't pull the HTTP dependencies
    transitively.  Idempotent.
    """
    try:
        from app.tools.research_adapters import install as _install_all
        _install_all()
    except Exception:
        logger.debug(
            "research_orchestrator: paid-adapter install failed "
            "(non-fatal; adapters remain unavailable this session)",
            exc_info=True,
        )


# ══════════════════════════════════════════════════════════════════════
# Per-field research
# ══════════════════════════════════════════════════════════════════════

def _domain_of(value: str) -> str:
    """Extract domain from a URL-ish value, empty if none."""
    try:
        from urllib.parse import urlparse
        if value and "://" in value:
            return urlparse(value).netloc.replace("www.", "")
    except Exception:
        pass
    return ""


def _research_field(
    subject: dict,
    field_spec: dict,
    source_priority: list[str],
    breaker: _DomainBreaker,
    per_call_timeout: float,
) -> tuple[str, str]:
    """Walk ``source_priority`` in order; return ``(value, source)``
    pair on first hit, or ``("N/A", "blocked|exhausted")`` if no source
    produced a value.

    Each adapter call is wrapped in a hard per-call timeout so one
    hanging fetch can't block the row.  This is not bulletproof
    (Python can't cancel arbitrary blocking C calls) but it stops the
    60-second-per-fetch failure mode that bit us before.
    """
    # ── Short-circuit known-hard fields ─────────────────────────────
    if field_spec.get("known_hard"):
        reason = field_spec.get("reason", "source is structurally blocked")
        return ("N/A", f"known-hard: {reason}")

    # ── Try each source in priority order ───────────────────────────
    #
    # A throwaway single-worker pool per adapter call is deliberate:
    # when an adapter hangs past ``per_call_timeout``, the underlying
    # Python thread cannot actually be killed (the OS has no cheap way
    # to interrupt arbitrary native code), so it keeps running.  With a
    # shared pool, that leaked thread would block subsequent
    # submissions; with a throwaway pool + ``shutdown(wait=False)`` the
    # leak is per-call and the fallback adapter starts immediately.
    last_err = ""
    for source in source_priority:
        adapter = _ADAPTERS.get(source)
        if adapter is None:
            continue
        # Probe domain from the subject's homepage if known; this lets
        # the circuit-breaker short-circuit before the call.
        probe_domain = _domain_of(subject.get("homepage", ""))
        if probe_domain and breaker.is_tripped(probe_domain):
            continue
        pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"adapter-{source}",
        )
        try:
            fut: Future = pool.submit(adapter, subject, field_spec)
            value = fut.result(timeout=per_call_timeout)
        except TimeoutError:
            last_err = f"{source}: timeout"
            breaker.record_failure(probe_domain or source, "timeout")
            continue
        except Exception as exc:
            last_err = f"{source}: {type(exc).__name__}"
            breaker.record_failure(probe_domain or source, str(exc)[:200])
            continue
        finally:
            # Drop the pool without waiting for leaked work — leaked
            # threads eventually complete on their own.
            pool.shutdown(wait=False, cancel_futures=True)
        if value:
            breaker.record_success(probe_domain or source)
            return (str(value)[:500], source)
    return ("N/A", f"exhausted ({last_err})" if last_err else "no source hit")


# ══════════════════════════════════════════════════════════════════════
# Per-subject research
# ══════════════════════════════════════════════════════════════════════

def _research_subject(
    subject: dict,
    fields: list[dict],
    source_priority: list[str],
    breaker: _DomainBreaker,
    per_call_timeout: float,
) -> dict:
    """Populate every field for one subject.  Returns a row dict of
    the shape::

        {
          "id": ...,
          "name": ...,
          "market": ...,
          "values": { field_key: {"value": ..., "source": ...}, ... }
        }
    """
    row = {
        "id": subject.get("id", ""),
        "name": subject.get("name", ""),
        "market": subject.get("market", ""),
        "values": {},
    }
    for field_spec in fields:
        key = field_spec["key"]
        value, source = _research_field(
            subject, field_spec, source_priority, breaker, per_call_timeout,
        )
        row["values"][key] = {"value": value, "source": source}
    return row


# ══════════════════════════════════════════════════════════════════════
# Partial streaming (Signal + progress heartbeat)
# ══════════════════════════════════════════════════════════════════════

def _format_row_for_signal(row: dict) -> str:
    """Render a row as a compact Signal-friendly message."""
    header = f"{row.get('name')} ({row.get('market')})"
    lines = [f"▸ {header}"]
    for k, v in (row.get("values") or {}).items():
        value = v.get("value") if isinstance(v, dict) else v
        source = v.get("source") if isinstance(v, dict) else ""
        if value == "N/A":
            lines.append(f"  {k}: — ({source})")
        else:
            lines.append(f"  {k}: {value}")
    return "\n".join(lines)


def _stream_partial(row: dict) -> int:
    """Send one partial row to the user's Signal thread AND record
    output-progress so the stall detector sees activity.

    Returns 1 if the Signal send succeeded, 0 otherwise.  Failures are
    silent — a Signal hiccup must not abort the research.
    """
    tid = current_task_id.get()
    if not tid:
        return 0
    # Record progress regardless of Signal send success — the row IS
    # progress, even if we couldn't ship it right now.
    try:
        record_output_progress(tid, note=f"row:{row.get('name','')}")
    except Exception:
        logger.debug("task_progress record failed", exc_info=True)

    sent = 0
    try:
        # Lazy import to avoid a startup cycle; signal_client is only
        # needed at runtime from inside a request.
        from app import signal_client as _sc_module
        sc = getattr(_sc_module, "signal_client", None)
        if sc is not None:
            msg = _format_row_for_signal(row)
            sc._send_sync(tid, msg[:1900])  # Signal 2000-char cap
            sent = 1
    except Exception:
        logger.debug("partial Signal send failed (non-fatal)", exc_info=True)
    return sent


# ══════════════════════════════════════════════════════════════════════
# Orchestrator entry point
# ══════════════════════════════════════════════════════════════════════

def orchestrate_research(spec: dict) -> dict:
    """Run research over a ``spec`` and return the aggregated result.

    This is the Python-level entry point.  The @tool wrapper below
    exposes it to CrewAI agents as a single JSON-string argument.
    """
    started = time.monotonic()

    subjects: list[dict] = list(spec.get("subjects") or [])
    fields:   list[dict] = list(spec.get("fields") or [])
    if not subjects or not fields:
        return {
            "error": "spec missing required 'subjects' or 'fields'",
            "rows": [], "skipped": [], "meta": {"elapsed": 0.0},
        }
    source_priority: list[str] = list(
        spec.get("source_priority") or ["regulator", "company_site", "search"]
    )
    parallelism = int(spec.get("max_subjects_in_parallel", 2))
    budget = float(spec.get("budget_seconds", 1500))
    per_call_timeout = float(spec.get("per_call_timeout_seconds", 20))

    # Up-front scoping note for any field flagged known-hard — lets the
    # user know which columns won't be reliably filled before we spend
    # time trying.
    tid = current_task_id.get()
    hard = [f for f in fields if f.get("known_hard")]
    if tid and hard:
        try:
            from app import signal_client as _sc_module
            sc = getattr(_sc_module, "signal_client", None)
            if sc is not None:
                labels = ", ".join(f.get("key") for f in hard)
                reasons = "; ".join(
                    f.get("reason", "source is structurally blocked") for f in hard
                )[:1000]
                sc._send_sync(
                    tid,
                    f"[research_orchestrator] Scoping note: the following "
                    f"field(s) will be returned as N/A by default — "
                    f"{labels}. Reason: {reasons}. I'll stream rows as "
                    f"they complete.",
                )
                record_output_progress(tid, note="scoping_note")
        except Exception:
            logger.debug("scoping note send failed", exc_info=True)

    breaker = _DomainBreaker()
    rows: list[dict] = []
    skipped: list[dict] = []
    partial_sends = 0

    # Worker pool bounded by ``parallelism``.  Each subject research
    # is itself serial across its fields (so per-domain circuit-breakers
    # accumulate coherently), but subjects run in parallel.
    with ThreadPoolExecutor(
        max_workers=max(1, parallelism), thread_name_prefix="research",
    ) as pool:
        futures: dict[Future, dict] = {
            pool.submit(
                _research_subject,
                subj, fields, source_priority, breaker, per_call_timeout,
            ): subj
            for subj in subjects
        }
        for fut in as_completed(futures):
            subj = futures[fut]
            elapsed = time.monotonic() - started
            if elapsed > budget:
                # Out of budget; mark remaining subjects as skipped and
                # don't wait for them.  Partial rows already streamed
                # are preserved.
                skipped.append({
                    "id": subj.get("id"),
                    "name": subj.get("name"),
                    "reason": f"budget_exhausted({elapsed:.0f}s > {budget:.0f}s)",
                })
                fut.cancel()
                continue
            try:
                row = fut.result()
            except Exception as exc:
                skipped.append({
                    "id": subj.get("id"),
                    "name": subj.get("name"),
                    "reason": f"error: {type(exc).__name__}: {str(exc)[:200]}",
                })
                continue
            rows.append(row)
            partial_sends += _stream_partial(row)

    total_elapsed = time.monotonic() - started
    return {
        "title": spec.get("title", ""),
        "rows": rows,
        "skipped": skipped,
        "domain_blocks": dict(breaker.tripped),
        "meta": {
            "elapsed_seconds": round(total_elapsed, 2),
            "subjects_completed": len(rows),
            "subjects_skipped": len(skipped),
            "partial_sends": partial_sends,
            "source_priority": source_priority,
            "parallelism": parallelism,
            "budget_seconds": budget,
        },
    }


# ══════════════════════════════════════════════════════════════════════
# CrewAI tool wrapper
# ══════════════════════════════════════════════════════════════════════

@tool("research_orchestrator")
def research_orchestrator(spec_json: str) -> str:
    """Orchestrate multi-subject, multi-field research with partial
    streaming and per-domain circuit breakers.

    USE THIS TOOL when the user asks for a table / matrix of data about
    N entities (companies, products, people), each with M attributes
    (URLs, contact info, summaries).  Much more reliable than
    hand-driving web_search + firecrawl_scrape for large matrices.

    INPUT  (``spec_json``, JSON string)::

      {
        "title": "short description of the research",
        "subjects": [{"id": "...", "name": "...", "market": "..."}, ...],
        "fields":   [{"key": "...", "hint": "..."}, ...],
        "max_subjects_in_parallel": 4,  (optional, default 4)
        "budget_seconds":           1500, (optional, default 1500 = 25 min)
        "per_call_timeout_seconds": 20, (optional, default 20)
        "source_priority": ["regulator","company_site","search"]
                                         (optional, this is the default)
      }

    Fields can be marked ``"known_hard": true`` with a ``"reason"`` to
    skip up front (e.g. LinkedIn personal profiles which block scraping).
    Those cells come back as "N/A" without burning budget.

    OUTPUT (JSON string)::

      {
        "rows":          [ {id, name, market, values: {key: {value, source}}} ],
        "skipped":       [ {id, name, reason} ],
        "domain_blocks": {domain: reason},
        "meta":          {elapsed_seconds, ...}
      }

    As research runs, partial rows are streamed to the user over
    Signal — don't repeat them in your final answer; instead summarise
    / interpret.
    """
    try:
        spec = json.loads(spec_json) if isinstance(spec_json, str) else spec_json
        if not isinstance(spec, dict):
            return json.dumps({"error": "spec must be a JSON object"})
    except Exception as exc:
        return json.dumps({"error": f"spec_json parse failed: {exc}"})

    # Lazy, idempotent.  Ensures Apollo + Sales-Navigator adapters are
    # registered before the caller's spec references them via
    # source_priority.
    install_paid_adapters()

    result = orchestrate_research(spec)
    return json.dumps(result, ensure_ascii=False, default=str)

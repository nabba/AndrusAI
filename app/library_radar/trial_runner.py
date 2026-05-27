"""Library trial runner — turn `pending` discoveries into trial results.

PROGRAM §46.13 (Q10.1). The :mod:`library_radar.proposer` discovers
candidate libraries and stages a proposal CR with a smoke-test
coding-session spec. THIS module runs the actual smoke test inside
a coding-session sandbox and, on pass, files an *adoption CR* for
the ``requirements.txt`` edit.

Flow per pending discovery::

    pending  →  running  →  passed    →  adoption_cr_filed
                       \\─→  failed

  1. Pop a row from ``trial_state.list_pending()`` (newest first,
     capped at ``_MAX_TRIALS_PER_PASS`` per cycle so we don't
     exhaust the coding-session quota).
  2. Look up the matching :mod:`proposal_bridge` ProposalState to
     fetch the ``coding_session_spec`` (smoke test path + pytest
     command + candidate package name).
  3. Start a coding-session via the production manager singleton.
  4. Write the smoke test (a minimal ``import <pkg>`` test) into
     the session worktree.
  5. Run ``pytest`` via the sandboxed runner.
  6. On exit-0: file an *adoption CR* for ``requirements.txt`` that
     adds the package. Standard operator gate. Mark the trial
     ``adoption_cr_filed``.
  7. On non-zero or refused: mark ``failed`` with the short reason.
  8. Discard the session (it's stateless from a feature point of
     view; the trial_state ledger carries the audit trail).

Master switch: ``LIBRARY_TRIAL_RUNNER_ENABLED`` (default ON).
Cadence-checked internally — 24h between full passes. Per-pass cap
of 3 trials prevents the runner from monopolising the coding-session
quota when a backlog accumulates.

Failure-isolated end-to-end:
  * coding-session unavailable → trial stays pending, retry next pass.
  * pytest non-zero → trial marked failed, no adoption CR.
  * change_requests unavailable → trial stays passed (re-tries
    adoption CR creation next pass).
"""
from __future__ import annotations

import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_RUN_CADENCE_S = 24 * 3600
_MAX_TRIALS_PER_PASS = 3
_PYTEST_TIMEOUT_S = 120
_PIP_TIMEOUT_S = 180

_STATE_FILE = "library_trial_runner.json"


def _enabled() -> bool:
    return os.getenv("LIBRARY_TRIAL_RUNNER_ENABLED", "true").lower() in (
        "true", "1", "yes", "on",
    )


def _now_ts() -> float:
    import time as _t
    return _t.time()


# ─────────────────────────────────────────────────────────────────────
#   Candidate gate — PyPI resolution
# ─────────────────────────────────────────────────────────────────────


_PYPI_TIMEOUT_S = 5.0
_PYPI_BASE = "https://pypi.org/pypi"


def _normalize_pypi_name(name: str) -> str:
    """PEP 503 normalisation for the PyPI JSON endpoint."""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def _pypi_status(name: str) -> str:
    """Return ``exists`` / ``absent`` / ``unknown`` for a candidate.

    ``unknown`` means we genuinely could not tell (network or transport
    error) — the caller retries later instead of failing the discovery.
    A 404 is the only definitive ``absent``."""
    norm = _normalize_pypi_name(name)
    if not norm:
        return "absent"
    req = urllib.request.Request(
        f"{_PYPI_BASE}/{norm}/json",
        headers={"User-Agent": "AndrusAI-library-radar/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_PYPI_TIMEOUT_S) as resp:
            return "exists" if resp.status == 200 else "absent"
    except urllib.error.HTTPError as exc:
        return "absent" if exc.code == 404 else "unknown"
    except Exception:
        return "unknown"


def _candidate_names(state) -> list[str]:
    """Package-name candidates in priority order, derived from the
    discovery slug (the slugified title). Tries the most specific
    hyphenated join first so "X Agents SDK" → "x-agents-sdk" / "x-agents"
    before the bare brand token "x"::

        slug "openai_agents_sdk"
          → ["openai-agents-sdk", "openai-agents", "openai"]

    The longest contiguous prefix that resolves on PyPI wins (checked by
    the caller); we fall back to the bare brand token, and finally fail
    if nothing resolves. This upgrades brand tokens like ``openai`` /
    ``claude`` / ``google`` to the real distributions ``openai-agents`` /
    ``claude-agent-sdk`` / ``google-adk`` without an LLM or a (defunct)
    PyPI search API. Only slug prefixes + the stored lead token are
    probed — never trailing tokeniser noise — so mastra→'industry' can't
    recur."""
    tokens = [t for t in re.split(r"[-_]+", (state.slug or "").lower()) if t]
    tokens = tokens[:5]  # bound the PyPI probe count
    names: list[str] = ["-".join(tokens[:k]) for k in range(len(tokens), 0, -1)]
    lead = (
        state.package_name
        or (state.candidates[0] if state.candidates else "")
    ).strip()
    if lead:
        names.append(lead)
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.lower()
        if n and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _select_candidate(state) -> tuple[str | None, bool, str]:
    """Resolve the discovery to a real PyPI distribution, preferring the
    most specific name (see :func:`_candidate_names`).

    Returns ``(package, terminal_if_none, reason)``:
      * ``package`` set            → trial this name.
      * ``(None, True, reason)``   → nothing resolves on PyPI; mark FAILED.
      * ``(None, False, reason)``  → PyPI unreachable; leave PENDING, retry.
    """
    names = _candidate_names(state)
    if not names:
        return None, True, "no candidate package names"
    saw_unknown = False
    for name in names:
        status = _pypi_status(name)
        if status == "exists":
            return name, False, f"resolved {name!r} on PyPI"
        if status == "unknown":
            saw_unknown = True
    if saw_unknown:
        return None, False, f"PyPI unreachable; will retry ({names[0]!r})"
    return None, True, f"no PyPI distribution for {names!r}"


# ─────────────────────────────────────────────────────────────────────
#   Smoke-test rendering
# ─────────────────────────────────────────────────────────────────────


def render_smoke_test(package: str, slug: str) -> str:
    """Generate the smoke-test Python body.

    Installs are by *distribution* name (e.g. ``openai-agents``) but the
    import name frequently differs — ``openai-agents`` imports as
    ``agents``, ``google-adk`` as ``google.adk``, ``pyyaml`` as ``yaml``.
    Guessing ``dist.replace('-','_')`` therefore fails for exactly the
    SDK packages the brand-token resolver now finds. So the test
    discovers the importable top-level module(s) the installed
    distribution actually provides (via ``importlib.metadata``) and
    imports those. Only modules belonging to OUR distribution are tried,
    so an unrelated top-level ``agents``/``sdk`` can't false-pass.

    Still no LLM in the test body — import targets come from installed
    metadata, not from anything the radar imagined."""
    # Anchor the test name to the slug so the path matches the spec
    # regardless of which candidate was picked.
    return (
        f'"""Smoke-import test for {package} (auto-generated by\n'
        f'library_radar.trial_runner). PROGRAM §46.13."""\n'
        f"import importlib\n"
        f"import importlib.metadata as _md\n"
        f"\n"
        f"_DIST = {package!r}\n"
        f"\n"
        f"\n"
        f"def _norm(s):\n"
        f"    return s.replace('-', '_').replace('.', '_').lower()\n"
        f"\n"
        f"\n"
        f"def _import_names():\n"
        f"    names = []\n"
        f"    try:\n"
        f"        for imp, dists in _md.packages_distributions().items():\n"
        f"            if any(_norm(d) == _norm(_DIST) for d in dists):\n"
        f"                names.append(imp)\n"
        f"    except Exception:\n"
        f"        pass\n"
        f"    if not names:\n"
        f"        try:\n"
        f"            for f in (_md.files(_DIST) or []):\n"
        f"                top = str(f).split('/')[0]\n"
        f"                if top and not top.endswith(('.dist-info', '.data')):\n"
        f"                    names.append(top[:-3] if top.endswith('.py') else top)\n"
        f"        except Exception:\n"
        f"            pass\n"
        f"    if not names:\n"
        f"        names.append(_DIST.replace('-', '_'))\n"
        f"    seen = set(); out = []\n"
        f"    for n in names:\n"
        f"        if n and n not in seen:\n"
        f"            seen.add(n); out.append(n)\n"
        f"    return out\n"
        f"\n"
        f"\n"
        f"def test_{slug[:30]}_import():\n"
        f"    errors = {{}}\n"
        f"    for name in _import_names():\n"
        f"        try:\n"
        f"            mod = importlib.import_module(name)\n"
        f"        except Exception as exc:\n"
        f"            errors[name] = repr(exc); continue\n"
        f"        if [a for a in dir(mod) if not a.startswith('_')]:\n"
        f"            return\n"
        f"        errors[name] = 'no public attributes'\n"
        f"    raise AssertionError(f'{{_DIST}}: not importable; tried {{errors}}')\n"
    )


# ─────────────────────────────────────────────────────────────────────
#   Adoption CR
# ─────────────────────────────────────────────────────────────────────


def _file_adoption_cr(
    *,
    signature: str,
    package: str,
    title: str,
    trial_log_excerpt: str,
) -> str | None:
    """Build + persist the adoption CR. Returns the CR id on success."""
    try:
        from app.change_requests.lifecycle import create_request
        from app.change_requests.models import RiskClass
    except Exception:
        logger.warning(
            "trial_runner: change_requests unavailable",
            exc_info=True,
        )
        return None

    req_path = Path(os.environ.get(
        "LIBRARY_RADAR_REQUIREMENTS_PATH",
        "/app/requirements.txt",
    ))
    try:
        old_content = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
    except OSError:
        old_content = ""

    # Conservative pin. NOTE: keep this addition free of per-signature
    # data (title/signature live in `reason`, which the CR content-hash
    # excludes) so multiple discoveries resolving to the SAME package
    # dedup into one CR via change_requests' content-hash, instead of
    # filing N near-identical pins (e.g. 5 OpenRouter discoveries → 1 CR).
    addition = (
        f"\n# added via library_radar trial-canary-adopt (PROGRAM §46.13)\n"
        f"{package}\n"
    )
    new_content = old_content.rstrip("\n") + ("\n" if old_content else "") + addition

    reason = (
        f"Trial-canary-adopt (PROGRAM §46.13 Q10.1): "
        f"library `{package}` passed smoke import test in a "
        f"coding-session sandbox. Filing the requirements.txt "
        f"pin for operator review.\n\n"
        f"Discovery: {title}\n\n"
        f"## Trial log excerpt\n\n"
        f"```\n{trial_log_excerpt[:800]}\n```\n"
        f"\n"
        f"Originating library_radar signature: `{signature}`.\n"
    )
    try:
        cr = create_request(
            requestor="library_radar_trial",
            path="requirements.txt",
            new_content=new_content,
            old_content=old_content,
            reason=reason,
            risk_class=RiskClass.STANDARD,
        )
    except Exception:
        logger.warning(
            "trial_runner: create_request raised for %s",
            signature, exc_info=True,
        )
        return None
    return cr.id


# ─────────────────────────────────────────────────────────────────────
#   One trial
# ─────────────────────────────────────────────────────────────────────


def _run_one_trial(state, proposal) -> tuple[str, str]:
    """Run a single smoke trial. Returns (final_status, reason)
    where final_status ∈ {passed, failed, adoption_cr_filed,
    pending} and reason is a short human-readable explanation.

    ``state`` is :class:`trial_state.TrialState`.
    ``proposal`` is the :mod:`proposal_bridge` ProposalState for
    the matching signature (carries coding_session_spec).
    """
    from app.library_radar import trial_state

    spec = getattr(proposal, "coding_session_spec", None)
    if not isinstance(spec, dict):
        msg = "missing coding_session_spec on proposal"
        trial_state.mark_failed(state.signature, error=msg)
        return "failed", msg

    # Candidate gate (incident 2026-05-27): resolve the discovery to a
    # real PyPI distribution via slug-derived prefixes — "X Agents SDK"
    # → "x-agents" rather than the bare brand "x" — preferring the most
    # specific match, never falling through to trailing tokeniser noise
    # (which once picked mastra→'industry'). No PyPI match → fail the
    # discovery so the queue advances, never a misleading requirements CR.
    package, terminal_if_none, sel_reason = _select_candidate(state)
    if not package:
        if terminal_if_none:
            trial_state.mark_failed(state.signature, error=sel_reason)
            return "failed", sel_reason
        # PyPI unreachable for the survivors — retry on a later pass
        # rather than burning the discovery on a transient outage.
        return "pending", sel_reason

    # Find the smoke-test file path from spec.files (the entry with
    # action=create).
    smoke_path: str | None = None
    for entry in (spec.get("files") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("action") == "create" and "test" in (entry.get("path") or ""):
            smoke_path = entry["path"]
            break
    if not smoke_path:
        msg = "spec has no smoke-test file entry"
        trial_state.mark_failed(state.signature, error=msg)
        return "failed", msg

    # Start a coding-session
    try:
        from app.coding_session.runtime import get_manager, worktree_root
    except Exception:
        return "pending", "coding_session runtime unavailable"
    try:
        mgr = get_manager()
        session = mgr.start(
            agent_id="library_radar_trial",
            base="HEAD",
            purpose=f"Library smoke trial: {package}",
            worktree_root=worktree_root(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "trial_runner: session start failed for %s: %s",
            state.signature, exc,
        )
        return "pending", f"session start failed: {exc}"

    trial_state.mark_running(
        state.signature, session_id=session.id, package_name=package,
    )

    try:
        worktree = Path(session.worktree_path)
        smoke_full = worktree / smoke_path
        smoke_full.parent.mkdir(parents=True, exist_ok=True)
        smoke_full.write_text(
            render_smoke_test(package=package, slug=state.slug),
            encoding="utf-8",
        )

        # Try `pip install <pkg>` first so the smoke test has
        # something to import. Pip needs to be in the runner
        # allowlist; if not, the trial gracefully degrades to
        # "failed with reason 'pip not allowed'" without harming
        # the gateway environment.
        from app.coding_session.runner import run as sandbox_run
        pip_result = sandbox_run(
            argv=["pip", "install", "--no-cache-dir", "--quiet", package],
            cwd=str(worktree),
            timeout_s=_PIP_TIMEOUT_S,
        )
        if pip_result.refused:
            # Skip install — the package may already be present in
            # the parent env (since we're inside the gateway
            # container) — fall through to pytest and let it speak.
            logger.debug(
                "trial_runner: pip install refused (allowlist?): %s",
                pip_result.refusal_reason,
            )
        elif pip_result.exit_code != 0:
            tail = (pip_result.stderr or pip_result.stdout)[-400:]
            trial_state.mark_failed(
                state.signature,
                error=f"pip install exit={pip_result.exit_code}: {tail}",
                pytest_exit=None,
            )
            return "failed", f"pip install failed (exit={pip_result.exit_code})"

        pytest_result = sandbox_run(
            argv=["pytest", str(smoke_path), "-q", "--no-header"],
            cwd=str(worktree),
            timeout_s=_PYTEST_TIMEOUT_S,
        )
        if pytest_result.refused:
            msg = f"pytest refused: {pytest_result.refusal_reason}"
            trial_state.mark_failed(state.signature, error=msg)
            return "failed", msg

        if pytest_result.exit_code != 0:
            tail = (pytest_result.stdout or pytest_result.stderr)[-400:]
            trial_state.mark_failed(
                state.signature,
                error=f"pytest exit={pytest_result.exit_code}: {tail}",
                pytest_exit=pytest_result.exit_code,
            )
            return "failed", (
                f"pytest failed (exit={pytest_result.exit_code})"
            )

        # Pass — file the adoption CR
        trial_state.mark_passed(
            state.signature, pytest_exit=pytest_result.exit_code,
        )
        cr_id = _file_adoption_cr(
            signature=state.signature,
            package=package,
            title=getattr(proposal, "title", "") or package,
            trial_log_excerpt=(pytest_result.stdout or "")[:800],
        )
        if cr_id:
            trial_state.mark_adoption_filed(state.signature, cr_id=cr_id)
            return "adoption_cr_filed", f"adoption CR {cr_id} filed"
        # If CR creation failed we leave state as "passed" so the
        # next pass retries the adoption-CR step.
        return "passed", "passed; adoption CR creation will retry"
    finally:
        # Always free the worktree — the trial state ledger carries
        # everything we need for retrospection.
        try:
            mgr.discard(
                session.id,
                reason="library_radar_trial: smoke test complete",
            )
        except Exception:
            logger.debug(
                "trial_runner: discard failed for %s",
                session.id, exc_info=True,
            )


# ─────────────────────────────────────────────────────────────────────
#   Public entry point — idle job
# ─────────────────────────────────────────────────────────────────────


def run_one_pass() -> dict[str, Any]:
    """Walk pending trials, run up to _MAX_TRIALS_PER_PASS. Returns
    a structured result dict."""
    summary: dict[str, Any] = {
        "status": "ran",
        "passed": 0,
        "failed": 0,
        "adoption_cr_filed": 0,
        "deferred": 0,
        "considered": 0,
    }
    if not _enabled():
        summary["status"] = "skipped_disabled"
        return summary

    try:
        from app.library_radar import trial_state
        from app.proposal_bridge import store as bridge_store
    except Exception:
        summary["status"] = "subsystem_unavailable"
        return summary

    pending = trial_state.list_pending(limit=_MAX_TRIALS_PER_PASS * 2)
    summary["considered"] = len(pending)
    if not pending:
        summary["status"] = "no_pending"
        return summary

    # Resolve proposals once. The store API is list_proposals(source=...) —
    # calling a non-existent list_all() here silently disabled the whole
    # trial→adoption pipeline for ~11 days (incident 2026-05-27): the
    # AttributeError was swallowed, proposals_by_sig stayed empty, and
    # every pending trial deferred as "no proposal".
    proposals_by_sig: dict[str, Any] = {}
    try:
        for p in bridge_store.list_proposals(source="library_radar"):
            proposals_by_sig[p.signature] = p
    except Exception:
        logger.warning(
            "trial_runner: bridge store list_proposals failed",
            exc_info=True,
        )
    if pending and not proposals_by_sig:
        logger.warning(
            "trial_runner: %d pending trial(s) but 0 library_radar "
            "proposals resolved from the bridge store — all trials will "
            "defer; check the proposal_bridge store API",
            len(pending),
        )

    ran = 0
    for state in pending:
        if ran >= _MAX_TRIALS_PER_PASS:
            summary["deferred"] += 1
            continue
        proposal = proposals_by_sig.get(state.signature)
        if proposal is None:
            logger.debug(
                "trial_runner: no proposal for %s; deferring",
                state.signature,
            )
            summary["deferred"] += 1
            continue
        try:
            status, reason = _run_one_trial(state, proposal)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trial_runner: trial raised for %s: %s",
                state.signature, exc, exc_info=True,
            )
            try:
                trial_state.mark_failed(
                    state.signature,
                    error=f"trial runner exception: {exc}",
                )
            except Exception:
                pass
            summary["failed"] += 1
            continue
        ran += 1
        if status == "adoption_cr_filed":
            summary["adoption_cr_filed"] += 1
        elif status == "passed":
            summary["passed"] += 1
        elif status == "failed":
            summary["failed"] += 1
        else:
            summary["deferred"] += 1
        logger.info(
            "trial_runner: %s → %s (%s)",
            state.signature, status, reason,
        )
    return summary


# ─────────────────────────────────────────────────────────────────────
#   Idle-job wrapper
# ─────────────────────────────────────────────────────────────────────


def run() -> dict[str, Any]:
    """Idle-scheduler entry point. Cadence-guarded via the canonical
    healing-handler common state helper."""
    if not _enabled():
        return {"status": "skipped_disabled"}
    try:
        from app.healing.handlers._common import (
            read_state_json, write_state_json,
        )
    except Exception:
        # Fall through to unconditional run when the cadence helper
        # is unavailable.
        return run_one_pass()
    state = read_state_json(_STATE_FILE, {"last_run_at": 0.0})
    now = _now_ts()
    if now - float(state.get("last_run_at", 0)) < _RUN_CADENCE_S:
        return {"status": "skipped_cadence"}
    state["last_run_at"] = now
    result = run_one_pass()
    try:
        write_state_json(_STATE_FILE, state)
    except Exception:
        pass
    return result

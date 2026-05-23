"""Bundle a coding-session diff into change requests.

The ``submit_session`` function is the single escape hatch from a
worktree to production. It runs:

  1. Validates the session is ACTIVE
  2. Discovers changed paths via the backend's
     ``list_changed_paths(worktree_path)``
  3. For each path:
       a. Reads ``new_content`` from the worktree (or empty for D)
       b. Reads ``old_content`` from the base sha (or empty for A)
       c. Calls the change-request port's ``create_request(...)``
       d. If the resulting CR is PENDING, calls ``send_ask(cr.id)``
       e. Builds a SubmitResult row
  4. Calls ``manager.submit(session_id, results=...)`` to mark the
     session SUBMITTED and store the results
  5. Calls ``manager.remove_worktree(session)`` to clean up

The change-request port is injectable: tests pass a fake; production
uses the default that lazy-imports ``app.change_requests``. This
keeps Phase 5.4-c's unit tests independent of #54 — the integration
runs end-to-end once both branches land.

What submit handles correctly:

  * **Per-file split** — each touched file becomes its own change
    request. Operator sees one Signal ASK per file.
  * **TIER_IMMUTABLE refusal** — the change-request validator
    rejects the file at request time; we record a SubmitResult with
    no change_request_id and the validator's reason. Other files in
    the batch still submit normally.
  * **Validator failure** — same shape as TIER_IMMUTABLE refusal;
    the SubmitResult carries the validator's reason.
  * **New files** — base read raises FileNotFoundError; we use ""
    for old_content.
  * **Deleted files** (kind 'D') — Phase 5.4-c does NOT handle
    deletes (the change-request system has no delete primitive).
    We record a SubmitResult with refusal_reason "delete-not-supported";
    the agent must use a different workflow if it wants to remove a
    file. Tracked as a follow-up.
  * **Rename** (kind 'R') — treated as an add of the new path. The
    old path's content disappears from the resulting branch; the
    change request records the new file. (Conservative; full rename
    semantics come later if needed.)

What submit does NOT handle:

  * Re-opening a SUBMITTED session — re-iteration is a fresh session.
    The manager's ``submit()`` is the gatekeeper — it raises
    IllegalTransition on already-terminal sessions.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.coding_session.manager import (
    IllegalTransition,
    Manager,
)
from app.coding_session.models import CodingSession, SubmitResult

logger = logging.getLogger(__name__)


# ── Change-request port ─────────────────────────────────────────────


class ChangeRequestPort(Protocol):
    """Seam between coding_session and the change-request system.

    Production wires ``DefaultChangeRequestPort`` which lazy-imports
    ``app.change_requests``. Tests pass a fake.
    """

    def create_request(
        self,
        *,
        requestor: str,
        path: str,
        new_content: str,
        old_content: str,
        reason: str,
    ) -> Any:
        """Create a ChangeRequest. Returns an object with ``.id`` and
        ``.status`` (a string-valued enum)."""

    def send_ask(self, request_id: str) -> int | None:
        """Send the Signal ASK; returns the message ts or None on
        failure. The submit module logs but doesn't fail the whole
        batch on send_ask errors."""


class DefaultChangeRequestPort:
    """Production-default port. Lazy-imports the real change-request
    module so unit tests of submit_session don't pull in the whole
    app.change_requests dependency tree."""

    def create_request(
        self,
        *,
        requestor: str,
        path: str,
        new_content: str,
        old_content: str,
        reason: str,
    ) -> Any:
        from app.change_requests import create_request

        return create_request(
            requestor=requestor,
            path=path,
            new_content=new_content,
            old_content=old_content,
            reason=reason,
        )

    def send_ask(self, request_id: str) -> int | None:
        from app.change_requests import send_ask

        return send_ask(request_id)


# ── Submit ──────────────────────────────────────────────────────────


def submit_session(
    session_id: str,
    *,
    submit_reason: str,
    manager: Manager,
    port: ChangeRequestPort | None = None,
    cleanup_worktree: bool = True,
    submit_mode: str = "per-file",
    branch_name: str | None = None,
    pr_title: str | None = None,
    pr_body: str | None = None,
    with_type_check: bool = False,
) -> tuple[CodingSession, list[SubmitResult]]:
    """Discover the worktree's changes, file change requests, and
    finalize the session.

    Args:
        session_id: the session to submit. Must be ACTIVE.
        submit_reason: operator-facing explanation; appended to each
            change request's reason after the session's purpose.
        manager: the lifecycle manager (provides backend access).
        port: change-request seam; defaults to
            :class:`DefaultChangeRequestPort` (lazy-imports the real
            module).
        cleanup_worktree: if True (default), tear down the worktree
            after submit. Tests pass False to inspect the worktree
            after submit.
        submit_mode: ``"per-file"`` (default — pre-2026-05-20 behavior:
            one change-request per touched file) or ``"branch"``
            (Phase 2 piece 2i: one commit + push + PR for the whole
            worktree). Branch mode requires the worktree backend to
            expose ``submit_as_branch``; backends that don't
            (LocalWorktreeBackend) raise ``IllegalTransition``.
        branch_name: optional branch name for ``submit_mode="branch"``;
            defaults to ``coding-session-<session_id_short>``.
        pr_title: optional PR title; defaults to the session purpose
            (first line, ≤80 chars).
        pr_body: optional PR body; defaults to the session purpose +
            submit_reason.
        with_type_check: if True (Phase 3 v2 follow-up, 2026-05-22),
            run the pyright sidecar against each touched ``.py`` file
            and attach error-severity diagnostics to the resulting
            ``SubmitResult.type_errors``. Observational — never blocks
            submit or alters CR status. Requires both
            ``pyright_sidecar_enabled=True`` AND a pyright binary on
            PATH; either gate failing leaves ``type_errors`` empty.

    Returns:
        ``(updated_session, [SubmitResult, ...])`` — the session in
        SUBMITTED status with ``submit_results`` populated, plus the
        same list returned for the caller's convenience (typically
        the tools layer).

    Raises:
        :class:`IllegalTransition` — session not ACTIVE / not found,
        or submit_mode/branch backend not supported.
    """
    if submit_mode not in ("per-file", "branch"):
        raise IllegalTransition(
            f"submit: invalid submit_mode {submit_mode!r}; "
            f"expected 'per-file' or 'branch'",
        )

    cs = manager.get(session_id)
    if cs is None:
        raise IllegalTransition(f"submit: session {session_id!r} not found")
    if not cs.is_active:
        raise IllegalTransition(
            f"submit: session is {cs.status.value} (not ACTIVE)"
        )

    port = port or DefaultChangeRequestPort()
    backend = manager.backend  # WorktreeBackend has the read methods

    if submit_mode == "branch":
        return _submit_as_branch(
            cs=cs,
            backend=backend,
            manager=manager,
            submit_reason=submit_reason,
            branch_name=branch_name,
            pr_title=pr_title,
            pr_body=pr_body,
            cleanup_worktree=cleanup_worktree,
        )

    # 1. Discover changed paths
    try:
        changes = backend.list_changed_paths(worktree_path=cs.worktree_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "submit: list_changed_paths failed for session %s: %s",
            session_id, exc,
        )
        manager.fail(session_id, reason=f"list_changed_paths failed: {exc}")
        # Re-raise so the tool surface returns a clean error
        raise

    results: list[SubmitResult] = []

    # PROGRAM §45.2 Q7.2 — schema-aware augmentation: if the diff
    # touches any schema-owning path, generate an extra migration
    # stub CR alongside the per-file fanout. Path-only detection
    # (no content-regex). The operator approves both the code CR
    # AND the migration CR via the normal gate. Failure-isolated.
    try:
        from app.coding_session.schema_migrations import (
            detect_schema_changes, render_migration_stub,
        )
        changed_paths_only = [p for p, _kind in changes]
        hint = detect_schema_changes(changed_paths_only)
        if hint is not None:
            stub = render_migration_stub(
                hint,
                session_id=session_id,
                purpose=getattr(cs, "purpose", "") or submit_reason,
            )
            migration_path = f"migrations/{hint.suggested_filename}"
            try:
                migration_result = _submit_one_synthesized_file(
                    cs=cs,
                    path=migration_path,
                    content=stub,
                    reason=(
                        f"[Q7.2 schema-aware submit] Migration stub for "
                        f"schema changes touching: "
                        f"{', '.join(hint.detected_paths[:3])}. Operator "
                        f"must fill in SQL before approving."
                    ),
                    port=port,
                )
                results.append(migration_result)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "submit: migration-stub CR failed for session %s: %s",
                    session_id, exc, exc_info=True,
                )
    except Exception:
        logger.debug(
            "submit: schema-migration hook failed for session %s",
            session_id, exc_info=True,
        )

    # 2. Per-file: build content + reason; call port; record result
    for path, kind in changes:
        try:
            result = _submit_one_file(
                cs=cs,
                path=path,
                kind=kind,
                submit_reason=submit_reason,
                port=port,
                backend=backend,
            )
        except Exception as exc:  # noqa: BLE001
            # An unexpected error per-file shouldn't kill the batch.
            # Record it as a refusal with the exception text and move
            # on. The session still terminates cleanly with the rest
            # of the results captured.
            logger.warning(
                "submit: file %s in session %s raised: %s",
                path, session_id, exc, exc_info=True,
            )
            result = SubmitResult(
                path=path,
                change_request_id=None,
                status="error",
                refusal_reason=f"{type(exc).__name__}: {exc}",
            )
        results.append(result)

    # 2b. Phase 3 v2 (2026-05-22) — optional pyright type-check pass.
    # Mutates each `.py` result's ``type_errors`` in place. Failure-
    # isolated: a sick sidecar / missing binary / per-file exception
    # never blocks the submit. Skips non-Python files, refusals
    # (no real content was applied), and the synthetic migration-stub
    # row (already covered by the human review of the SQL).
    if with_type_check:
        _attach_type_errors_to_results(
            results=results,
            worktree_path=cs.worktree_path,
        )

    # 3. Mark session SUBMITTED + store the results
    updated = manager.submit(session_id, results=results)

    # 4. Tear down worktree (best-effort; failure non-fatal)
    if cleanup_worktree:
        ok, err = manager.remove_worktree(updated)
        if not ok:
            logger.warning(
                "submit: worktree teardown failed for session %s: %s",
                session_id, err,
            )

    return updated, results


# ── CR-keyed type-error lookup (Phase 3 v2 follow-up, 2026-05-22) ───


def find_type_errors_for_cr(cr_id: str) -> dict | None:
    """Look up the ``SubmitResult.type_errors`` recorded for the change
    request with id ``cr_id``.

    Walks every coding session in the store (newest-first), inspects
    each session's ``submit_results``, and returns the first matching
    row's payload. Returns ``None`` when no match is found.

    Returns:
      ``{session_id, path, submitted_at, type_errors}`` on hit;
      ``None`` on miss.

    Failure-isolated: a sick store / corrupt session row never
    propagates — the caller treats None as "no data" the same way
    it would treat a real miss.

    Used by:
      * ``GET /api/cp/changes/{id}/type-errors`` REST endpoint
      * Any future operator surface that wants to render type-error
        context alongside a CR
    """
    if not cr_id:
        return None
    try:
        from app.coding_session import store as cs_store
    except Exception:
        return None
    try:
        sessions = cs_store.list_all() or []
    except Exception:
        logger.debug(
            "find_type_errors_for_cr: list_all failed", exc_info=True,
        )
        return None

    # Newest-first ordering — list_all may not sort; we sort defensively
    # by ``submitted_at`` desc so the most recent attribution wins.
    def _sort_key(cs):
        return (getattr(cs, "submitted_at", "") or "") or ""

    try:
        sessions_sorted = sorted(sessions, key=_sort_key, reverse=True)
    except Exception:
        sessions_sorted = list(sessions)

    for cs in sessions_sorted:
        results = getattr(cs, "submit_results", None) or []
        for r in results:
            if getattr(r, "change_request_id", None) != cr_id:
                continue
            # Match found — surface the payload even if type_errors is
            # empty (operator may want to know "type-check ran clean").
            return {
                "session_id": getattr(cs, "id", "") or "",
                "path": getattr(r, "path", "") or "",
                "submitted_at": getattr(cs, "submitted_at", "") or "",
                "type_errors": list(getattr(r, "type_errors", None) or []),
            }
    return None


def build_type_error_count_map() -> dict[str, int]:
    """Build a single ``{cr_id: error_count}`` map across all sessions.

    Optimised for the list-endpoint use case where we want a row-level
    badge but don't want N round trips. One scan over the store, one
    pass per session's submit_results — O(sessions × results), not
    O(sessions × results × CRs).

    Behavior:
      * Sessions without submit_results: skipped silently.
      * SubmitResults with empty type_errors: NOT included (operators
        only care about CRs with positive error count).
      * SubmitResults without change_request_id (refusals): skipped.
      * Multiple sessions hitting the same CR id: newest wins (matches
        :func:`find_type_errors_for_cr`).

    Failure-isolated: a sick store → empty dict, never raises.
    """
    try:
        from app.coding_session import store as cs_store
    except Exception:
        return {}
    try:
        sessions = cs_store.list_all() or []
    except Exception:
        logger.debug(
            "build_type_error_count_map: list_all failed", exc_info=True,
        )
        return {}

    # Sort oldest-first so newer rows overwrite as we iterate forward —
    # the final state has the newest attribution for each CR id, which
    # matches find_type_errors_for_cr's "newest wins" semantics.
    def _sort_key(cs):
        return (getattr(cs, "submitted_at", "") or "") or ""

    try:
        sessions_sorted = sorted(sessions, key=_sort_key)
    except Exception:
        sessions_sorted = list(sessions)

    out: dict[str, int] = {}
    for cs in sessions_sorted:
        results = getattr(cs, "submit_results", None) or []
        for r in results:
            cr_id = getattr(r, "change_request_id", None)
            if not cr_id:
                continue
            errors = getattr(r, "type_errors", None) or []
            if not errors:
                # Don't pollute the map with zero-count entries —
                # the badge code treats missing as zero anyway.
                out.pop(cr_id, None)
                continue
            out[cr_id] = len(errors)
    return out


# ── Type-check pass (Phase 3 v2 follow-up, 2026-05-22) ──────────────


def _attach_type_errors_to_results(
    *,
    results: list[SubmitResult],
    worktree_path: str | None,
) -> None:
    """Mutate ``results`` in place: for each .py SubmitResult whose
    CR was actually created (change_request_id present), run the
    pyright sidecar over the worktree-resolved path and attach the
    error-severity diagnostics.

    Skips:
      * Refusals (no ``change_request_id`` — the file wasn't applied)
      * Non-Python files (``.py`` suffix gate)
      * Synthetic migration stubs (status="error" / unresolved CR)

    Failure-isolated end-to-end:
      * Sidecar disabled → all type_errors stay empty
      * Binary missing → all type_errors stay empty
      * Per-file exception → that result keeps empty type_errors,
        the rest still get checked
      * Missing worktree_path → no-op (can't resolve relative paths)
    """
    if not worktree_path:
        return

    try:
        from app.code_intel.pyright_sidecar import check_file
    except Exception:
        logger.debug(
            "submit: pyright_sidecar unavailable; skipping type-check pass",
            exc_info=True,
        )
        return

    from pathlib import Path
    root = Path(worktree_path)

    for r in results:
        # Only check .py files with a successfully-created CR
        if r.change_request_id is None:
            continue
        if not r.path.endswith(".py"):
            continue
        try:
            report = check_file(root / r.path)
        except Exception:
            logger.debug(
                "submit: pyright check_file raised for %s in session",
                r.path, exc_info=True,
            )
            continue
        # Only attach error-severity rows — warnings + info would
        # bloat the SubmitResult without giving the operator
        # actionable signal at the gate.
        r.type_errors = [
            d.to_dict() for d in report.diagnostics
            if d.severity == "error"
        ]


# ── Branch path (Phase 2 piece 2i, 2026-05-20) ──────────────────────


def _submit_as_branch(
    *,
    cs: CodingSession,
    backend,
    manager: Manager,
    submit_reason: str,
    branch_name: str | None,
    pr_title: str | None,
    pr_body: str | None,
    cleanup_worktree: bool,
) -> tuple[CodingSession, list[SubmitResult]]:
    """Commit + push + open PR for the whole worktree as one branch.

    Mirrors the dict-return shape of
    ``BridgeWorktreeBackend.submit_as_branch`` — one SubmitResult
    capturing the branch-submit outcome. On success: status="branch_submitted"
    with the PR URL in ``refusal_reason`` (which doubles as the
    operator-facing extra context). On failure: status="error".
    """
    if not hasattr(backend, "submit_as_branch"):
        raise IllegalTransition(
            "submit_mode='branch' requires a worktree backend with "
            "submit_as_branch (production BridgeWorktreeBackend has "
            "it; LocalWorktreeBackend does not). Use 'per-file' or "
            "wire a branch-capable backend.",
        )

    branch = (branch_name or _default_branch_name(cs)).strip()
    title = (pr_title or _default_pr_title(cs)).strip()
    body = (pr_body or _default_pr_body(cs, submit_reason)).strip()
    commit_message = title  # one-line commit; PR body holds prose

    try:
        result = backend.submit_as_branch(
            worktree_path=cs.worktree_path,
            branch=branch,
            commit_message=commit_message,
            pr_title=title,
            pr_body=body,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "submit: backend.submit_as_branch raised for session %s: %s",
            cs.id, exc, exc_info=True,
        )
        manager.fail(cs.id, reason=f"submit_as_branch raised: {exc}")
        # Re-raise so the tool surface returns a clean error
        raise

    if not result.get("ok"):
        sr = SubmitResult(
            path=f"branch:{branch}",
            change_request_id=None,
            status="error",
            refusal_reason=str(result.get("error") or "submit_as_branch failed"),
        )
        updated = manager.submit(cs.id, results=[sr])
        if cleanup_worktree:
            ok, err = manager.remove_worktree(updated)
            if not ok:
                logger.warning(
                    "submit: branch-mode teardown failed for %s: %s",
                    cs.id, err,
                )
        return updated, [sr]

    # Success — pr_url goes into the refusal_reason slot as a
    # diagnostic note (the field is mis-named for this path; future
    # cleanup may add a dedicated `note` slot to SubmitResult).
    pr_url = result.get("pr_url") or ""
    note_parts = [f"commit {(result.get('commit_sha') or '')[:12]}"]
    if pr_url:
        note_parts.append(f"PR {pr_url}")
    else:
        note_parts.append("PR not opened (operator can open manually)")
    sr = SubmitResult(
        path=f"branch:{branch}",
        change_request_id=None,
        status="branch_submitted",
        refusal_reason=" / ".join(note_parts),
    )
    updated = manager.submit(cs.id, results=[sr])
    if cleanup_worktree:
        ok, err = manager.remove_worktree(updated)
        if not ok:
            logger.warning(
                "submit: branch-mode teardown failed for %s: %s",
                cs.id, err,
            )
    return updated, [sr]


def _default_branch_name(cs: CodingSession) -> str:
    """coding-session-<id_short> — stable across retries (re-submit
    of the same session reuses the same branch)."""
    return f"coding-session-{cs.id[:8]}"


def _default_pr_title(cs: CodingSession) -> str:
    """First line of the session purpose, capped at 72 chars (git
    commit convention) so the commit message stays readable."""
    purpose = (cs.purpose or "").strip()
    if not purpose:
        return f"Coding session {cs.id[:8]}"
    first_line = purpose.splitlines()[0]
    if len(first_line) > 72:
        first_line = first_line[:69] + "..."
    return first_line


def _default_pr_body(cs: CodingSession, submit_reason: str) -> str:
    """Multi-line PR body: purpose + submit reason + session id for
    traceability."""
    parts = []
    if cs.purpose:
        parts.append(cs.purpose.strip())
    if submit_reason and submit_reason.strip():
        parts.append("")
        parts.append(f"**Submit reason:** {submit_reason.strip()}")
    parts.append("")
    parts.append(f"_Filed by coding session `{cs.id}` (agent: `{cs.agent_id}`)._")
    return "\n".join(parts)


# ── Per-file path ───────────────────────────────────────────────────


def _submit_one_synthesized_file(
    *,
    cs: CodingSession,
    path: str,
    content: str,
    reason: str,
    port: ChangeRequestPort,
) -> SubmitResult:
    """Q7.2 — file a CR for a file the session DIDN'T actually create
    (the schema-migration stub). Empty ``old_content`` (file is new),
    synthesized ``new_content``, attribution still tied to the session
    so audit trail is intact."""
    cr = port.create_request(
        requestor=cs.agent_id,
        path=path,
        new_content=content,
        old_content="",  # synthesized file — no base content
        reason=(
            f"{cs.purpose}\n\n"
            f"{reason}\n\n"
            f"[synthesized by coding session {cs.id}; Q7.2 schema-aware submit]"
        ),
    )
    cr_status = _status_value(cr)
    if cr_status == "PENDING":
        try:
            port.send_ask(_id_value(cr))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "submit: send_ask for synthesized CR %s failed: %s",
                _id_value(cr), exc,
            )
    return SubmitResult(
        path=path,
        change_request_id=_id_value(cr),
        status=cr_status.lower() if cr_status else "unknown",
        refusal_reason=None,
    )


def _submit_one_file(
    *,
    cs: CodingSession,
    path: str,
    kind: str,
    submit_reason: str,
    port: ChangeRequestPort,
    backend: Any,
) -> SubmitResult:
    """Build the change-request payload for one file and dispatch it."""
    if kind == "D":
        # Deletes are out of scope for v1 — the change-request system
        # only writes content, not removes files. Operator can do it
        # manually if needed.
        return SubmitResult(
            path=path,
            change_request_id=None,
            status="refused",
            refusal_reason=(
                "delete-not-supported: the change-request system has "
                "no delete primitive in v1. To remove a file, the "
                "operator must do it manually via PR."
            ),
        )

    if kind == "?":
        return SubmitResult(
            path=path,
            change_request_id=None,
            status="refused",
            refusal_reason=f"unknown change kind for path {path!r}",
        )

    # Read the new content (worktree state)
    try:
        new_content = backend.read_worktree_file(
            worktree_path=cs.worktree_path, path=path,
        )
    except FileNotFoundError:
        # Race: file was modified-then-deleted; treat as delete refusal
        return SubmitResult(
            path=path,
            change_request_id=None,
            status="refused",
            refusal_reason=(
                f"file {path!r} disappeared from worktree during submit; "
                "treat as delete (not supported)."
            ),
        )

    # Read the old content (base sha state)
    try:
        old_content = backend.read_base_file(base_sha=cs.base_sha, path=path)
    except FileNotFoundError:
        # Added file: no base content
        old_content = ""

    # Build the per-CR reason. The operator sees this in the React UI
    # and the Signal ASK; tying it to the session id makes audit
    # forensics easier.
    full_reason = (
        f"{cs.purpose}\n\n"
        f"{submit_reason}\n\n"
        f"[from coding session {cs.id}, change kind {kind}]"
    )

    # Dispatch
    cr = port.create_request(
        requestor=cs.agent_id,
        path=path,
        new_content=new_content,
        old_content=old_content,
        reason=full_reason,
    )

    cr_status = _status_value(cr)

    # If the request landed PENDING, fire the Signal ASK. Failures
    # there are non-fatal (the request is still visible in React);
    # we log but don't change the SubmitResult shape.
    if cr_status == "pending":
        try:
            ts = port.send_ask(_id_value(cr))
            logger.debug(
                "submit: send_ask for %s returned ts=%r",
                _id_value(cr), ts,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "submit: send_ask raised for %s: %s",
                _id_value(cr), exc,
            )

    refusal = None
    if cr_status in {"tier_immutable_refused", "rejected"}:
        # Validator rejection — surface the validator's reason
        refusal = _decision_reason(cr) or "rejected at validation"

    return SubmitResult(
        path=path,
        change_request_id=_id_value(cr),
        status=cr_status,
        refusal_reason=refusal,
    )


# ── Duck-type accessors ─────────────────────────────────────────────


def _id_value(cr: Any) -> str:
    """Pull ``cr.id`` defensively (for fakes that might use a string)."""
    val = getattr(cr, "id", None)
    if val is None:
        raise AttributeError(
            f"change request {cr!r} has no .id attribute"
        )
    return str(val)


def _status_value(cr: Any) -> str:
    """Pull the change-request status as a lowercase string. The real
    ``Status`` enum is ``str``-valued so ``.value`` works; tests can
    pass plain strings too."""
    s = getattr(cr, "status", None)
    if s is None:
        raise AttributeError(
            f"change request {cr!r} has no .status attribute"
        )
    if hasattr(s, "value"):
        return str(s.value).lower()
    return str(s).lower()


def _decision_reason(cr: Any) -> str | None:
    return getattr(cr, "decision_reason", None)

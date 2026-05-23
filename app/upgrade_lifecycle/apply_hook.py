"""P0#1b — Apply hook for approved upgrade decision CRs.

PROGRAM §63 follow-up. Closes the architectural loop between
``docs/proposed_upgrades/<sig>.md`` decision CRs (paper trail, lands
in an allowed root) and the actual mutation of ``requirements.txt``
(curated by :mod:`app.upgrade_lifecycle.requirements_writer`).

Flow:

  1. U4 (or U6 acceptance) stages a CR with target_path under
     ``docs/proposed_upgrades/``. Body starts with a YAML front-matter
     block:

         ---
         action: bump_requirement
         package: starlette
         from_version: 0.52.1
         to_version: 1.0.1
         ---

  2. Operator approves the CR via ``/cp/changes``. The CR-application
     machinery writes the markdown to ``docs/proposed_upgrades/<sig>.md``
     (the decision record).

  3. This daemon polls the change-request audit log for newly-APPLIED
     CRs at ``docs/proposed_upgrades/``, reads the file, parses the
     YAML front-matter, and dispatches to the appropriate writer.

  4. ``requirements_writer.apply_bump(...)`` does the line edit on
     ``requirements.txt``. The continuity ledger event marks the
     bump in the identity timeline.

Failure-isolated end to end: every dispatch wraps in try/except; a
hook failure files a Signal alert but never blocks the daemon.

State: per-CR-id idempotency token at
``workspace/upgrade_lifecycle/apply_hook_state.json`` so re-runs after
a crash never double-apply.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


APPLY_HOOK_CADENCE_S = 600           # 10 min poll
WARMUP_S = 180
DAEMON_THREAD_NAME = "ul-apply-hook"
_DOCS_PREFIX = "docs/proposed_upgrades/"

_driver_lock = threading.Lock()
_driver_started = False
_stop_event = threading.Event()


# ── Master switch ────────────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_upgrade_lifecycle_apply_hook_enabled
        return get_upgrade_lifecycle_apply_hook_enabled()
    except Exception:
        return False


# ── State (idempotency tokens) ───────────────────────────────────────────


def _state_path() -> Path:
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "apply_hook_state.json"
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / "apply_hook_state.json"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/apply_hook_state.json")


def _read_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("ul.apply_hook: state write failed", exc_info=True)


# ── YAML front-matter parser (stdlib-only) ───────────────────────────────


_FRONT_MATTER_RE = re.compile(
    r"\A\s*---\s*\n(?P<body>.*?)\n---\s*\n",
    re.DOTALL,
)
_KV_LINE_RE = re.compile(r"^\s*(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(?P<value>.*?)\s*$")


def parse_front_matter(text: str) -> Optional[dict[str, str]]:
    """Extract the leading ``--- ... ---`` block as a flat dict of strings.

    Returns ``None`` when the text doesn't start with a front-matter
    block — that's the signal to skip the CR (it's a docs-only proposal,
    not an upgrade we should apply).

    Deliberately stdlib-only — no PyYAML dep, no nested parsing. Bump
    intents are flat key=value strings; anything richer routes through
    a different file format.
    """
    m = _FRONT_MATTER_RE.match(text or "")
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group("body").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _KV_LINE_RE.match(line)
        if not match:
            continue
        out[match.group("key")] = match.group("value")
    return out


# ── Dispatcher ───────────────────────────────────────────────────────────


def dispatch(*, front_matter: dict[str, str], cr_id: str,
             reason: str) -> dict:
    """Route a parsed front-matter dict to the right writer.

    Returns a small status dict for logging.
    """
    action = front_matter.get("action", "").strip()
    if action == "bump_requirement":
        return _dispatch_bump(front_matter, cr_id=cr_id, reason=reason)
    if action == "bump_python":
        return _dispatch_python_bump(front_matter, cr_id=cr_id, reason=reason)
    return {"ok": False, "reason": f"unknown_action:{action!r}"}


def _dispatch_bump(front_matter: dict[str, str], *, cr_id: str,
                  reason: str) -> dict:
    pkg = front_matter.get("package", "").strip()
    to_ver = front_matter.get("to_version", "").strip()
    if not pkg or not to_ver:
        return {"ok": False, "reason": "missing_package_or_version"}

    # D#a (PROGRAM §63.10) — detect the project's package manager
    # and route to the right writer. pip → requirements_writer;
    # uv / poetry / pdm → pyproject_writer.
    manager_evidence = "pip"
    try:
        from app.upgrade_lifecycle.package_manager import (
            detect_manager,
            PackageManager,
            writer_can_handle,
        )
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[2]
        detection = detect_manager(repo_root)
        manager_evidence = detection.manager.value
        if not writer_can_handle(detection):
            # uv / poetry / pdm path → pyproject_writer.
            return _dispatch_pyproject_bump(
                package=pkg, to_version=to_ver, cr_id=cr_id, reason=reason,
                manager_evidence=manager_evidence,
            )
    except Exception:
        logger.debug("apply_hook: detect_manager failed", exc_info=True)

    try:
        from app.upgrade_lifecycle.requirements_writer import apply_bump
    except Exception:
        return {"ok": False, "reason": "writer_unavailable"}
    res = apply_bump(
        package=pkg,
        to_version=to_ver,
        requestor="upgrade_lifecycle",
        reason=f"CR {cr_id} approved: {reason[:200]}",
    )
    return {
        "ok": res.ok,
        "reason": res.reason,
        "package": pkg,
        "to_version": to_ver,
        "manager": manager_evidence,
        "diff_lines": list(res.diff_lines),
    }


def _dispatch_pyproject_bump(
    *, package: str, to_version: str, cr_id: str, reason: str,
    manager_evidence: str,
) -> dict:
    """Route bumps to pyproject_writer when uv/poetry/pdm detected."""
    try:
        from app.upgrade_lifecycle.pyproject_writer import apply_bump
    except Exception:
        return {"ok": False, "reason": "pyproject_writer_unavailable"}
    res = apply_bump(
        package=package,
        to_version=to_version,
        requestor="upgrade_lifecycle",
        reason=f"CR {cr_id} approved: {reason[:200]}",
    )
    if res.ok and res.lockfile_hint:
        _notify_lockfile_regen_needed(
            cr_id=cr_id, package=package, to_version=to_version,
            manager=manager_evidence, lockfile_hint=res.lockfile_hint,
        )
    return {
        "ok": res.ok,
        "reason": res.reason,
        "package": package,
        "to_version": to_version,
        "manager": manager_evidence,
        "section": res.table_section,
        "lockfile_hint": res.lockfile_hint,
        "diff_lines": list(res.diff_lines),
    }


def _notify_lockfile_regen_needed(
    *, cr_id: str, package: str, to_version: str,
    manager: str, lockfile_hint: str,
) -> None:
    """Surface a Signal alert that the lockfile needs regenerating.

    pyproject.toml + lockfile drift is a real pitfall — the writer
    mutates pyproject but the lock now points at the OLD constraint.
    Container builds will silently get the new version (because the
    new build runs the manager's install which reads pyproject), but
    local dev environments stay pinned to the old lock until
    `uv sync` / `poetry lock` runs.
    """
    try:
        from app.notify import notify
        notify(
            title="📦 Lockfile regen needed",
            body=(
                f"CR `{cr_id}` bumped {package} → {to_version} in "
                f"pyproject.toml ({manager} project). {lockfile_hint}"
            ),
            url="/cp/changes",
            topic=f"lockfile_regen:{package}_to_{to_version}",
            critical=False, arbitrate=False,
        )
    except Exception:
        logger.debug("apply_hook: lockfile notify failed", exc_info=True)


def _dispatch_python_bump(front_matter: dict[str, str], *, cr_id: str,
                         reason: str) -> dict:
    """P0#4 — Python version upgrade dispatch.

    Calls dockerfile_writer + fires a loud Signal alert (Python bumps
    are higher impact than requirements bumps — operator should know
    immediately even if they approved the underlying CR).
    """
    from_ver = front_matter.get("from_version", "").strip()
    to_ver = front_matter.get("to_version", "").strip()
    if not to_ver:
        return {"ok": False, "reason": "missing_to_version"}
    try:
        from app.upgrade_lifecycle.dockerfile_writer import apply_bump
    except Exception:
        return {"ok": False, "reason": "writer_unavailable"}
    res = apply_bump(
        to_version=to_ver,
        from_version=from_ver or None,
        requestor="upgrade_lifecycle",
        reason=f"CR {cr_id} approved: {reason[:200]}",
    )
    if res.ok and res.reason == "ok":
        _notify_python_bump_applied(
            cr_id=cr_id, from_version=res.old_version,
            to_version=res.new_version,
            sha_pin_dropped=res.sha_pin_dropped,
        )
    return {
        "ok": res.ok,
        "reason": res.reason,
        "from_version": res.old_version,
        "to_version": res.new_version,
        "sha_pin_dropped": res.sha_pin_dropped,
        "diff_lines": list(res.diff_lines),
    }


def _notify_python_bump_applied(*, cr_id: str, from_version: str,
                                to_version: str,
                                sha_pin_dropped: bool) -> None:
    """Loud Signal alert after a Python bump lands.

    Operator approved the CR knowing this would happen, but the
    consequences (container rebuild + SHA re-pin needed) are big
    enough that a separate notification is warranted.
    """
    try:
        from app.notify import notify
        body = (
            f"Python {from_version} → {to_version} bumped in Dockerfile "
            f"via CR `{cr_id}`. "
        )
        if sha_pin_dropped:
            body += (
                "**SHA digest pin was dropped** — re-pin BEFORE the "
                "next deploy. See the TODO comment in Dockerfile. "
            )
        body += "Container rebuild required for the change to take effect."
        notify(
            title=f"🐍 Python {to_version} bump applied",
            body=body,
            url="/cp/changes",
            topic=f"python_bump:{from_version}_to_{to_version}",
            critical=True,    # high-impact change deserves louder bell
            arbitrate=False,    # bypass arbiter — operator must see this
        )
    except Exception:
        logger.debug("apply_hook: python-bump notify failed", exc_info=True)


# ── Audit-log polling ────────────────────────────────────────────────────


def _cr_audit_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "change_requests" / "audit.jsonl"
    except Exception:
        return Path("/app/workspace/change_requests/audit.jsonl")


def _iter_recent_applied_upgrades(audit_path: Optional[Path] = None):
    """Yield (cr_id, path, reason) for CRs at docs/proposed_upgrades/
    whose latest status is APPLIED.

    Walks the audit JSONL once per pass; collapses multiple status
    rows per CR to the latest.
    """
    path = audit_path or _cr_audit_path()
    if not path.exists():
        return
    rows_by_cr: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cr_id = row.get("cr_id") or row.get("id") or ""
                target = str(row.get("path") or row.get("target_path") or "")
                if not cr_id or not target.startswith(_DOCS_PREFIX):
                    continue
                rows_by_cr[cr_id] = row
    except OSError:
        return
    for cr_id, row in rows_by_cr.items():
        status = str(row.get("status") or row.get("transition") or "").lower()
        if status not in ("applied", "approved", "applied_ok"):
            continue
        yield cr_id, str(row.get("path") or row.get("target_path") or ""), str(row.get("reason") or "")


def _read_landed_file(target_path: str) -> Optional[str]:
    """Read the markdown body that landed on disk after CR approval."""
    try:
        from app.paths import WORKSPACE_ROOT
        # CR-applied files land at repo root (relative to gateway working
        # directory). Try both repo-root and workspace-root for safety.
        for base in (
            Path(__file__).resolve().parents[2],
            Path(WORKSPACE_ROOT).parent,
        ):
            p = base / target_path
            if p.exists():
                return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


# ── Public driver ────────────────────────────────────────────────────────


def run_one_pass(
    *,
    audit_path: Optional[Path] = None,
    file_reader: Optional[callable] = None,
    bump_dispatcher: Optional[callable] = None,
) -> dict:
    """One sweep of the audit log. Returns summary dict for logging.

    Idempotent — already-processed cr_ids are skipped via the state
    file. Crash-safe — state is persisted after each successful
    dispatch.
    """
    out = {
        "ok": True, "processed": 0, "skipped": 0,
        "errors": 0, "dispatched": [],
    }
    if not _enabled():
        out["ok"] = False
        out["reason"] = "master_switch_off"
        return out

    state = _read_state()
    processed_ids = set(state.get("processed_cr_ids") or [])
    reader = file_reader or _read_landed_file
    dispatcher = bump_dispatcher or dispatch

    new_processed = list(processed_ids)
    for cr_id, target_path, reason in _iter_recent_applied_upgrades(audit_path):
        if cr_id in processed_ids:
            out["skipped"] += 1
            continue

        body = reader(target_path)
        if body is None:
            logger.debug(
                "ul.apply_hook: CR %s landed file not readable at %s",
                cr_id, target_path,
            )
            out["errors"] += 1
            continue

        fm = parse_front_matter(body)
        if fm is None:
            # Doc-only proposal (no upgrade intent). Mark processed
            # so we don't keep re-reading it.
            new_processed.append(cr_id)
            out["skipped"] += 1
            continue

        try:
            result = dispatcher(
                front_matter=fm, cr_id=cr_id, reason=reason,
            )
        except Exception as exc:
            logger.debug("ul.apply_hook: dispatch raised", exc_info=True)
            result = {"ok": False, "reason": f"dispatch_exception:{exc}"}

        out["processed"] += 1
        out["dispatched"].append({"cr_id": cr_id, "result": result})

        if result.get("ok"):
            new_processed.append(cr_id)
        else:
            out["errors"] += 1
            # Don't mark errored CRs as processed — next pass retries.
            # Surface a Signal alert (failure-isolated).
            _notify_apply_failed(cr_id, result)

    if new_processed != list(processed_ids):
        state["processed_cr_ids"] = sorted(set(new_processed))
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        # Garbage-collect very old entries to keep the file small (~1000 cap).
        if len(state["processed_cr_ids"]) > 1000:
            state["processed_cr_ids"] = state["processed_cr_ids"][-1000:]
        _write_state(state)

    return out


def _notify_apply_failed(cr_id: str, result: dict) -> None:
    try:
        from app.notify import notify
        notify(
            title="📦 Upgrade apply failed",
            body=(
                f"CR `{cr_id}` was approved but the apply step failed: "
                f"{result.get('reason')!r}. Operator: investigate "
                f"workspace/upgrade_lifecycle/ and "
                f"requirements_writer logs."
            ),
            url="/cp/changes",
            topic=f"ul_apply_failed:{cr_id}",
            critical=False, arbitrate=True,
        )
    except Exception:
        logger.debug("ul.apply_hook: notify failed", exc_info=True)


# ── Daemon ───────────────────────────────────────────────────────────────


def _driver() -> None:
    if _stop_event.wait(WARMUP_S):
        return
    while not _stop_event.is_set():
        try:
            run_one_pass()
        except Exception:
            logger.debug("ul.apply_hook: pass raised", exc_info=True)
        if _stop_event.wait(APPLY_HOOK_CADENCE_S):
            return


def _thread_alive() -> bool:
    """A1-P0 — true iff our daemon thread is currently alive.

    The watchdog's respawn contract needs this check (not a sticky
    flag) so a dead thread can actually be respawned.
    """
    return any(
        t.name == DAEMON_THREAD_NAME and t.is_alive()
        for t in threading.enumerate()
    )


def start() -> bool:
    """Start the apply-hook daemon. Thread-liveness-aware per the
    watchdog contract — refuses only when an ALIVE thread by our
    canonical name already exists.
    """
    global _driver_started
    if not _enabled():
        return False
    with _driver_lock:
        if _thread_alive():
            return False
        if _stop_event.is_set():
            _stop_event.clear()
        thread = threading.Thread(
            target=_driver, name=DAEMON_THREAD_NAME, daemon=True,
        )
        thread.start()
        _driver_started = True
    logger.info("ul.apply_hook: daemon started")
    return True


def stop() -> None:
    _stop_event.set()


def is_running() -> bool:
    return _thread_alive()

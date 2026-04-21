"""Control Plane dashboard API routes.

Provides REST endpoints for the React dashboard.
All routes prefixed with /api/cp/.
"""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cp", tags=["control-plane"])

# ── Request models ───────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    mission: str = ""
    description: str = ""

class TicketCreate(BaseModel):
    title: str
    description: str = ""
    project_id: str = ""
    priority: int = 5

class TicketUpdate(BaseModel):
    status: str = ""
    result_summary: str = ""

class CommentCreate(BaseModel):
    author: str = "user"
    content: str

class BudgetOverride(BaseModel):
    project_id: str
    agent_role: str
    new_limit: float
    approver: str = "user"

class BudgetSet(BaseModel):
    project_id: str
    agent_role: str
    limit_usd: float
    limit_tokens: int = None

# ── Projects ─────────────────────────────────────────────────────────────────

@router.get("/projects")
def list_projects():
    from app.control_plane.projects import get_projects
    return get_projects().list_all()

@router.post("/projects")
def create_project(body: ProjectCreate):
    from app.control_plane.projects import get_projects
    result = get_projects().create(body.name, body.mission, body.description)
    if not result:
        raise HTTPException(400, "Failed to create project")
    return result

@router.get("/projects/{project_id}")
def get_project(project_id: str):
    from app.control_plane.projects import get_projects
    proj = get_projects().get_by_id(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return proj

@router.get("/projects/{project_id}/status")
def project_status(project_id: str):
    from app.control_plane.projects import get_projects
    return get_projects().get_status(project_id)

# ── Tickets ──────────────────────────────────────────────────────────────────

@router.get("/tickets")
def list_tickets(
    project_id: str = Query(None),
    status: str = Query(None),
    limit: int = Query(50),
):
    from app.control_plane.tickets import get_tickets
    if status:
        from app.control_plane.db import execute
        rows = execute(
            """SELECT * FROM control_plane.tickets
               WHERE (%s IS NULL OR project_id::text = %s)
                 AND status = %s
               ORDER BY created_at DESC LIMIT %s""",
            (project_id, project_id, status, limit), fetch=True,
        )
        return rows or []
    return get_tickets().get_recent(project_id, limit)

@router.get("/tickets/board")
def ticket_board(project_id: str = Query(None)):
    from app.control_plane.tickets import get_tickets
    return get_tickets().get_board(project_id)

@router.post("/tickets")
def create_ticket(body: TicketCreate):
    from app.control_plane.tickets import get_tickets
    from app.control_plane.projects import get_projects
    pid = body.project_id or get_projects().get_active_project_id()
    result = get_tickets().create_manual(body.title, pid, body.description, body.priority)
    if not result:
        raise HTTPException(400, "Failed to create ticket")
    return result

@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    from app.control_plane.tickets import get_tickets
    ticket = get_tickets().get(ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket

@router.put("/tickets/{ticket_id}")
def update_ticket(ticket_id: str, body: TicketUpdate):
    from app.control_plane.tickets import get_tickets
    tm = get_tickets()
    requeued = False
    if body.status == "done":
        tm.complete(ticket_id, body.result_summary or "Closed")
    elif body.status == "failed":
        tm.fail(ticket_id, body.result_summary or "Failed")
    elif body.status:
        from app.control_plane.db import execute
        execute(
            "UPDATE control_plane.tickets SET status = %s, updated_at = NOW() WHERE id = %s",
            (body.status, ticket_id),
        )
        if body.status == "todo":
            # Drag-to-todo from the dashboard means "run this again" — spawn
            # Commander in the background so a crew actually picks it up
            # instead of the ticket sitting orphaned.
            requeued = _requeue_ticket_async(ticket_id)
    return {"status": "updated", "requeued": requeued}


def _requeue_ticket_async(ticket_id: str) -> bool:
    """Fire-and-forget: dispatch the ticket's title through Commander so the
    existing routing pipeline assigns it to a crew and runs it.

    Leaves the original ticket in the 'todo' column as a historical marker
    and comments on it so the audit trail is obvious. Returns True when a
    background worker was spawned, False when the prerequisites couldn't be
    assembled (missing ticket, Commander unavailable, etc).
    """
    import threading
    try:
        from app.control_plane.tickets import get_tickets
        ticket = get_tickets().get(ticket_id)
        if not ticket:
            return False
        title = (ticket.get("title") or "").strip()
        if not title:
            return False

        def _worker():
            try:
                try:
                    get_tickets().add_comment(
                        ticket_id, "dashboard",
                        "Re-queued via dashboard drag-to-todo; routing through Commander.",
                    )
                except Exception:
                    logger.debug("requeue: comment write failed", exc_info=True)
                try:
                    from app.agents.commander import Commander
                    Commander().handle(title, sender="dashboard")
                except Exception:
                    logger.warning("requeue: commander dispatch failed", exc_info=True)
            except Exception:
                logger.debug("requeue: worker crashed", exc_info=True)

        threading.Thread(
            target=_worker,
            name=f"ticket-requeue-{ticket_id[:8]}",
            daemon=True,
        ).start()
        return True
    except Exception:
        logger.debug("requeue: setup failed", exc_info=True)
        return False

@router.post("/tickets/{ticket_id}/comments")
def add_comment(ticket_id: str, body: CommentCreate):
    from app.control_plane.tickets import get_tickets
    get_tickets().add_comment(ticket_id, body.author, body.content)
    return {"status": "added"}

# ── Budgets ──────────────────────────────────────────────────────────────────

@router.get("/budgets")
def get_budgets(project_id: str = Query(None)):
    from app.control_plane.budgets import get_budget_enforcer
    return get_budget_enforcer().get_status(project_id)

@router.post("/budgets")
def set_budget(body: BudgetSet):
    from app.control_plane.budgets import get_budget_enforcer
    get_budget_enforcer().set_budget(body.project_id, body.agent_role, body.limit_usd, body.limit_tokens)
    return {"status": "set"}

@router.post("/budgets/override")
def override_budget(body: BudgetOverride):
    from app.control_plane.budgets import get_budget_enforcer
    get_budget_enforcer().override_budget(body.project_id, body.agent_role, body.new_limit, body.approver)
    return {"status": "overridden"}

# ── Audit ────────────────────────────────────────────────────────────────────

@router.get("/audit")
def get_audit_log(
    project_id: str = Query(None),
    actor: str = Query(None),
    action: str = Query(None),
    limit: int = Query(50),
):
    from app.control_plane.audit import get_audit
    return get_audit().query(
        project_id=project_id, actor=actor,
        action_prefix=action, limit=limit,
    )

@router.get("/audit/costs")
def audit_costs(project_id: str = Query(None)):
    from app.control_plane.audit import get_audit
    return get_audit().cost_summary(project_id)

# ── Governance ───────────────────────────────────────────────────────────────

@router.get("/governance/pending")
def pending_governance(project_id: str = Query(None)):
    from app.control_plane.governance import get_governance
    return get_governance().get_pending(project_id)

@router.post("/governance/{request_id}/approve")
def approve_governance(request_id: str):
    from app.control_plane.governance import get_governance
    ok = get_governance().approve(request_id)
    if not ok:
        raise HTTPException(404, "Request not found or already resolved")
    return {"status": "approved"}

@router.post("/governance/{request_id}/reject")
def reject_governance(request_id: str):
    from app.control_plane.governance import get_governance
    ok = get_governance().reject(request_id)
    if not ok:
        raise HTTPException(404, "Request not found or already resolved")
    return {"status": "rejected"}

# ── Org Chart ────────────────────────────────────────────────────────────────

@router.get("/org-chart")
def get_org_chart_api():
    from app.control_plane.org_chart import get_org_chart
    return get_org_chart()


# ── Delegation-mode toggles (shown on Org Chart page) ────────────────────────
# When ON for a crew, tasks go to Coordinator + specialists instead of a
# single monolithic agent.  See app/crews/delegation_settings.py.

class DelegationUpdate(BaseModel):
    enabled: bool


@router.get("/delegation")
def get_delegation_settings():
    """Return {crew: bool} for every crew that supports delegation mode."""
    try:
        from app.crews.delegation_settings import get_all
        return {"settings": get_all()}
    except Exception as exc:
        raise HTTPException(500, f"delegation settings unavailable: {exc}")


@router.post("/delegation/{crew}")
def set_delegation_setting(crew: str, body: DelegationUpdate):
    """Enable or disable delegation mode for a specific crew."""
    try:
        from app.crews.delegation_settings import set_enabled
        updated = set_enabled(crew, body.enabled)
        if crew not in updated:
            raise HTTPException(404, f"unknown crew: {crew}")
        return {"settings": updated, "crew": crew, "enabled": body.enabled}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"delegation toggle failed: {exc}")


# ── System Health (aggregated from existing systems) ─────────────────────────

@router.get("/health")
def control_plane_health():
    """Aggregated system health for dashboard."""
    from app.control_plane.db import execute_scalar
    from app.control_plane.governance import get_governance
    ticket_count = execute_scalar("SELECT COUNT(*) FROM control_plane.tickets") or 0
    audit_count = execute_scalar("SELECT COUNT(*) FROM control_plane.audit_log") or 0
    pending = get_governance().pending_count()
    return {
        "status": "ok",
        "tickets_total": ticket_count,
        "audit_entries": audit_count,
        "governance_pending": pending,
    }

# ── Costs ────────────────────────────────────────────────────────────────────

@router.get("/costs/by-agent")
def costs_by_agent(project_id: str = Query(None)):
    from app.control_plane.audit import get_audit
    return get_audit().cost_summary(project_id)

@router.get("/costs/daily")
def costs_daily(project_id: str = Query(None), days: int = Query(30)):
    from app.control_plane.db import execute
    rows = execute(
        """SELECT DATE(timestamp) as day,
                  SUM(cost_usd) as total_cost,
                  SUM(tokens) as total_tokens,
                  COUNT(*) as call_count
           FROM control_plane.audit_log
           WHERE cost_usd IS NOT NULL
             AND (%s IS NULL OR project_id::text = %s)
             AND timestamp >= NOW() - INTERVAL '%s days'
           GROUP BY DATE(timestamp)
           ORDER BY day DESC""",
        (project_id, project_id, days), fetch=True,
    )
    return rows or []

# ── Operations: errors, anomalies, self-deploy pipeline ──────────────────────

@router.get("/errors")
def recent_errors(limit: int = Query(20, ge=1, le=200)):
    """Recent errors + pattern counts from the self-heal journal."""
    recent: list[dict] = []
    patterns: dict[str, int] = {}
    err: str | None = None
    try:
        from app.self_heal import get_recent_errors, get_error_patterns
        recent = list(get_recent_errors(limit) or [])
        patterns = dict(get_error_patterns() or {})
    except Exception as exc:
        err = str(exc)
        logger.debug("errors endpoint: %s", exc)
    return {
        "recent": recent,
        "patterns": patterns,
        "total_recent": len(recent),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }

@router.get("/anomalies")
def recent_anomalies(limit: int = Query(20, ge=1, le=200)):
    """Recent statistical anomaly alerts from the detector."""
    alerts: list[dict] = []
    err: str | None = None
    try:
        from app.anomaly_detector import get_recent_alerts
        alerts = list(get_recent_alerts(limit) or [])
    except Exception as exc:
        err = str(exc)
        logger.debug("anomalies endpoint: %s", exc)
    return {
        "recent_alerts": alerts,
        "total": len(alerts),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }

@router.get("/deploys")
def recent_deploys(limit: int = Query(20, ge=1, le=200)):
    """Recent entries from the self-deploy pipeline log."""
    from pathlib import Path as _Path
    import json as _json
    entries: list[dict] = []
    err: str | None = None
    try:
        path = _Path("/app/workspace/deploy_log.json")
        if path.exists():
            try:
                raw = _json.loads(path.read_text() or "[]")
                if isinstance(raw, list):
                    entries = raw[-limit:][::-1]  # newest first
            except Exception as exc:
                err = f"deploy log parse: {exc}"
    except Exception as exc:
        err = str(exc)
    auto_deploy = None
    try:
        from app.config import get_settings
        auto_deploy = bool(getattr(get_settings(), "evolution_auto_deploy", False))
    except Exception:
        pass
    return {
        "recent": entries,
        "auto_deploy_enabled": auto_deploy,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }

# ── Tech radar (scoped operational memory) ───────────────────────────────────

@router.get("/tech-radar")
def tech_radar(limit: int = Query(20, ge=1, le=100)):
    """Technology-discovery items collected during idle scans.

    Mirrors the parsing the Firestore publisher performs: memory items stored
    in ``scope_tech_radar`` follow ``[category] title: summary. Action: ...``.
    """
    import re as _re
    discoveries: list[dict] = []
    err: str | None = None
    try:
        from app.memory.scoped_memory import retrieve_operational
        items = retrieve_operational("scope_tech_radar", "technology discovery", n=limit) or []
        for item in items:
            text = item if isinstance(item, str) else str(item)
            m = _re.match(r'\[(\w+)\]\s*(.+?):\s*(.+?)(?:\.\s*Action:\s*(.+))?$', text, _re.DOTALL)
            if m:
                discoveries.append({
                    "category": m.group(1),
                    "title": m.group(2).strip(),
                    "summary": m.group(3).strip(),
                    "action": (m.group(4) or "").strip(),
                })
            else:
                discoveries.append({
                    "category": "unknown",
                    "title": text[:80],
                    "summary": text[:200],
                    "action": "",
                })
    except Exception as exc:
        err = str(exc)
        logger.debug("tech-radar endpoint: %s", exc)
    return {
        "discoveries": discoveries,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }

# ── LLM catalog, role assignments, discovery control ─────────────────────────

@router.get("/llms/catalog")
def llm_catalog():
    """Current live LLM catalog + role assignments + configured cost mode.

    Reads the runtime ``CATALOG`` dict (mutated by the catalog builder) so
    newly-discovered models appear without a service restart.
    """
    models: list[dict] = []
    err: str | None = None
    mode = "balanced"
    try:
        from app.llm_catalog import CATALOG
        for name, entry in CATALOG.items():
            data = dict(entry)
            data["name"] = name
            models.append(data)
    except Exception as exc:
        err = str(exc)
        logger.debug("llms/catalog endpoint: %s", exc)
    # Read the live runtime mode (dashboard switch / Signal command /
    # env-config startup) so the dashboard reflects what the resolver
    # is actually using. Falls back to "balanced" on any failure.
    try:
        from app.llm_mode import get_mode
        mode = get_mode() or "balanced"
    except Exception:
        pass
    role_assignments: dict[str, str] = {}
    public_roles: list[str] = []
    modes_list: list[str] = []
    try:
        from app.llm_catalog import (
            resolve_role_default,
            PUBLIC_ROLES,
            RUNTIME_MODES,
        )
        public_roles = list(PUBLIC_ROLES)
        modes_list = list(RUNTIME_MODES)
        for role in public_roles:
            try:
                resolved = resolve_role_default(role, mode)
                if resolved:
                    role_assignments[role] = resolved
            except Exception:
                continue
    except Exception:
        pass
    return {
        "models": models,
        "role_assignments": role_assignments,
        # ``mode`` is the canonical unified axis. ``cost_mode`` is kept
        # as an alias in the payload for one release so legacy clients
        # keep working; migrate readers to ``mode``.
        "mode": mode,
        "cost_mode": mode,
        "roles": public_roles,     # single source of truth for the UI pin dialog
        "modes": modes_list,
        "cost_modes": modes_list,  # alias for legacy clients
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }

@router.get("/llms/roles")
def llm_role_assignments_endpoint():
    """Explicit role → model assignments stored in PostgreSQL overrides table."""
    rows: list[dict] = []
    err: str | None = None
    try:
        from app.llm_role_assignments import list_assignments
        rows = list(list_assignments(active_only=True) or [])
    except Exception as exc:
        err = str(exc)
        logger.debug("llms/roles endpoint: %s", exc)
    return {
        "assignments": rows,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }

@router.get("/llms/discovery")
def llm_discovery_status(limit: int = Query(50, ge=1, le=500)):
    """Recently-discovered models + their benchmarking/promotion status."""
    from app.control_plane.db import execute
    models: list[dict] = []
    err: str | None = None
    try:
        rows = execute(
            """SELECT model_id, provider, display_name, context_window,
                      cost_input_per_m, cost_output_per_m, multimodal, tool_calling,
                      benchmark_score, benchmark_role, per_role_scores,
                      status, promoted_tier, promoted_roles,
                      created_at, updated_at, promoted_at
               FROM control_plane.discovered_models
               ORDER BY COALESCE(updated_at, created_at) DESC
               LIMIT %s""",
            (limit,),
            fetch=True,
        ) or []
        models = rows
    except Exception as exc:
        err = str(exc)
        logger.debug("llms/discovery status: %s", exc)
    return {
        "discovered": models,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": err,
    }

class DiscoveryRun(BaseModel):
    max_benchmarks: int = 3

@router.post("/llms/discovery/run")
def llm_discovery_run(body: DiscoveryRun):
    """Trigger a discovery cycle synchronously. Returns summary counts."""
    try:
        from app.llm_discovery import run_discovery_cycle
        result = run_discovery_cycle(max_benchmarks=max(1, min(body.max_benchmarks, 10)))
        return {"status": "ok", "result": result}
    except Exception as exc:
        logger.warning("llms/discovery/run failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Promotions (layer 2 of the resolver's authority cake) ────────────

class PromoteRequest(BaseModel):
    model: str
    reason: str = ""

@router.get("/llms/promotions")
def llm_promotions_endpoint():
    """List currently-promoted models (global boost)."""
    try:
        from app.llm_promotions import list_promotions_with_detail
        return {
            "promotions": list_promotions_with_detail(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.debug("llms/promotions endpoint: %s", exc)
        return {"promotions": [], "error": str(exc)}

@router.post("/llms/promote")
def llm_promote_endpoint(body: PromoteRequest):
    """Promote a catalog model — becomes resolver's first choice where it fits."""
    try:
        from app.llm_promotions import promote
        ok = promote(
            body.model,
            promoted_by="user:dashboard",
            reason=body.reason or "dashboard promotion",
        )
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=f"model {body.model!r} not in live CATALOG",
            )
        return {"status": "ok", "model": body.model}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("llms/promote failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

class DemoteRequest(BaseModel):
    model: str

@router.post("/llms/demote")
def llm_demote_endpoint(body: DemoteRequest):
    """Remove a promotion. Model returns to the regular scored pool."""
    try:
        from app.llm_promotions import demote
        demote(body.model)
        return {"status": "ok", "model": body.model}
    except Exception as exc:
        logger.warning("llms/demote failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Hand pins (layer 3 — hard override) ───────────────────────────────

class PinRequest(BaseModel):
    """Hand-pin request body.

    Clients should send ``mode`` (the unified runtime-mode axis).
    ``cost_mode`` is accepted as a legacy alias; if both are present,
    ``mode`` wins.
    """
    role: str
    mode: str | None = None
    cost_mode: str | None = None  # legacy alias
    model: str
    reason: str = ""

    def resolved_mode(self) -> str:
        return (self.mode or self.cost_mode or "balanced")

@router.get("/llms/pins")
def llm_pins_endpoint():
    """List currently-active hand pins."""
    try:
        from app.llm_role_assignments import list_pins
        return {
            "pins": list_pins(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.debug("llms/pins endpoint: %s", exc)
        return {"pins": [], "error": str(exc)}

@router.post("/llms/pin")
def llm_pin_endpoint(body: PinRequest):
    """Hand-pin a model to (role, mode) — hard resolver override."""
    try:
        from app.llm_role_assignments import pin_role
        mode = body.resolved_mode()
        ok = pin_role(
            body.role, mode, body.model,
            assigned_by="user:dashboard",
            reason=body.reason or "dashboard pin",
        )
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=f"pin rejected — {body.model!r} not in live CATALOG",
            )
        return {"status": "ok", "role": body.role,
                "mode": mode, "cost_mode": mode,  # alias for legacy clients
                "model": body.model}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("llms/pin failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

class UnpinRequest(BaseModel):
    role: str
    mode: str | None = None
    cost_mode: str | None = None  # legacy alias

    def resolved_mode(self) -> str:
        return (self.mode or self.cost_mode or "balanced")

@router.post("/llms/unpin")
def llm_unpin_endpoint(body: UnpinRequest):
    """Remove hand pins for (role, mode). Resolver takes back over."""
    try:
        from app.llm_role_assignments import unpin_role
        mode = body.resolved_mode()
        n = unpin_role(body.role, mode)
        return {"status": "ok", "retired": n,
                "role": body.role, "mode": mode, "cost_mode": mode}
    except Exception as exc:
        logger.warning("llms/unpin failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

# ── Crew tasks (live execution + roster) ─────────────────────────────────────

# Canonical crew roster. "kind" distinguishes user-addressable crews (routed
# via natural-language dispatch) from internal crews (orchestration, quality
# review, reflection, self-learning). This list is the single source of truth
# used to backfill the /tasks response so every crew is always visible in the
# dashboard even when Firestore hasn't seen it yet.
_KNOWN_CREWS: tuple[tuple[str, str], ...] = (
    # User-addressable (11)
    ("research",       "user"),
    ("coding",         "user"),
    ("writing",        "user"),
    ("media",          "user"),
    ("creative",       "user"),
    ("pim",            "user"),
    ("financial",      "user"),
    ("desktop",        "user"),
    ("repo_analysis",  "user"),
    ("devops",         "user"),
    ("tech_radar",     "user"),
    # Internal (4)
    ("commander",        "internal"),
    ("critic",           "internal"),
    ("retrospective",    "internal"),
    ("self_improvement", "internal"),
)

@router.get("/tasks")
def get_crew_tasks(limit: int = Query(20, ge=1, le=200), project_id: str | None = Query(None)):
    """Return recent crew tasks + crew statuses + full agent roster.

    - `tasks`  — last N Firestore task documents (state=running or completed)
    - `crews`  — every known crew merged with Firestore status (never missing
                 from the list even if Firebase is unavailable)
    - `agents` — the complete PostgreSQL org-chart roster so every agent/
                 subagent is represented even when idle
    """
    tasks: list[dict] = []
    crews: list[dict] = []
    firebase_error: str | None = None

    try:
        from app.firebase.infra import _get_db
        db = _get_db()
        if db:
            try:
                from google.cloud import firestore as _fs
                # When filtering, over-fetch so post-filter page size approaches
                # the requested limit even if older untagged docs are mixed in.
                fetch_limit = limit * 4 if project_id else limit
                for d in (
                    db.collection("tasks")
                      .order_by("started_at", direction=_fs.Query.DESCENDING)
                      .limit(fetch_limit)
                      .stream()
                ):
                    data = d.to_dict() or {}
                    data["id"] = d.id
                    if project_id and data.get("project_id") != project_id:
                        continue
                    tasks.append(data)
                    if len(tasks) >= limit:
                        break
            except Exception as exc:
                firebase_error = f"tasks read: {exc}"
                logger.debug("tasks endpoint: %s", firebase_error)

            try:
                for d in db.collection("crews").stream():
                    data = d.to_dict() or {}
                    data["name"] = d.id
                    crews.append(data)
            except Exception as exc:
                if not firebase_error:
                    firebase_error = f"crews read: {exc}"
                logger.debug("tasks endpoint crews: %s", exc)
        else:
            firebase_error = "Firestore unavailable"
    except Exception as exc:
        firebase_error = str(exc)
        logger.debug("tasks endpoint: firebase infra import failed: %s", exc)

    # Ensure every known crew appears in the list even if Firestore missed it.
    # Each crew carries a "kind" tag so the dashboard can group user-addressable
    # crews separately from internal orchestration crews.
    known_kinds = {name: kind for name, kind in _KNOWN_CREWS}
    seen_crews = {c.get("name") for c in crews}
    for c in crews:
        name = c.get("name")
        if name in known_kinds and "kind" not in c:
            c["kind"] = known_kinds[name]
    for name, kind in _KNOWN_CREWS:
        if name not in seen_crews:
            crews.append({"name": name, "state": "unknown", "kind": kind})

    agents: list[dict] = []
    try:
        from app.control_plane.org_chart import get_org_chart
        agents = get_org_chart() or []
    except Exception as exc:
        logger.debug("tasks endpoint: org_chart read failed: %s", exc)

    return {
        "tasks": tasks,
        "crews": crews,
        "agents": agents,
        "project_id": project_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "error": firebase_error,
    }

# ── Token usage & cost projection ────────────────────────────────────────────

_TOKEN_PERIODS = ("hour", "day", "week", "month", "year")

@router.get("/tokens")
def token_usage(project_id: str | None = Query(None)):
    """Aggregated token usage, request-level cost stats, and a simple monthly
    projection. Mirrors the payload the legacy dashboard consumed from the
    Firestore `status/tokens` + `status/request_costs` documents.

    When ``project_id`` is supplied, only rows tagged with that project are
    aggregated. Rows recorded before the per-project tagging migration landed
    have a NULL ``project_id`` and are excluded from filtered responses.
    """
    try:
        from app.llm_benchmarks import (
            get_token_stats,
            get_request_cost_stats,
            get_crew_cost_stats,
        )
    except Exception as exc:
        logger.debug("tokens endpoint: llm_benchmarks import failed: %s", exc)
        return {
            "stats": {p: [] for p in _TOKEN_PERIODS},
            "request_costs": {p: {} for p in ("day", "week", "month")},
            "by_crew": {"day": []},
            "projection": {"day_cost_usd": 0.0, "mtd_cost_usd": 0.0, "projected_monthly_usd": 0.0},
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }

    stats = {p: get_token_stats(p, project_id=project_id) for p in _TOKEN_PERIODS}
    request_costs = {
        p: get_request_cost_stats(p, project_id=project_id) for p in ("day", "week", "month")
    }

    day_cost = sum(float(r.get("cost_usd") or 0) for r in stats.get("day", []))
    month_cost = sum(float(r.get("cost_usd") or 0) for r in stats.get("month", []))

    return {
        "stats": stats,
        "request_costs": request_costs,
        "by_crew": {"day": get_crew_cost_stats("day", project_id=project_id)},
        "projection": {
            "day_cost_usd": round(day_cost, 6),
            "mtd_cost_usd": round(month_cost, 6),
            "projected_monthly_usd": round(day_cost * 30, 4),
        },
        "project_id": project_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Consciousness Indicators (Garland / Butlin-Chalmers) ─────────────────────

@router.get("/consciousness")
def consciousness_indicators(history_limit: int = Query(30, ge=1, le=200)):
    """Latest consciousness-probe report + historical timeline.

    Shape matches the legacy Firestore document the old HTML dashboard consumed
    (status/consciousness_probes): { latest, history, updated_at }.
    Reads from the `internal_states` table where the probe runner persists its
    output (agent_id='consciousness_probe').
    """
    from app.control_plane.db import execute
    try:
        rows = execute(
            """
            SELECT full_state, created_at
            FROM internal_states
            WHERE agent_id = 'consciousness_probe'
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (history_limit,),
            fetch=True,
        ) or []
    except Exception as exc:
        logger.debug("consciousness endpoint: DB read failed: %s", exc)
        return {"latest": {}, "history": [], "updated_at": None, "error": str(exc)}

    history: list[dict] = []
    latest: dict = {}
    for row in rows:
        fs = row.get("full_state") if isinstance(row, dict) else row[0]
        ts = row.get("created_at") if isinstance(row, dict) else row[1]
        if isinstance(fs, str):
            try:
                fs = json.loads(fs)
            except Exception:
                continue
        if not isinstance(fs, dict) or "composite_score" not in fs:
            continue
        entry = {
            "score": fs.get("composite_score"),
            "timestamp": str(ts),
            "probes": fs.get("probes", []),
        }
        history.append(entry)
        if not latest:
            latest = {
                "report_id": fs.get("report_id", ""),
                "timestamp": str(ts),
                "probes": fs.get("probes", []),
                "composite_score": fs.get("composite_score"),
                "summary": fs.get("summary", ""),
            }

    return {
        "latest": latest,
        "history": history,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

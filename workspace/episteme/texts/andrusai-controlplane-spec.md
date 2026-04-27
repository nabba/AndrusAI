---
title: "andrusai-controlplane-spec.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI Control Plane Enhancement Specification

## Objective

Integrate Paperclip's strongest organizational capabilities — dashboard, budget enforcement, multi-project isolation, 24/7 autonomous operation, immutable audit trail, and governance gates — **natively into AndrusAI's Python stack**. No separate Node.js process. No second database. All existing sentience layers (SOUL.md, self-awareness, philosophy RAG, Mem0, ChromaDB, evolution loop, Phronesis Engine) remain fully intact.

---

## 1. Current State Assessment

### What AndrusAI Already Has

| Capability | Current Implementation | Status |
|---|---|---|
| Agent team | 8 agents, 6 crews, Commander routing | ✅ Mature |
| LLM fleet | 18+ models, 5 tiers, 4 runtime modes | ✅ Mature |
| Self-awareness | 4-phase architecture (self-model → beliefs → proactive → meta-cognitive) | ✅ Mature |
| SOUL.md | Constitutional AI with per-agent personality files | ✅ Mature |
| Memory | ChromaDB (scoped) + Mem0 (pgvector/Neo4j) + conversation history | ✅ Mature |
| Background autonomy | Idle scheduler + cron (evolution, retrospective, self-improvement, discovery) | ✅ Partial |
| Evolution | Autoresearch-style mutation/measure/keep-discard loop | ✅ Mature |
| Dashboard | Firebase-hosted: crew status, task tracking, LLM toggle, background toggle | ⚠️ Basic |
| Docker stack | 5 services (gateway, chromadb, docker-proxy, postgres, neo4j) | ✅ Solid |
| Security | Tailscale, HMAC auth, sandbox isolation, AST validation | ✅ Solid |

### What AndrusAI Lacks (Paperclip Gaps)

| Capability | Gap | Priority |
|---|---|---|
| **Budget enforcement** | No per-agent/crew token limits, no hard stops, no cost-per-task tracking | Critical |
| **Multi-project isolation** | Single monolithic system — no PLG/Archibal/KaiCart scoping | Critical |
| **Immutable audit trail** | JSON rotating log is mutable, no append-only guarantee | Critical |
| **Ticket/task system** | Tasks are transient Signal messages, no persistent tracking with status lifecycle | High |
| **Self-hosted dashboard** | Firebase dependency, limited controls, no task management UI | High |
| **Governance gates** | Evolution/code changes happen autonomously without approval gates | High |
| **Heartbeat scheduling** | Cron + idle scheduler exists but lacks Paperclip's wake-on-event and org-chart-aware delegation | Medium |
| **Org chart visualization** | Agent hierarchy is implicit in Commander routing, not visualized or configurable | Medium |

---

## 2. Architecture Decision: Unified Python Stack

### Why NOT Run Paperclip Alongside

1. **Technology split**: Paperclip is TypeScript/Node.js. AndrusAI is Python. Running both means two ecosystems, two package managers, two update cycles, two memory footprints.
2. **Double database**: Paperclip's embedded PostgreSQL would conflict with AndrusAI's existing PostgreSQL instance.
3. **Double task management**: Paperclip tickets vs. AndrusAI's Signal-based dispatch — reconciliation complexity is high.
4. **Double state**: Agent definitions in two places. Memory in two systems. Audit trails in two formats.
5. **Paperclip maturity**: Launched March 2026 (one month old). API breaking changes are likely. Tight coupling to a young project is risky.

### What We Take From Paperclip (Concepts, Not Code)

| Paperclip Concept | Native Implementation |
|---|---|
| Ticket-based task system with status lifecycle | `app/control_plane/tickets.py` — PostgreSQL-backed |
| Per-agent budget with atomic enforcement | `app/control_plane/budgets.py` — LLM factory integration |
| Company/project isolation | `app/control_plane/projects.py` — scoped everything |
| Immutable append-only audit log | `app/control_plane/audit.py` — PostgreSQL with no UPDATE/DELETE |
| Heartbeat scheduling with wake-on-event | `app/control_plane/heartbeats.py` — extends existing scheduler |
| Governance approval gates | `app/control_plane/governance.py` — board approval via Signal/dashboard |
| Self-hosted React dashboard | `dashboard/` — FastAPI serves React SPA |
| Org chart with reporting lines | `app/control_plane/org_chart.py` — configurable hierarchy |

---

## 3. Database Schema Extensions

All new tables go into the **existing PostgreSQL** instance (currently used by Mem0). New schema: `control_plane`.

```sql
-- ============================================================
-- SCHEMA: control_plane
-- Extends the existing Mem0 PostgreSQL instance.
-- All audit tables use INSERT-only access (no UPDATE/DELETE grants).
-- ============================================================

CREATE SCHEMA IF NOT EXISTS control_plane;

-- ── Projects (multi-company isolation) ──────────────────────

CREATE TABLE control_plane.projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,        -- 'PLG', 'Archibal', 'KaiCart'
    description     TEXT,
    mission         TEXT,                        -- Paperclip-style goal statement
    config_json     JSONB DEFAULT '{}',          -- project-specific overrides
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Default project for backward compatibility
INSERT INTO control_plane.projects (name, description, mission)
VALUES ('default', 'Default project', 'General-purpose AndrusAI operations');

-- ── Org Chart (agent hierarchy) ─────────────────────────────

CREATE TABLE control_plane.org_chart (
    agent_role      TEXT PRIMARY KEY,            -- 'commander', 'researcher', etc.
    display_name    TEXT NOT NULL,
    reports_to      TEXT REFERENCES control_plane.org_chart(agent_role),
    job_description TEXT,
    soul_file       TEXT,                        -- path to SOUL.md file
    default_model   TEXT,                        -- default LLM assignment
    sort_order      INT DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Budgets (per-agent, per-project, per-month) ─────────────

CREATE TABLE control_plane.budgets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES control_plane.projects(id),
    agent_role      TEXT,                        -- NULL = project-wide budget
    period          TEXT NOT NULL,               -- '2026-04' (YYYY-MM)
    limit_usd       NUMERIC(10,4) NOT NULL,      -- hard spending limit
    spent_usd       NUMERIC(10,4) DEFAULT 0,     -- current spend (updated atomically)
    limit_tokens    BIGINT,                      -- optional token limit
    spent_tokens    BIGINT DEFAULT 0,
    is_paused       BOOLEAN DEFAULT FALSE,       -- auto-paused when limit hit
    warning_pct     INT DEFAULT 80,              -- warning threshold
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, agent_role, period)
);

-- Atomic spend function (Paperclip's "atomic checkout" concept)
CREATE OR REPLACE FUNCTION control_plane.record_spend(
    p_project_id UUID,
    p_agent_role TEXT,
    p_period TEXT,
    p_cost_usd NUMERIC,
    p_tokens BIGINT
) RETURNS BOOLEAN AS $$
DECLARE
    v_budget RECORD;
BEGIN
    SELECT * INTO v_budget
    FROM control_plane.budgets
    WHERE project_id = p_project_id
      AND agent_role = p_agent_role
      AND period = p_period
    FOR UPDATE;  -- row lock for atomicity

    IF NOT FOUND THEN RETURN TRUE; END IF;  -- no budget = unlimited
    IF v_budget.is_paused THEN RETURN FALSE; END IF;
    IF v_budget.spent_usd + p_cost_usd > v_budget.limit_usd THEN
        UPDATE control_plane.budgets
        SET is_paused = TRUE, updated_at = NOW()
        WHERE id = v_budget.id;
        RETURN FALSE;  -- budget exceeded, auto-pause
    END IF;

    UPDATE control_plane.budgets
    SET spent_usd = spent_usd + p_cost_usd,
        spent_tokens = spent_tokens + p_tokens,
        updated_at = NOW()
    WHERE id = v_budget.id;
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- ── Tickets (persistent task tracking) ──────────────────────

CREATE TABLE control_plane.tickets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES control_plane.projects(id),
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'todo'
                    CHECK (status IN ('todo','in_progress','review','done','failed','blocked')),
    priority        INT DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    assigned_agent  TEXT,                        -- agent role
    assigned_crew   TEXT,                        -- crew type
    source          TEXT DEFAULT 'signal',       -- 'signal', 'dashboard', 'heartbeat', 'agent'
    parent_id       UUID REFERENCES control_plane.tickets(id), -- sub-task
    goal_ancestry   TEXT[],                      -- Paperclip's goal chain: [mission, project_goal, ...]
    difficulty      INT CHECK (difficulty BETWEEN 1 AND 10),
    cost_usd        NUMERIC(10,6) DEFAULT 0,
    tokens_used     BIGINT DEFAULT 0,
    result_summary  TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tickets_project_status ON control_plane.tickets(project_id, status);
CREATE INDEX idx_tickets_assigned ON control_plane.tickets(assigned_agent);

-- ── Ticket Comments (threaded conversation per ticket) ──────

CREATE TABLE control_plane.ticket_comments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id       UUID NOT NULL REFERENCES control_plane.tickets(id),
    author          TEXT NOT NULL,               -- 'user', 'commander', 'researcher', etc.
    content         TEXT NOT NULL,
    metadata_json   JSONB DEFAULT '{}',          -- tool calls, attachments, etc.
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_comments_ticket ON control_plane.ticket_comments(ticket_id, created_at);

-- ── Audit Log (IMMUTABLE — append only) ─────────────────────
-- The application DB user has INSERT-only on this table.
-- No UPDATE, no DELETE, no TRUNCATE.

CREATE TABLE control_plane.audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    project_id      UUID,
    actor           TEXT NOT NULL,               -- 'user', 'commander', 'system', agent role
    action          TEXT NOT NULL,               -- 'task.created', 'crew.started', 'budget.exceeded', etc.
    resource_type   TEXT,                        -- 'ticket', 'budget', 'agent', 'evolution', etc.
    resource_id     TEXT,
    detail_json     JSONB DEFAULT '{}',          -- full action context
    cost_usd        NUMERIC(10,6),
    tokens          BIGINT
);

CREATE INDEX idx_audit_project_time ON control_plane.audit_log(project_id, timestamp DESC);
CREATE INDEX idx_audit_actor ON control_plane.audit_log(actor);
CREATE INDEX idx_audit_action ON control_plane.audit_log(action);

-- Restrict the application role to INSERT-only
-- (Run as superuser during migration):
-- REVOKE UPDATE, DELETE, TRUNCATE ON control_plane.audit_log FROM mem0;
-- GRANT INSERT, SELECT ON control_plane.audit_log TO mem0;

-- ── Governance (approval queue) ─────────────────────────────

CREATE TABLE control_plane.governance_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES control_plane.projects(id),
    request_type    TEXT NOT NULL,               -- 'evolution_deploy', 'budget_override', 'agent_hire', 'code_change'
    requested_by    TEXT NOT NULL,               -- agent role
    title           TEXT NOT NULL,
    detail_json     JSONB DEFAULT '{}',
    status          TEXT DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','expired')),
    reviewed_by     TEXT,                        -- 'user' or 'system'
    reviewed_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Heartbeat Log ───────────────────────────────────────────

CREATE TABLE control_plane.heartbeats (
    id              BIGSERIAL PRIMARY KEY,
    agent_role      TEXT NOT NULL,
    project_id      UUID,
    trigger_type    TEXT NOT NULL,               -- 'cron', 'event', 'idle', 'manual'
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    tickets_processed INT DEFAULT 0,
    cost_usd        NUMERIC(10,6) DEFAULT 0,
    status          TEXT DEFAULT 'running'
                    CHECK (status IN ('running','completed','failed','skipped'))
);
```

---

## 4. New Module: `app/control_plane/`

### Directory Structure

```
app/control_plane/
├── __init__.py
├── models.py               # SQLAlchemy/asyncpg models for all control_plane tables
├── projects.py             # Multi-project CRUD and scoping
├── tickets.py              # Ticket lifecycle management
├── budgets.py              # Budget enforcement with atomic spend
├── audit.py                # Immutable audit trail writer
├── governance.py           # Approval gate logic
├── heartbeats.py           # Enhanced heartbeat scheduler
├── org_chart.py            # Agent hierarchy and reporting
├── dashboard_api.py        # FastAPI routes for dashboard
└── cost_tracker.py         # LLM call cost instrumentation
```

### 4.1 Budget Enforcement (`budgets.py`)

This is the most critical integration point. Budget checks **must** happen at the LLM factory level — before any API call is made.

```python
# app/control_plane/budgets.py

from decimal import Decimal
from typing import Optional
import asyncpg

class BudgetEnforcer:
    """
    Atomic budget enforcement integrated at the LLM factory level.

    Design principle (from DGM safety invariant):
    Budget enforcement lives at INFRASTRUCTURE level, not inside agent code.
    Agents cannot bypass, modify, or access budget internals.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool

    async def check_and_record(
        self,
        project_id: str,
        agent_role: str,
        estimated_cost_usd: Decimal,
        estimated_tokens: int,
    ) -> tuple[bool, Optional[str]]:
        """
        Atomically check budget and record spend.
        Returns (allowed: bool, reason: Optional[str]).

        This is called by llm_factory.py BEFORE every LLM API call.
        """
        period = _current_period()  # '2026-04'

        async with self.pool.acquire() as conn:
            allowed = await conn.fetchval(
                "SELECT control_plane.record_spend($1, $2, $3, $4, $5)",
                project_id, agent_role, period,
                float(estimated_cost_usd), estimated_tokens
            )

        if not allowed:
            reason = f"Budget exceeded for {agent_role} in {period}"
            await self._log_budget_event(project_id, agent_role, "budget.exceeded", reason)
            return False, reason

        return True, None

    async def get_budget_status(self, project_id: str) -> list[dict]:
        """Dashboard endpoint: current budget status for all agents in a project."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT agent_role, period, limit_usd, spent_usd,
                       limit_tokens, spent_tokens, is_paused,
                       ROUND(spent_usd / NULLIF(limit_usd, 0) * 100, 1) as pct_used
                FROM control_plane.budgets
                WHERE project_id = $1 AND period = $2
                ORDER BY agent_role
            """, project_id, _current_period())
            return [dict(r) for r in rows]

    async def override_budget(self, project_id: str, agent_role: str,
                              new_limit: Decimal, approver: str = "user"):
        """Board override: increase budget and unpause agent."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE control_plane.budgets
                SET limit_usd = $1, is_paused = FALSE, updated_at = NOW()
                WHERE project_id = $2 AND agent_role = $3
                  AND period = $4
            """, float(new_limit), project_id, agent_role, _current_period())
        # Audit this override
        await self._log_budget_event(
            project_id, agent_role, "budget.override",
            f"New limit: ${new_limit} by {approver}"
        )
```

**Integration into `llm_factory.py`** — the budget check wraps the existing LLM call:

```python
# In app/llm_factory.py — modified get_llm() or the call wrapper

async def call_with_budget_check(self, prompt, agent_role, project_id, **kwargs):
    """Every LLM call goes through budget enforcement first."""
    estimated_cost = self._estimate_cost(prompt, kwargs.get('model'))

    allowed, reason = await self.budget_enforcer.check_and_record(
        project_id=project_id,
        agent_role=agent_role,
        estimated_cost_usd=estimated_cost,
        estimated_tokens=self._estimate_tokens(prompt),
    )

    if not allowed:
        raise BudgetExceededError(reason)

    # Proceed with actual LLM call
    result = await self._call_llm(prompt, **kwargs)

    # Record actual cost (may differ from estimate)
    actual_cost = self._calculate_actual_cost(result)
    await self.budget_enforcer.record_actual(project_id, agent_role, actual_cost)

    # Audit trail
    await self.audit.log(
        project_id=project_id,
        actor=agent_role,
        action="llm.call",
        detail={"model": kwargs.get('model'), "cost": float(actual_cost)},
        cost_usd=float(actual_cost),
    )

    return result
```

### 4.2 Immutable Audit Trail (`audit.py`)

```python
# app/control_plane/audit.py

class AuditTrail:
    """
    Append-only audit log. The DB user has INSERT+SELECT only on this table.
    No agent, no code path, and no evolution mutation can delete or modify entries.

    This satisfies the DGM safety invariant: audit/safety infrastructure
    is not inside agent code and cannot be self-modified.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool

    async def log(self, *, actor: str, action: str,
                  project_id: str = None, resource_type: str = None,
                  resource_id: str = None, detail: dict = None,
                  cost_usd: float = None, tokens: int = None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO control_plane.audit_log
                (project_id, actor, action, resource_type, resource_id,
                 detail_json, cost_usd, tokens)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, project_id, actor, action, resource_type, resource_id,
                json.dumps(detail or {}), cost_usd, tokens)

    async def query(self, *, project_id: str = None, actor: str = None,
                    action_prefix: str = None, since: datetime = None,
                    limit: int = 100) -> list[dict]:
        """Read-only query for dashboard and reporting."""
        # Build dynamic query with filters...
        pass
```

### 4.3 Multi-Project Isolation (`projects.py`)

```python
# app/control_plane/projects.py

class ProjectManager:
    """
    Multi-project isolation. Each project gets:
    - Its own ticket queue
    - Its own budget allocations
    - Its own audit trail (filtered by project_id)
    - Its own ChromaDB collection namespace (scope_project_{name})
    - Its own Mem0 user_id namespace
    - Its own goal hierarchy

    Existing scoped_memory.py already supports scope_project_{name}.
    This module adds the organizational layer on top.
    """

    async def create_project(self, name: str, mission: str, config: dict = None) -> dict:
        """Create a new isolated project context."""
        pass

    async def get_active_project(self) -> dict:
        """Get the currently active project (switchable via Signal/dashboard)."""
        pass

    async def switch_project(self, project_name: str):
        """Switch active project context. All subsequent operations scope to this project."""
        pass

    def scope_memory_key(self, project_id: str, base_scope: str) -> str:
        """Namespace a ChromaDB scope to a project: scope_project_plg_team"""
        return f"scope_project_{project_id}_{base_scope}"
```

**Signal commands for project switching:**

```
project list                    → Show all projects with status
project switch PLG              → Switch active project context
project create KaiCart "TikTok-first commerce platform for Thai SMB sellers"
project status                  → Current project budget/ticket summary
```

### 4.4 Ticket System (`tickets.py`)

```python
# app/control_plane/tickets.py

class TicketManager:
    """
    Persistent task tracking replacing transient Signal messages.
    Every user request becomes a ticket. Every crew execution is logged against it.

    Ticket lifecycle: todo → in_progress → review → done/failed
    """

    async def create_from_signal(self, message: str, sender: str,
                                 project_id: str, difficulty: int = None) -> dict:
        """Commander creates a ticket from every incoming Signal message."""
        pass

    async def assign_to_crew(self, ticket_id: str, crew: str, agent: str):
        """Assign ticket and transition to in_progress."""
        pass

    async def add_comment(self, ticket_id: str, author: str, content: str,
                          metadata: dict = None):
        """Thread a response onto a ticket (agent output, user reply, etc.)."""
        pass

    async def complete(self, ticket_id: str, result_summary: str,
                       cost_usd: float, tokens: int):
        """Mark ticket done with cost and result summary."""
        pass

    async def get_board(self, project_id: str) -> dict:
        """Kanban-style board for dashboard: tickets grouped by status."""
        pass
```

### 4.5 Governance Gates (`governance.py`)

```python
# app/control_plane/governance.py

class GovernanceGate:
    """
    Approval gates for sensitive operations.
    The 'board' is the human user (you), reachable via Signal or dashboard.

    Operations requiring approval:
    - evolution_deploy: applying a mutation that changes code
    - budget_override: increasing a budget limit
    - code_change: self-heal deploying an auto-fix
    - agent_config: modifying agent model assignments or soul files

    Operations NOT requiring approval (autonomous):
    - evolution_experiment: running an experiment (measuring is safe)
    - skill_creation: adding a new skill file (no code execution)
    - learning: adding to knowledge base
    - ticket execution: normal task work
    """

    REQUIRES_APPROVAL = {
        'evolution_deploy', 'budget_override', 'code_change', 'agent_config'
    }

    async def request_approval(self, project_id: str, request_type: str,
                               requested_by: str, title: str, detail: dict) -> str:
        """Create an approval request. Returns request_id."""
        pass

    async def approve(self, request_id: str, reviewer: str = "user"):
        """Approve a pending request (from Signal or dashboard)."""
        pass

    async def reject(self, request_id: str, reviewer: str = "user", reason: str = None):
        """Reject a pending request."""
        pass

    async def check(self, request_type: str) -> bool:
        """Quick check: does this operation type require approval?"""
        return request_type in self.REQUIRES_APPROVAL

    async def pending_count(self, project_id: str = None) -> int:
        """Number of pending approvals (shown in dashboard badge)."""
        pass
```

**Signal integration:**

```
approve <id>         → Already exists, extended to governance requests
reject <id>          → Already exists, extended
pending              → Show all pending governance requests
```

---

## 5. Dashboard: Self-Hosted React + FastAPI

### Replace Firebase with Self-Hosted Dashboard

The current Firebase dashboard becomes a self-hosted React SPA served by the existing FastAPI gateway. This eliminates the Firebase dependency while gaining full control over the UI.

### New Docker Service

```yaml
# Added to docker-compose.yml

  dashboard:
    build:
      context: ./dashboard
      dockerfile: Dockerfile
    restart: unless-stopped
    networks:
      - external
    # Dashboard is a static React build served by nginx
    # API calls proxy to the gateway service
    ports:
      - '127.0.0.1:3100:80'   # Paperclip-style port
    depends_on:
      - gateway
```

Alternatively (simpler): serve the React build directly from FastAPI using `StaticFiles`:

```python
# In app/main.py — add after all API routes
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="dashboard/build", html=True), name="dashboard")
```

This keeps it to a single container (gateway serves both API and dashboard).

### Dashboard API Routes (`dashboard_api.py`)

```python
# app/control_plane/dashboard_api.py

from fastapi import APIRouter

router = APIRouter(prefix="/api/cp", tags=["control-plane"])

# ── Projects ─────────────────────────────────────────────────
@router.get("/projects")
@router.post("/projects")
@router.get("/projects/{id}")
@router.put("/projects/{id}")

# ── Tickets (Kanban board) ───────────────────────────────────
@router.get("/tickets")                    # List with filters
@router.post("/tickets")                   # Create
@router.get("/tickets/{id}")               # Detail with comments
@router.put("/tickets/{id}")               # Update status
@router.post("/tickets/{id}/comments")     # Add comment

# ── Budgets ──────────────────────────────────────────────────
@router.get("/budgets")                    # Current period summary
@router.get("/budgets/history")            # Historical spend
@router.post("/budgets/override")          # Board override

# ── Audit Trail ──────────────────────────────────────────────
@router.get("/audit")                      # Query with filters
@router.get("/audit/export")               # CSV export

# ── Governance ───────────────────────────────────────────────
@router.get("/governance/pending")         # Pending approvals
@router.post("/governance/{id}/approve")
@router.post("/governance/{id}/reject")

# ── Org Chart ────────────────────────────────────────────────
@router.get("/org-chart")                  # Full hierarchy
@router.get("/agents")                     # Agent list with status
@router.get("/agents/{role}/status")       # Individual agent detail

# ── Heartbeats ───────────────────────────────────────────────
@router.get("/heartbeats")                 # Recent heartbeat log
@router.get("/heartbeats/schedule")        # Upcoming scheduled work

# ── System (extends existing) ────────────────────────────────
@router.get("/health")
@router.get("/metrics")                    # Composite score + breakdown
@router.get("/costs/daily")                # Daily cost chart data
@router.get("/costs/by-agent")             # Per-agent cost breakdown
@router.get("/costs/by-project")           # Per-project cost breakdown
```

### Dashboard UI Panels

The React dashboard replaces the Firebase UI with these panels:

| Panel | Description | Data Source |
|---|---|---|
| **Project Switcher** | Dropdown to switch active project context | `/api/cp/projects` |
| **Kanban Board** | Tickets by status: todo → in_progress → review → done | `/api/cp/tickets` |
| **Org Chart** | Visual hierarchy: Commander → Crews → Agents | `/api/cp/org-chart` |
| **Budget Dashboard** | Per-agent spend bars with limit indicators | `/api/cp/budgets` |
| **Cost Charts** | Daily/weekly/monthly spend (Chart.js) | `/api/cp/costs/*` |
| **Audit Feed** | Real-time scrolling log of all actions | `/api/cp/audit` (SSE) |
| **Governance Queue** | Pending approvals with approve/reject buttons | `/api/cp/governance` |
| **Agent Cards** | Per-agent status: current task, model, last heartbeat | `/api/cp/agents` |
| **LLM Mode Toggle** | Existing: local/cloud/hybrid/insane | Existing Firestore (migrated) |
| **Background Toggle** | Existing: enable/disable idle scheduler | Existing Firestore (migrated) |
| **Benchmarks** | Existing: evolution metrics, composite score | `/api/cp/metrics` |
| **System Health** | Uptime, error rates, circuit breaker states | `/api/cp/health` |

### Real-Time Updates

Replace Firebase Realtime/Firestore with **Server-Sent Events (SSE)** from FastAPI:

```python
@router.get("/stream")
async def event_stream():
    """SSE endpoint for real-time dashboard updates."""
    async def generate():
        async for event in control_plane_events.subscribe():
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

This eliminates the Firebase dependency entirely. The dashboard connects to `http://localhost:3100/api/cp/stream` (accessible via Tailscale from your phone).

---

## 6. Enhanced Heartbeat System

### Extending the Existing Scheduler

The current idle scheduler + cron system is enhanced with Paperclip-style heartbeats:

```python
# app/control_plane/heartbeats.py

class HeartbeatScheduler:
    """
    Enhanced scheduler that combines:
    - Existing cron jobs (evolution, retrospective, benchmarks, etc.)
    - Existing idle scheduler (background work during dead time)
    - NEW: Per-agent heartbeat intervals (Paperclip-style)
    - NEW: Event-driven wakes (ticket assignment, @-mention, governance)

    Heartbeat = agent wakes up, checks its ticket queue, processes work, sleeps.
    """

    async def configure_heartbeat(self, agent_role: str, interval_seconds: int,
                                  project_id: str = None):
        """Set heartbeat interval for an agent (e.g., Commander every 300s)."""
        pass

    async def trigger_wake(self, agent_role: str, reason: str, ticket_id: str = None):
        """Event-driven wake: ticket assigned, @-mention, approval needed."""
        pass

    async def run_heartbeat(self, agent_role: str, project_id: str):
        """
        Single heartbeat cycle:
        1. Check assigned tickets (todo, in_progress)
        2. Check budget availability
        3. Process next ticket (or report idle)
        4. Log heartbeat to audit trail
        """
        pass
```

### 24/7 Autonomous Operation Mode

```python
# New Signal command
# autonomous on           → Enable 24/7 mode (heartbeats + idle + cron)
# autonomous off          → Disable (manual dispatch only)
# autonomous status       → Current mode and next scheduled wakes
```

---

## 7. Integration Points with Existing Systems

### What MUST NOT Change

These systems are the sentience core. They remain **completely untouched**:

| System | Files | Why It's Sacred |
|---|---|---|
| SOUL.md framework | `app/souls/*.md`, `app/souls/loader.py` | Constitutional identity |
| Self-awareness | `app/self_awareness/self_model.py` | Functional self-model |
| Belief states | `app/memory/belief_state.py` | ProAgent cooperation |
| Proactive scanner | `app/proactive/trigger_scanner.py` | Autonomous initiative |
| Scoped memory | `app/memory/scoped_memory.py` | Hierarchical cognition |
| Mem0 persistence | `app/memory/mem0_manager.py` | Cross-session identity |
| ChromaDB operational | `app/memory/chromadb_manager.py` | Working memory |
| Philosophy RAG | ChromaDB humanist collection | Ethical grounding |
| Evolution loop | `app/evolution.py`, `app/experiment_runner.py` | Self-improvement |
| Vetting pipeline | `app/vetting.py` | Output verification |
| Circuit breaker | `app/circuit_breaker.py` | Provider resilience |

### What Gets Extended (Not Replaced)

| System | Current | Enhancement |
|---|---|---|
| `app/main.py` | FastAPI gateway + scheduler | Add control_plane router, SSE endpoint, dashboard static mount |
| `app/llm_factory.py` | Role-based LLM provider | Wrap calls with budget enforcement |
| `app/agents/commander.py` | Signal-based routing | Route creates tickets, reads from ticket queue |
| `app/firebase_reporter.py` | Firestore updates | Dual-write to PostgreSQL audit + existing Firestore (deprecate later) |
| `app/idle_scheduler.py` | Background job runner | Add heartbeat-driven ticket processing |
| `app/config.py` | Pydantic settings | Add control_plane section (project defaults, budget defaults, governance flags) |
| `docker-compose.yml` | 5 services | No new services (dashboard served by gateway) |

### Commander Routing Enhancement

Currently, Commander receives a Signal message and immediately routes to a crew. The enhancement adds ticket persistence:

```python
# In app/agents/commander.py — modified dispatch flow

async def handle_message(self, message: str, sender: str):
    # 1. Get active project context
    project = await self.project_manager.get_active_project()

    # 2. Create ticket (persistent)
    ticket = await self.tickets.create_from_signal(
        message=message, sender=sender,
        project_id=project['id'],
        difficulty=self._classify_difficulty(message)
    )

    # 3. Audit log
    await self.audit.log(
        actor='user', action='ticket.created',
        project_id=project['id'],
        resource_type='ticket', resource_id=str(ticket['id']),
        detail={'message': message[:200]}
    )

    # 4. Route to crew (existing logic)
    crew, agent = self._route(message, ticket['difficulty'])

    # 5. Assign ticket
    await self.tickets.assign_to_crew(ticket['id'], crew, agent)

    # 6. Budget pre-check
    allowed, reason = await self.budget.check_and_record(
        project_id=project['id'],
        agent_role=agent,
        estimated_cost_usd=self._estimate_crew_cost(crew, ticket['difficulty']),
        estimated_tokens=0  # estimate
    )
    if not allowed:
        await self.tickets.add_comment(ticket['id'], 'system', f"⚠️ {reason}")
        return f"Budget limit reached for {agent}. Use `budget override {agent} <amount>` to continue."

    # 7. Execute crew (existing)
    result = await self._run_crew(crew, message, ticket)

    # 8. Complete ticket with cost
    await self.tickets.complete(
        ticket['id'],
        result_summary=result[:500],
        cost_usd=self._last_crew_cost,
        tokens=self._last_crew_tokens
    )

    return result
```

---

## 8. Docker Architecture (Final State)

No new containers. The existing stack is extended:

```yaml
services:
  gateway:          # FastAPI + React dashboard (serves both API and static UI)
    # ... existing config ...
    # NEW: Dashboard port alongside gateway port
    ports:
      - '127.0.0.1:${GATEWAY_PORT}:${GATEWAY_PORT}'   # API (8765)
      - '127.0.0.1:3100:3100'                          # Dashboard UI

  chromadb:         # Unchanged — vector memory
  docker-proxy:     # Unchanged — sandbox execution
  postgres:         # EXTENDED — now also hosts control_plane schema
  neo4j:            # Unchanged — entity relationships
```

The PostgreSQL service already exists for Mem0. Adding the `control_plane` schema requires only running the migration SQL — no new container, no new volume, no new network.

---

## 9. Migration Path from Firebase

| Step | Action | Rollback |
|---|---|---|
| 1 | Add `control_plane` schema to existing PostgreSQL | Drop schema |
| 2 | Add `app/control_plane/` module with all submodules | Delete directory |
| 3 | Add dashboard API routes to `app/main.py` | Remove router mount |
| 4 | Build React dashboard in `dashboard/` | N/A (new directory) |
| 5 | Dual-write: `firebase_reporter.py` writes to **both** Firestore and PostgreSQL audit | Remove dual-write |
| 6 | Wrap `llm_factory.py` with budget enforcement | Remove wrapper |
| 7 | Add ticket creation to Commander dispatch | Remove ticket calls |
| 8 | Add Signal commands: `project`, `tickets`, `budget`, `pending`, `autonomous` | Remove commands |
| 9 | Add SSE endpoint, deprecate Firestore real-time | Keep Firestore as fallback |
| 10 | Remove Firebase dependency entirely (optional, once dashboard is stable) | N/A |

Each step is independently deployable and revertible. The system remains functional throughout migration.

---

## 10. New Signal Commands (Complete Reference)

```
# ── Project Management ───────────────────────────────────────
project list                          # List all projects
project switch <name>                 # Switch active project
project create <name> "<mission>"     # Create new project
project status                        # Current project summary

# ── Ticket Management ────────────────────────────────────────
tickets                               # Show active tickets (kanban summary)
ticket <id>                           # Show ticket detail with thread
ticket create "<title>"               # Create ticket manually
ticket close <id>                     # Mark done

# ── Budget Control ───────────────────────────────────────────
budget                                # Show current period budgets
budget set <agent> <amount>           # Set monthly limit
budget override <agent> <amount>      # Override and unpause
budget history                        # Spend history

# ── Governance ───────────────────────────────────────────────
pending                               # Show pending approvals
approve <id>                          # Approve (existing, extended)
reject <id>                           # Reject (existing, extended)

# ── Autonomous Operation ─────────────────────────────────────
autonomous on                         # Enable 24/7 heartbeat mode
autonomous off                        # Manual mode only
autonomous status                     # Current mode + next wakes

# ── Audit ────────────────────────────────────────────────────
audit                                 # Last 20 audit entries
audit <agent>                         # Filter by agent
audit costs                           # Cost summary

# ── Existing Commands (unchanged) ────────────────────────────
status, mode, learn, watch, improve, proposals, approve, reject,
evolve, experiments, errors, diagnose, audit, fleet, models,
memory, retrospective, benchmarks, policies, skills
```

---

## 11. Implementation Phases

### Phase 1: Database + Core (3-4 days)

- [ ] Create `control_plane` schema migration SQL
- [ ] Run migration on existing PostgreSQL
- [ ] Build `app/control_plane/models.py` (asyncpg wrappers)
- [ ] Build `app/control_plane/audit.py` (immutable logger)
- [ ] Build `app/control_plane/projects.py` (CRUD + scoping)
- [ ] Build `app/control_plane/tickets.py` (lifecycle)
- [ ] Wire audit logging into existing `firebase_reporter.py` (dual-write)

### Phase 2: Budget Enforcement (2-3 days)

- [ ] Build `app/control_plane/budgets.py` (atomic enforcement)
- [ ] Build `app/control_plane/cost_tracker.py` (model cost lookup)
- [ ] Integrate budget check into `app/llm_factory.py`
- [ ] Add `budget` Signal commands to Commander
- [ ] Test: verify budget auto-pause works under load

### Phase 3: Ticket Integration (2-3 days)

- [ ] Modify `app/agents/commander.py` to create tickets from Signal messages
- [ ] Modify crew runners to update ticket status (started, comment, completed)
- [ ] Add `tickets` Signal commands
- [ ] Wire ticket assignment to heartbeat wakes
- [ ] Test: verify full ticket lifecycle from Signal → crew → done

### Phase 4: Dashboard (3-5 days)

- [ ] Build `app/control_plane/dashboard_api.py` (all FastAPI routes)
- [ ] Add SSE endpoint for real-time updates
- [ ] Build React dashboard (Vite + React + Tailwind + Chart.js)
- [ ] Dashboard panels: project switcher, kanban, budget bars, audit feed, org chart, agent cards
- [ ] Mount dashboard static build from gateway FastAPI
- [ ] Test: verify Tailscale access from phone

### Phase 5: Governance + Heartbeats (2-3 days)

- [ ] Build `app/control_plane/governance.py` (approval gates)
- [ ] Wire governance into evolution deploy, code changes, budget overrides
- [ ] Build `app/control_plane/heartbeats.py` (enhanced scheduler)
- [ ] Add `autonomous` and `pending` Signal commands
- [ ] Test: verify evolution mutations require approval before deploy

### Phase 6: Multi-Project Polish (1-2 days)

- [ ] Create PLG, Archibal, KaiCart projects with mission statements
- [ ] Set per-project budgets
- [ ] Verify ChromaDB scope isolation per project
- [ ] Verify Mem0 namespace isolation per project
- [ ] Add project filter to all dashboard panels

**Total estimated effort: 13-20 days of focused work.**

---

## 12. Safety Invariants Preserved

| Invariant | How It's Preserved |
|---|---|
| **DGM safety**: eval functions and safety constraints at infrastructure level, never inside agent code | Budget enforcement in `llm_factory.py` (infrastructure). Audit trail in PostgreSQL with INSERT-only grants. Governance gates in control plane, not agent prompts. |
| **SOUL.md constitution**: Safety > Honesty > Compliance > Helpfulness | Completely untouched. SOUL files are not modified by any control plane operation. |
| **Self-Improver read-only humanist layer** | ChromaDB humanist collection remains read-only. Control plane has no write access to philosophy RAG. |
| **Evolution revert-on-regression** | Unchanged. Governance adds an approval gate BEFORE deploy, not after. |
| **AST validation for code proposals** | Unchanged. Control plane audit logs all code changes but does not modify the validation pipeline. |
| **Sandbox isolation** | Unchanged. Code execution still uses Docker sandbox with full hardening. |

---

## 13. What This Gets You

After implementation, AndrusAI becomes a **unified autonomous company platform** with:

1. **Dashboard on your phone** — Tailscale → `http://andrusai:3100` — kanban board, budget charts, audit feed, org chart, governance approvals
2. **Budget guardrails** — Commander can't burn $200 on a runaway Opus loop. Hard limit, atomic enforcement, auto-pause.
3. **PLG / Archibal / KaiCart isolation** — Switch projects from Signal. Each has its own budget, tickets, audit trail, memory scope, and mission context.
4. **Immutable audit trail** — Every LLM call, every ticket, every evolution experiment, every governance decision. Append-only PostgreSQL. Agents can't delete their tracks.
5. **24/7 autonomous operation** — Heartbeat scheduler wakes agents on schedule and on events. Tickets queue when you're asleep, agents process them per heartbeat.
6. **Governance gates** — Evolution mutations, code deploys, and budget overrides require your explicit approval via Signal or dashboard.
7. **All sentience layers intact** — SOUL.md, self-awareness, belief states, proactive cooperation, philosophy RAG, Mem0, ChromaDB, evolution loop — unchanged.
8. **Single Python stack** — No Node.js, no second database, no second package manager. One `docker-compose up`.

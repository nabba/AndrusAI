import { useMemo, useState } from 'react';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import { useCrewTasksQuery, type CrewTask, type CrewStatus } from '../api/queries';
import type { OrgChartAgent } from '../types';
import { useProject } from '../context/useProject';
import { CREW_REGISTRY, crewMeta, type CrewKind } from '../crews';
import { TaskFlowDrawer } from './TaskFlowDrawer';

// Port of the legacy dashboard's "Current & Recent Tasks" section with
// additional crew + org-chart roster panels so every crew, agent, and
// sub-agent is represented — even when idle.

// Crew visuals come from the canonical registry — every known crew gets a
// consistent icon/label across every dashboard surface.
const crewIcon = (name: string | undefined | null) => crewMeta(name).icon;
const crewLabel = (name: string | undefined | null) => crewMeta(name).label;

const STATE_STYLES: Record<string, string> = {
  running: 'text-[#60a5fa] bg-[#60a5fa]/10 border-[#60a5fa]/30',
  completed: 'text-[#34d399] bg-[#34d399]/10 border-[#34d399]/30',
  failed: 'text-[#f87171] bg-[#f87171]/10 border-[#f87171]/30',
  idle: 'text-[#7a8599] bg-[#7a8599]/10 border-[#7a8599]/30',
  unknown: 'text-[#7a8599] bg-[#7a8599]/5 border-[#7a8599]/20',
};

function StateBadge({ state }: { state: string }) {
  const cls = STATE_STYLES[state] ?? STATE_STYLES.unknown;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wider font-medium ${cls}`}>
      {state}
    </span>
  );
}

function fmtDuration(startIso?: string, endIso?: string | null): string {
  if (!startIso) return '—';
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  if (isNaN(start) || isNaN(end)) return '—';
  const secs = Math.max(0, Math.round((end - start) / 1000));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return `${h}h ${m}m`;
}

function fmtEta(task: CrewTask): string {
  if (task.state === 'completed') return 'done';
  if (task.state === 'failed') return 'failed';
  if (!task.eta) return '—';
  const diff = new Date(task.eta).getTime() - Date.now();
  if (isNaN(diff)) return '—';
  if (diff < 0) return 'overdue';
  const secs = Math.round(diff / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  return `${Math.floor(secs / 3600)}h`;
}

function DelegationFlow({ task }: { task: CrewTask }) {
  if (task.delegated_from && task.delegated_to) {
    return (
      <span className="text-[10px] text-[#a78bfa]">
        {task.delegated_from} → {task.delegated_to}
      </span>
    );
  }
  if (task.parent_task_id) {
    return <span className="text-[10px] text-[#a78bfa]">sub-agent</span>;
  }
  return <span className="text-[10px] text-[#7a8599]">—</span>;
}

function StatCard({ label, value, color = 'text-[#e2e8f0]' }: { label: string; value: number | string; color?: string }) {
  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
      <div className="text-[11px] text-[#7a8599] uppercase tracking-wider">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${color}`}>{value}</div>
    </div>
  );
}

function CrewCard({ crew, runningTask }: { crew: CrewStatus; runningTask?: CrewTask }) {
  const state = crew.state ?? 'unknown';
  const meta = crewMeta(crew.name);
  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4 space-y-2 hover:border-[#60a5fa]/40 transition-colors">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xl">{meta.icon}</span>
          <span className="text-sm font-semibold text-[#e2e8f0]">{meta.label}</span>
        </div>
        <StateBadge state={state} />
      </div>
      <div className="text-[10px] text-[#7a8599] leading-snug">{meta.description}</div>
      {runningTask ? (
        <>
          <div className="text-xs text-[#e2e8f0] leading-snug truncate" title={runningTask.summary ?? ''}>
            {runningTask.summary?.slice(0, 80) || '(no summary)'}
          </div>
          <div className="flex items-center gap-3 text-[10px] text-[#7a8599]">
            <span>⏱ {fmtDuration(runningTask.started_at)}</span>
            {runningTask.model && <span>🧠 {runningTask.model}</span>}
            <span>ETA {fmtEta(runningTask)}</span>
          </div>
        </>
      ) : (
        <div className="text-xs text-[#7a8599] italic">Idle</div>
      )}
    </div>
  );
}

function AgentRow({ agent, busyCrews }: { agent: OrgChartAgent; busyCrews: Set<string> }) {
  const busy = busyCrews.has(agent.agent_role);
  return (
    <div className="flex items-center justify-between px-3 py-2 bg-[#0a0e14] border border-[#1e2738] rounded-md">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm">{crewIcon(agent.agent_role)}</span>
        <div className="min-w-0">
          <div className="text-xs text-[#e2e8f0] truncate">{agent.display_name}</div>
          <div className="text-[10px] text-[#7a8599] truncate">
            {agent.agent_role}
            {agent.reports_to ? ` · reports to ${agent.reports_to}` : ''}
          </div>
        </div>
      </div>
      <StateBadge state={busy ? 'running' : 'idle'} />
    </div>
  );
}

function TaskRow({
  task,
  childTasks,
  onSelect,
}: {
  task: CrewTask;
  childTasks: CrewTask[];
  onSelect: (taskId: string) => void;
}) {
  return (
    <>
      <tr
        className="hover:bg-[#1e2738]/50 transition-colors cursor-pointer"
        onClick={() => onSelect(task.id)}
        title="Open task-flow drawer"
      >
        <td className="px-3 py-2 whitespace-nowrap">
          <span className="inline-flex items-center gap-1">
            <span>{crewIcon(task.crew)}</span>
            <span className="text-[#e2e8f0] text-xs">{crewLabel(task.crew)}</span>
          </span>
        </td>
        <td className="px-3 py-2 text-xs text-[#e2e8f0] max-w-md">
          <div className="truncate" title={task.summary ?? ''}>{task.summary?.slice(0, 120) || '—'}</div>
          {task.error && <div className="text-[10px] text-[#f87171] truncate">err: {task.error}</div>}
        </td>
        <td className="px-3 py-2 text-[10px] text-[#7a8599] whitespace-nowrap">{task.model ?? '—'}</td>
        <td className="px-3 py-2 text-right text-[#22d3ee] whitespace-nowrap text-xs">
          {task.tokens_used != null ? task.tokens_used.toLocaleString() : '—'}
        </td>
        <td className="px-3 py-2 text-right text-[#34d399] whitespace-nowrap text-xs">
          {task.cost_usd != null ? `$${task.cost_usd.toFixed(4)}` : '—'}
        </td>
        <td className="px-3 py-2"><StateBadge state={task.state} /></td>
        <td className="px-3 py-2 whitespace-nowrap"><DelegationFlow task={task} /></td>
        <td className={`px-3 py-2 text-xs whitespace-nowrap ${task.state === 'running' ? 'text-[#fb923c]' : 'text-[#7a8599]'}`}>
          {fmtDuration(task.started_at, task.completed_at)}
        </td>
        <td className="px-3 py-2 text-xs text-[#7a8599] whitespace-nowrap">{fmtEta(task)}</td>
      </tr>
      {childTasks.map((child) => (
        <tr
          key={child.id}
          className="opacity-70 hover:bg-[#1e2738]/30 transition-colors cursor-pointer"
          onClick={() => onSelect(child.id)}
          title="Open task-flow drawer"
        >
          <td className="px-3 py-2 whitespace-nowrap pl-8">
            <span className="inline-flex items-center gap-1 text-[#a78bfa]">
              <span>↳</span>
              <span>{crewIcon(child.crew)}</span>
              <span className="text-xs">{crewLabel(child.crew)}</span>
            </span>
          </td>
          <td className="px-3 py-2 text-xs text-[#e2e8f0] max-w-md">
            <div className="truncate" title={child.summary ?? ''}>{child.summary?.slice(0, 120) || '—'}</div>
            {child.sub_agent_progress && <div className="text-[10px] text-[#a78bfa]">{child.sub_agent_progress}</div>}
          </td>
          <td className="px-3 py-2 text-[10px] text-[#7a8599] whitespace-nowrap">{child.model ?? '—'}</td>
          <td className="px-3 py-2 text-right text-[#22d3ee] whitespace-nowrap text-xs">
            {child.tokens_used != null ? child.tokens_used.toLocaleString() : '—'}
          </td>
          <td className="px-3 py-2 text-right text-[#34d399] whitespace-nowrap text-xs">
            {child.cost_usd != null ? `$${child.cost_usd.toFixed(4)}` : '—'}
          </td>
          <td className="px-3 py-2"><StateBadge state={child.state} /></td>
          <td className="px-3 py-2 whitespace-nowrap"><DelegationFlow task={child} /></td>
          <td className={`px-3 py-2 text-xs whitespace-nowrap ${child.state === 'running' ? 'text-[#fb923c]' : 'text-[#7a8599]'}`}>
            {fmtDuration(child.started_at, child.completed_at)}
          </td>
          <td className="px-3 py-2 text-xs text-[#7a8599] whitespace-nowrap">{fmtEta(child)}</td>
        </tr>
      ))}
    </>
  );
}

export function TasksPage() {
  const { activeProject, isAllProjects } = useProject();
  const { data, isLoading, error, refetch } = useCrewTasksQuery(20, activeProject?.id);
  // Task-flow drawer state — clicking any task row opens the nested
  // timeline/tree view in a right-side drawer. null = drawer closed.
  const [flowTaskId, setFlowTaskId] = useState<string | null>(null);

  const tasks = useMemo(() => data?.tasks ?? [], [data?.tasks]);
  const crews = useMemo(() => data?.crews ?? [], [data?.crews]);
  const agents = useMemo(() => data?.agents ?? [], [data?.agents]);

  const summary = useMemo(() => {
    const running = tasks.filter((t) => t.state === 'running').length;
    const completed = tasks.filter((t) => t.state === 'completed').length;
    const failed = tasks.filter((t) => t.state === 'failed').length;
    const idleCrews = crews.filter((c) => (c.state ?? 'unknown') !== 'running').length;
    return { running, completed, failed, idleCrews, totalAgents: agents.length };
  }, [tasks, crews, agents]);

  // Map running tasks to crews so cards can show current work.
  const runningByCrew = useMemo(() => {
    const map = new Map<string, CrewTask>();
    for (const t of tasks) {
      if (t.state === 'running' && !map.has(t.crew)) map.set(t.crew, t);
    }
    return map;
  }, [tasks]);

  // Which crews are currently busy (for the roster side panel).
  const busyCrews = useMemo(() => {
    const s = new Set<string>();
    for (const t of tasks) {
      if (t.state === 'running') {
        s.add(t.crew);
        if (t.delegated_to) s.add(t.delegated_to);
      }
    }
    return s;
  }, [tasks]);

  // Root tasks + their children (sub-agent delegations).
  const { rootTasks, childrenByParent } = useMemo(() => {
    const childrenByParent = new Map<string, CrewTask[]>();
    const rootTasks: CrewTask[] = [];
    for (const t of tasks) {
      if (t.parent_task_id) {
        const arr = childrenByParent.get(t.parent_task_id) ?? [];
        arr.push(t);
        childrenByParent.set(t.parent_task_id, arr);
      } else {
        rootTasks.push(t);
      }
    }
    return { rootTasks, childrenByParent };
  }, [tasks]);

  // Group agents by reports_to for the roster sidebar.
  const agentsByManager = useMemo(() => {
    const groups = new Map<string, OrgChartAgent[]>();
    for (const a of agents) {
      const key = a.reports_to ?? '(top)';
      const arr = groups.get(key) ?? [];
      arr.push(a);
      groups.set(key, arr);
    }
    return groups;
  }, [agents]);

  // Merge backend crews with the canonical registry so every crew is visible
  // even when Firestore or the backend list hasn't surfaced it yet. Backend
  // status (idle/running/...) wins when present; otherwise we show "unknown".
  const crewsByKind = useMemo(() => {
    const byName = new Map<string, CrewStatus>();
    for (const c of crews) byName.set(c.name, c);
    const groups: Record<CrewKind, CrewStatus[]> = { user: [], internal: [] };
    for (const meta of CREW_REGISTRY) {
      const fromBackend = byName.get(meta.name);
      groups[meta.kind].push(
        fromBackend
          ? { ...fromBackend, kind: fromBackend.kind ?? meta.kind }
          : { name: meta.name, state: 'unknown', kind: meta.kind },
      );
    }
    // Any unregistered crew the backend surfaces (e.g. ad-hoc) falls into 'user'.
    for (const c of crews) {
      if (!CREW_REGISTRY.some((m) => m.name === c.name)) {
        groups[(c.kind as CrewKind | undefined) ?? 'user'].push(c);
      }
    }
    return groups;
  }, [crews]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-6 w-48" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (error) return <ErrorPanel error={error} onRetry={refetch} />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Crew Activity</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          {isAllProjects ? 'All projects' : activeProject ? `Project: ${activeProject.name}` : 'All projects'}
          <span className="opacity-60"> · current and recent tasks across every crew, agent, and sub-agent.</span>
          {data?.error && <span className="text-[#fbbf24]"> · {data.error}</span>}
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="Running" value={summary.running} color="text-[#60a5fa]" />
        <StatCard label="Completed" value={summary.completed} color="text-[#34d399]" />
        <StatCard label="Failed" value={summary.failed} color="text-[#f87171]" />
        <StatCard label="Idle Crews" value={summary.idleCrews} color="text-[#7a8599]" />
        <StatCard label="Agents" value={summary.totalAgents} color="text-[#a78bfa]" />
      </div>

      {/* Crew grid — every crew represented, grouped by kind */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-[#7a8599] uppercase tracking-wider">
            User-Addressable Crews
          </h2>
          <span className="text-[10px] text-[#7a8599]">{crewsByKind.user.length} crews</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {crewsByKind.user.map((c) => (
            <CrewCard key={c.name} crew={c} runningTask={runningByCrew.get(c.name)} />
          ))}
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-[#a78bfa] uppercase tracking-wider">
            Internal Crews
          </h2>
          <span className="text-[10px] text-[#7a8599]">{crewsByKind.internal.length} crews</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {crewsByKind.internal.map((c) => (
            <CrewCard key={c.name} crew={c} runningTask={runningByCrew.get(c.name)} />
          ))}
        </div>
      </section>

      {/* Agent roster — every agent/subagent represented, grouped by manager */}
      <section>
        <h2 className="text-sm font-medium text-[#7a8599] uppercase tracking-wider mb-3">
          Agent Roster ({agents.length})
        </h2>
        {agents.length === 0 ? (
          <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4 text-sm text-[#7a8599]">
            No agents registered in org chart.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from(agentsByManager.entries()).map(([manager, group]) => (
              <div key={manager} className="bg-[#111820] border border-[#1e2738] rounded-lg p-3 space-y-2">
                <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">
                  {manager === '(top)' ? 'Top-Level' : `Reports to ${manager}`}
                </div>
                <div className="space-y-1.5">
                  {group.map((a) => (
                    <AgentRow key={a.agent_role} agent={a} busyCrews={busyCrews} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Recent tasks table */}
      <section>
        <h2 className="text-sm font-medium text-[#7a8599] uppercase tracking-wider mb-3">
          Current & Recent Tasks ({tasks.length})
        </h2>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
          {tasks.length === 0 ? (
            <div className="p-8 text-center text-[#7a8599] text-sm">No tasks yet.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#1e2738]">
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Crew</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Task</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">LLM</th>
                    <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Tokens</th>
                    <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Cost</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Status</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Flow</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Duration</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">ETA</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1e2738]">
                  {rootTasks.map((t) => (
                    <TaskRow
                      key={t.id}
                      task={t}
                      childTasks={childrenByParent.get(t.id) ?? []}
                      onSelect={setFlowTaskId}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* Task-flow drawer — opens on row click, polls /timeline at 2 s
          while the task is running, shows Tree + Timeline views. */}
      <TaskFlowDrawer
        taskId={flowTaskId}
        onClose={() => setFlowTaskId(null)}
      />
    </div>
  );
}

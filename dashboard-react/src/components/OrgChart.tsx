import { useApi } from '../hooks/useApi';
import type { OrgChartAgent } from '../types/index.ts';

function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse bg-[#1e2738] rounded ${className}`} />;
}

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-[#34d399]',
  idle: 'bg-[#fbbf24]',
  offline: 'bg-[#7a8599]',
};

const ROLE_ICONS: Record<string, string> = {
  commander: '👑',
  researcher: '🔬',
  coder: '💻',
  writer: '✍️',
  media: '🎨',
  critic: '🎯',
  'self-improver': '🔄',
  introspector: '🧠',
  default: '🤖',
};

function getRoleIcon(role: string): string {
  const lower = role.toLowerCase();
  for (const [key, icon] of Object.entries(ROLE_ICONS)) {
    if (lower.includes(key)) return icon;
  }
  return ROLE_ICONS.default;
}

function AgentNode({ agent, depth = 0 }: { agent: OrgChartAgent; depth?: number }) {
  const statusColor = STATUS_COLORS[agent.status ?? 'offline'] ?? STATUS_COLORS.offline;
  const icon = getRoleIcon(agent.role);
  const hasChildren = agent.children && agent.children.length > 0;

  return (
    <div className="flex flex-col items-center">
      {/* Node */}
      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3 w-36 text-center hover:border-[#60a5fa]/40 transition-colors relative">
        <div className="text-2xl mb-1">{icon}</div>
        <div className="text-xs font-medium text-[#e2e8f0] truncate">{agent.name}</div>
        <div className="text-xs text-[#7a8599] capitalize mt-0.5 truncate">{agent.role}</div>
        {agent.status && (
          <div className="flex items-center justify-center gap-1 mt-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${statusColor}`} />
            <span className="text-[10px] text-[#7a8599] capitalize">{agent.status}</span>
          </div>
        )}
      </div>

      {/* Children */}
      {hasChildren && (
        <>
          {/* Vertical connector */}
          <div className="w-px h-6 bg-[#1e2738]" />

          {/* Horizontal line spanning children */}
          {agent.children!.length > 1 && (
            <div
              className="h-px bg-[#1e2738]"
              style={{ width: `${Math.min(agent.children!.length * 160, 900)}px` }}
            />
          )}

          {/* Children row */}
          <div className="flex gap-4 items-start">
            {agent.children!.map((child) => (
              <div key={child.id} className="flex flex-col items-center">
                {agent.children!.length > 1 && <div className="w-px h-6 bg-[#1e2738]" />}
                <AgentNode agent={child} depth={depth + 1} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function buildTree(agents: OrgChartAgent[]): OrgChartAgent[] {
  // If agents already have children populated, return as-is
  const hasChildren = agents.some((a) => a.children && a.children.length > 0);
  if (hasChildren) return agents.filter((a) => !a.reports_to);

  // Build tree from flat list
  const map = new Map<string, OrgChartAgent>();
  const roots: OrgChartAgent[] = [];

  agents.forEach((a) => {
    map.set(a.id, { ...a, children: [] });
  });

  map.forEach((agent) => {
    if (agent.reports_to && map.has(agent.reports_to)) {
      const parent = map.get(agent.reports_to)!;
      parent.children = parent.children ?? [];
      parent.children.push(agent);
    } else {
      roots.push(agent);
    }
  });

  return roots;
}

// Fallback static org chart when API has no data
const FALLBACK_ORG: OrgChartAgent = {
  id: 'commander',
  name: 'Commander',
  role: 'commander',
  status: 'active',
  children: [
    { id: 'researcher', name: 'Researcher', role: 'researcher', status: 'idle', children: [] },
    { id: 'coder', name: 'Coder', role: 'coder', status: 'active', children: [] },
    { id: 'writer', name: 'Writer', role: 'writer', status: 'idle', children: [] },
    { id: 'media', name: 'Media', role: 'media', status: 'offline', children: [] },
    { id: 'critic', name: 'Critic', role: 'critic', status: 'active', children: [] },
    { id: 'self-improver', name: 'Self-Improver', role: 'self-improver', status: 'idle', children: [] },
    { id: 'introspector', name: 'Introspector', role: 'introspector', status: 'idle', children: [] },
  ],
};

export function OrgChart() {
  const { data: agents, loading, error } = useApi<OrgChartAgent[]>('/org-chart', 60000);

  let roots: OrgChartAgent[] = [];
  if (agents) {
    roots = buildTree(agents);
    if (roots.length === 0) roots = [FALLBACK_ORG];
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Org Chart</h1>
        <p className="text-sm text-[#7a8599] mt-1">Agent hierarchy and status</p>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-[#7a8599]">
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#34d399]" />
          Active
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#fbbf24]" />
          Idle
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-[#7a8599]" />
          Offline
        </div>
      </div>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-6 overflow-x-auto">
        {loading ? (
          <div className="flex justify-center">
            <div className="space-y-4 text-center">
              <Skeleton className="h-24 w-36 mx-auto" />
              <div className="flex gap-4 justify-center">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-20 w-32" />
                ))}
              </div>
            </div>
          </div>
        ) : error ? (
          <div className="text-center py-8 text-[#f87171] text-sm">{error}</div>
        ) : (
          <div className="flex flex-col items-center gap-0 min-w-max mx-auto">
            {roots.map((root) => (
              <AgentNode key={root.id} agent={root} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

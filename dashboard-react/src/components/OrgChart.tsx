import type { OrgChartAgent } from '../types';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useOrgChartQuery,
  useDelegationSettingsQuery,
  useSetDelegationSetting,
} from '../api/queries';

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

type TreeAgent = OrgChartAgent & { children: TreeAgent[] };

function AgentNode({ agent }: { agent: TreeAgent }) {
  const icon = getRoleIcon(agent.agent_role);
  const hasChildren = agent.children.length > 0;

  return (
    <div className="flex flex-col items-center">
      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3 w-36 text-center hover:border-[#60a5fa]/40 transition-colors relative">
        <div className="text-2xl mb-1">{icon}</div>
        <div className="text-xs font-medium text-[#e2e8f0] truncate">{agent.display_name}</div>
        <div className="text-xs text-[#7a8599] capitalize mt-0.5 truncate">{agent.agent_role}</div>
        {agent.job_description && (
          <div className="text-[10px] text-[#7a8599] mt-1 truncate" title={agent.job_description}>
            {agent.job_description.slice(0, 30)}
          </div>
        )}
      </div>

      {hasChildren && (
        <>
          <div className="w-px h-6 bg-[#1e2738]" />
          {agent.children.length > 1 && (
            <div
              className="h-px bg-[#1e2738]"
              style={{ width: `${Math.min(agent.children.length * 160, 900)}px` }}
            />
          )}
          <div className="flex gap-4 items-start">
            {agent.children.map((child) => (
              <div key={child.agent_role} className="flex flex-col items-center">
                {agent.children.length > 1 && <div className="w-px h-6 bg-[#1e2738]" />}
                <AgentNode agent={child} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function buildTree(agents: OrgChartAgent[]): TreeAgent[] {
  const map = new Map<string, TreeAgent>();
  const roots: TreeAgent[] = [];

  agents.forEach((a) => {
    map.set(a.agent_role, { ...a, children: [] });
  });

  map.forEach((agent) => {
    if (agent.reports_to && map.has(agent.reports_to)) {
      map.get(agent.reports_to)!.children.push(agent);
    } else {
      roots.push(agent);
    }
  });

  return roots;
}

// ── Delegation Mode toggles ─────────────────────────────────────────────────
// When ON for a crew, tasks dispatch to Coordinator + specialists instead of
// a single monolithic agent.  Preserves the full tool palette on providers
// with tight tool limits (Anthropic).

const DELEGATION_DESCRIPTIONS: Record<string, string> = {
  research:
    'Research crew → Coordinator + Web + Document + Synthesis specialists. Each sub-agent keeps ≤ 18 tools so Anthropic strict-mode works. ~2× LLM calls, full tool palette preserved.',
  coding:
    'Coding crew → Coordinator + Execution + Debug specialists. Coordinator writes the code, Execution runs it in the sandbox, Debug diagnoses failures from journal/tensions. Great for multi-step debugging.',
  writing:
    'Writing crew → Coordinator + Research + Synthesis specialists. Research gathers facts into a brief, Synthesis produces the finished prose with dialectics/philosophy. Best for longer substantive pieces.',
};

function DelegationPanel() {
  const { data, isLoading } = useDelegationSettingsQuery();
  const setMut = useSetDelegationSetting();
  const settings = data?.settings ?? {};

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-5">
      <div className="mb-3">
        <h2 className="text-base font-semibold text-[#e2e8f0]">Delegation Mode</h2>
        <p className="text-xs text-[#7a8599] mt-1">
          Split each crew into a Coordinator + specialist sub-agents. Every agent stays
          under any provider's tool limit. Tradeoff: ~2× LLM calls per task.
        </p>
      </div>

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : (
        <div className="space-y-2">
          {Object.entries(settings).map(([crew, enabled]) => {
            const desc = DELEGATION_DESCRIPTIONS[crew] ?? '';
            const pending = setMut.isPending && setMut.variables?.crew === crew;
            return (
              <div
                key={crew}
                className={`flex items-start gap-3 p-3 rounded border ${
                  enabled
                    ? 'bg-[#34d399]/5 border-[#34d399]/30'
                    : 'bg-[#0a0e14] border-[#1e2738]'
                }`}
              >
                <button
                  disabled={pending}
                  onClick={() => setMut.mutate({ crew, enabled: !enabled })}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors flex-shrink-0 ${
                    enabled ? 'bg-[#34d399]' : 'bg-[#1e2738]'
                  } ${pending ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
                  aria-label={`Toggle delegation for ${crew}`}
                >
                  <span
                    className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
                      enabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-[#e2e8f0] capitalize">
                      {crew}
                    </span>
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                        enabled
                          ? 'bg-[#34d399]/20 text-[#34d399]'
                          : 'bg-[#1e2738] text-[#7a8599]'
                      }`}
                    >
                      {enabled ? 'DELEGATION ON' : 'SINGLE AGENT'}
                    </span>
                  </div>
                  {desc && <p className="text-xs text-[#7a8599] mt-1">{desc}</p>}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {setMut.isError && (
        <p className="text-xs text-[#f87171] mt-2">
          Failed to toggle: {(setMut.error as Error).message}
        </p>
      )}
    </div>
  );
}

export function OrgChart() {
  const { data: agents, isLoading, error, refetch } = useOrgChartQuery();
  const roots = agents ? buildTree(agents) : [];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Org Chart</h1>
        <p className="text-sm text-[#7a8599] mt-1">Agent hierarchy</p>
      </div>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-6 overflow-x-auto">
        {isLoading ? (
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
          <ErrorPanel error={error} onRetry={refetch} />
        ) : (
          <div className="flex flex-col items-center gap-0 min-w-max mx-auto">
            {roots.map((root) => (
              <AgentNode key={root.agent_role} agent={root} />
            ))}
          </div>
        )}
      </div>

      <DelegationPanel />
    </div>
  );
}

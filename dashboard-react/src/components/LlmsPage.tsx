import { useMemo, useState } from 'react';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useLlmCatalogQuery,
  useLlmRolesQuery,
  useLlmDiscoveryQuery,
  useRunLlmDiscovery,
  useTechRadarQuery,
  type LlmModel,
  type DiscoveredModel,
  type TechDiscovery,
} from '../api/queries';

type LlmsTab = 'catalog' | 'discovery' | 'radar';

const TIER_COLORS: Record<string, string> = {
  local:   'bg-[#7a8599]/15 text-[#7a8599] border-[#7a8599]/30',
  free:    'bg-[#34d399]/15 text-[#34d399] border-[#34d399]/30',
  budget:  'bg-[#22d3ee]/15 text-[#22d3ee] border-[#22d3ee]/30',
  mid:     'bg-[#60a5fa]/15 text-[#60a5fa] border-[#60a5fa]/30',
  premium: 'bg-[#a78bfa]/15 text-[#a78bfa] border-[#a78bfa]/30',
};
const TIER_ORDER = ['premium', 'mid', 'budget', 'free', 'local'];

const RADAR_COLORS: Record<string, { text: string; bg: string }> = {
  models:     { text: 'text-[#60a5fa]', bg: 'bg-[#60a5fa]/10 border-[#60a5fa]/30' },
  frameworks: { text: 'text-[#a78bfa]', bg: 'bg-[#a78bfa]/10 border-[#a78bfa]/30' },
  research:   { text: 'text-[#34d399]', bg: 'bg-[#34d399]/10 border-[#34d399]/30' },
  tools:      { text: 'text-[#fbbf24]', bg: 'bg-[#fbbf24]/10 border-[#fbbf24]/30' },
  unknown:    { text: 'text-[#7a8599]', bg: 'bg-[#7a8599]/10 border-[#7a8599]/30' },
};

function fmtCost(perM?: number): string {
  if (perM == null) return '—';
  if (perM === 0) return 'free';
  return `$${perM.toFixed(2)}/M`;
}

function relTime(iso?: string): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return iso;
  const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

// ── Catalog tab ─────────────────────────────────────────────────────────────

function CatalogTab() {
  const catQ = useLlmCatalogQuery();
  const rolesQ = useLlmRolesQuery();

  if (catQ.isLoading) return <Skeleton className="h-64" />;
  if (catQ.error) return <ErrorPanel error={catQ.error} onRetry={catQ.refetch} />;

  const models = catQ.data?.models ?? [];
  const roleAssignments = catQ.data?.role_assignments ?? {};
  const costMode = catQ.data?.cost_mode ?? 'balanced';
  const overrides = rolesQ.data?.assignments ?? [];

  // Group models by tier.
  const byTier = new Map<string, LlmModel[]>();
  for (const m of models) {
    const tier = (m.tier as string) ?? 'unknown';
    const arr = byTier.get(tier) ?? [];
    arr.push(m);
    byTier.set(tier, arr);
  }
  const tiers = TIER_ORDER.filter((t) => byTier.has(t)).concat(
    Array.from(byTier.keys()).filter((t) => !TIER_ORDER.includes(t)),
  );

  return (
    <div className="space-y-6">
      {catQ.data?.error && <div className="text-xs text-[#fbbf24]">{catQ.data.error}</div>}

      {/* Role assignments table */}
      <section>
        <h3 className="text-xs font-medium text-[#7a8599] uppercase tracking-wider mb-2">
          Role Assignments — cost mode: <span className="text-[#60a5fa]">{costMode}</span>
        </h3>
        {Object.keys(roleAssignments).length === 0 ? (
          <p className="text-sm text-[#7a8599] italic">No role assignments resolved.</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {Object.entries(roleAssignments).map(([role, model]) => {
              const override = overrides.find((o) => o.role === role && o.cost_mode === costMode);
              return (
                <div key={role} className="flex items-center justify-between bg-[#0a0e14] border border-[#1e2738] rounded-lg px-3 py-2">
                  <span className="text-xs text-[#a78bfa] uppercase tracking-wider">{role}</span>
                  <div className="text-right min-w-0">
                    <div className="text-xs text-[#e2e8f0] truncate">{model}</div>
                    {override && (
                      <div className="text-[10px] text-[#7a8599]">
                        via {override.source ?? 'override'}{override.assigned_by ? ` · by ${override.assigned_by}` : ''}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Model catalog grouped by tier */}
      <section>
        <h3 className="text-xs font-medium text-[#7a8599] uppercase tracking-wider mb-2">
          Model Catalog ({models.length})
        </h3>
        <div className="space-y-4">
          {tiers.map((tier) => {
            const rows = byTier.get(tier) ?? [];
            return (
              <div key={tier}>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`text-[10px] px-2 py-0.5 rounded border uppercase tracking-wider font-medium ${TIER_COLORS[tier] ?? TIER_COLORS.local}`}>
                    {tier}
                  </span>
                  <span className="text-[10px] text-[#7a8599]">{rows.length} model{rows.length === 1 ? '' : 's'}</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {rows.map((m) => <ModelCard key={m.name} model={m} assignedRoles={Object.entries(roleAssignments).filter(([, mod]) => mod === m.name).map(([r]) => r)} />)}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Postgres role-assignment overrides */}
      {overrides.length > 0 && (
        <section>
          <h3 className="text-xs font-medium text-[#7a8599] uppercase tracking-wider mb-2">
            Overrides ({overrides.length})
          </h3>
          <div className="bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1e2738]">
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Role</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Cost Mode</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Model</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Source</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Assigned by</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Priority</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e2738]">
                {overrides.map((o, i) => (
                  <tr key={i}>
                    <td className="px-3 py-2 text-[#a78bfa]">{o.role}</td>
                    <td className="px-3 py-2 text-[#7a8599]">{o.cost_mode}</td>
                    <td className="px-3 py-2 text-[#e2e8f0]">{o.model}</td>
                    <td className="px-3 py-2 text-[#7a8599]">{o.source ?? '—'}</td>
                    <td className="px-3 py-2 text-[#7a8599]">{o.assigned_by ?? '—'}</td>
                    <td className="px-3 py-2 text-[#7a8599]">{o.priority ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function ModelCard({ model, assignedRoles }: { model: LlmModel; assignedRoles: string[] }) {
  const tier = (model.tier as string) ?? 'unknown';
  return (
    <div className="bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3">
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="min-w-0 flex-1">
          <div className="text-sm text-[#e2e8f0] font-medium truncate">{model.name}</div>
          <div className="text-[10px] text-[#7a8599] truncate">{model.provider ?? '?'} · ctx {model.context ?? '?'}</div>
        </div>
        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${TIER_COLORS[tier] ?? TIER_COLORS.local}`}>
          {tier}
        </span>
      </div>
      <div className="flex items-center justify-between text-[10px] text-[#7a8599] mt-2">
        <span>in: {fmtCost(model.cost_input_per_m)}</span>
        <span>out: {fmtCost(model.cost_output_per_m)}</span>
        {model.tool_use_reliability != null && (
          <span>tools: {(model.tool_use_reliability * 100).toFixed(0)}%</span>
        )}
      </div>
      {assignedRoles.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {assignedRoles.map((r) => (
            <span key={r} className="text-[9px] px-1.5 py-0.5 rounded bg-[#a78bfa]/10 text-[#a78bfa] border border-[#a78bfa]/30 uppercase tracking-wider">
              {r}
            </span>
          ))}
        </div>
      )}
      {model.description ? (
        <p className="text-[10px] text-[#7a8599] mt-2 line-clamp-2">{String(model.description)}</p>
      ) : null}
    </div>
  );
}

// ── Discovery tab ───────────────────────────────────────────────────────────

function DiscoveryTab() {
  const discQ = useLlmDiscoveryQuery();
  const run = useRunLlmDiscovery();
  const [benchmarks, setBenchmarks] = useState(3);

  const models = useMemo(() => discQ.data?.discovered ?? [], [discQ.data?.discovered]);
  const stats = useMemo(() => ({
    total: models.length,
    promoted: models.filter((m) => m.status === 'promoted').length,
    benchmarking: models.filter((m) => m.status === 'benchmarking').length,
    discovered: models.filter((m) => m.status === 'discovered').length,
  }), [models]);

  return (
    <div className="space-y-4">
      {/* Control bar */}
      <div className="flex flex-wrap items-center gap-3 bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3">
        <span className="text-xs text-[#7a8599]">Benchmark budget:</span>
        <input
          type="number"
          min={1}
          max={10}
          value={benchmarks}
          onChange={(e) => setBenchmarks(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
          className="w-16 bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
        />
        <button
          onClick={() => run.mutate(benchmarks)}
          disabled={run.isPending}
          className="px-3 py-1.5 text-sm bg-[#60a5fa]/20 border border-[#60a5fa]/30 text-[#60a5fa] rounded-lg hover:bg-[#60a5fa]/30 disabled:opacity-50 transition-colors"
        >
          {run.isPending ? 'Running…' : 'Run Discovery'}
        </button>
        {run.data?.result && (
          <span className="text-[10px] text-[#34d399]">
            last run: {JSON.stringify(run.data.result).slice(0, 120)}
          </span>
        )}
        {run.error && (
          <span className="text-[10px] text-[#f87171]">{(run.error as Error).message}</span>
        )}
        <span className="ml-auto text-[10px] text-[#7a8599]">
          Pipeline: SCAN → FILTER → BENCHMARK → PROPOSE → PROMOTE (governance-gated for paid tiers)
        </span>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-2">
        {[
          { label: 'Total', value: stats.total, color: 'text-[#e2e8f0]' },
          { label: 'Discovered', value: stats.discovered, color: 'text-[#60a5fa]' },
          { label: 'Benchmarking', value: stats.benchmarking, color: 'text-[#fbbf24]' },
          { label: 'Promoted', value: stats.promoted, color: 'text-[#34d399]' },
        ].map((s) => (
          <div key={s.label} className="bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3">
            <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">{s.label}</div>
            <div className={`text-xl font-bold ${s.color}`}>{s.value}</div>
          </div>
        ))}
      </div>

      {discQ.isLoading ? (
        <Skeleton className="h-48" />
      ) : discQ.error ? (
        <ErrorPanel error={discQ.error} onRetry={discQ.refetch} />
      ) : discQ.data?.error ? (
        <div className="text-xs text-[#fbbf24]">{discQ.data.error}</div>
      ) : models.length === 0 ? (
        <p className="text-sm text-[#7a8599] italic">No discovered models yet — run discovery to scan providers.</p>
      ) : (
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1e2738]">
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Model</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Provider</th>
                  <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Context</th>
                  <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">In $/M</th>
                  <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Out $/M</th>
                  <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Score</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Role</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Status</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e2738]">
                {models.map((m) => <DiscoveredRow key={m.model_id} m={m} />)}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function DiscoveredRow({ m }: { m: DiscoveredModel }) {
  const statusColor =
    m.status === 'promoted' ? 'text-[#34d399]'
    : m.status === 'benchmarking' ? 'text-[#fbbf24]'
    : m.status === 'discovered' ? 'text-[#60a5fa]'
    : 'text-[#7a8599]';
  return (
    <tr className="hover:bg-[#1e2738]/50 transition-colors">
      <td className="px-3 py-2 text-xs text-[#e2e8f0]">{m.display_name ?? m.model_id}</td>
      <td className="px-3 py-2 text-xs text-[#7a8599]">{m.provider ?? '—'}</td>
      <td className="px-3 py-2 text-xs text-right text-[#7a8599]">
        {m.context_window ? m.context_window.toLocaleString() : '—'}
      </td>
      <td className="px-3 py-2 text-xs text-right text-[#7a8599]">{fmtCost(m.cost_input_per_m)}</td>
      <td className="px-3 py-2 text-xs text-right text-[#7a8599]">{fmtCost(m.cost_output_per_m)}</td>
      <td className="px-3 py-2 text-xs text-right text-[#34d399]">
        {m.benchmark_score != null ? m.benchmark_score.toFixed(3) : '—'}
      </td>
      <td className="px-3 py-2 text-xs text-[#a78bfa]">{m.benchmark_role ?? '—'}</td>
      <td className={`px-3 py-2 text-xs uppercase tracking-wider font-medium ${statusColor}`}>
        {m.status ?? 'unknown'}
        {m.promoted_tier ? <span className="text-[#7a8599] ml-1 normal-case">· {m.promoted_tier}</span> : null}
      </td>
      <td className="px-3 py-2 text-[10px] text-[#7a8599]">{relTime(m.updated_at ?? m.created_at)}</td>
    </tr>
  );
}

// ── Tech Radar tab ──────────────────────────────────────────────────────────

function TechRadarTab() {
  const { data, isLoading, error, refetch } = useTechRadarQuery();

  const items = useMemo(() => data?.discoveries ?? [], [data?.discoveries]);
  const buckets = useMemo(() => {
    const map = new Map<string, TechDiscovery[]>();
    for (const it of items) {
      const key = (it.category || 'unknown').toLowerCase();
      const arr = map.get(key) ?? [];
      arr.push(it);
      map.set(key, arr);
    }
    return map;
  }, [items]);

  if (isLoading) return <Skeleton className="h-48" />;
  if (error) return <ErrorPanel error={error} onRetry={refetch} />;

  if (items.length === 0) {
    return (
      <p className="text-sm text-[#7a8599] italic">
        No tech discoveries yet — the background scanner records items during idle time.
      </p>
    );
  }

  const orderedCategories = ['models', 'frameworks', 'research', 'tools', 'unknown']
    .filter((k) => buckets.has(k))
    .concat(Array.from(buckets.keys()).filter((k) => !['models', 'frameworks', 'research', 'tools', 'unknown'].includes(k)));

  return (
    <div className="space-y-5">
      {data?.error && <div className="text-xs text-[#fbbf24]">{data.error}</div>}
      {orderedCategories.map((cat) => {
        const colors = RADAR_COLORS[cat] ?? RADAR_COLORS.unknown;
        const rows = buckets.get(cat) ?? [];
        return (
          <section key={cat}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`text-[10px] px-2 py-0.5 rounded border uppercase tracking-wider font-medium ${colors.bg} ${colors.text}`}>
                {cat}
              </span>
              <span className="text-[10px] text-[#7a8599]">{rows.length} item{rows.length === 1 ? '' : 's'}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {rows.map((d, i) => (
                <div key={`${d.title}-${i}`} className="bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3">
                  <div className={`text-sm font-medium ${colors.text}`}>{d.title}</div>
                  {d.summary && <div className="text-xs text-[#7a8599] mt-1 leading-snug">{d.summary}</div>}
                  {d.action && (
                    <div className="text-[10px] text-[#a78bfa] mt-2">
                      <strong className="uppercase tracking-wider">Action:</strong> {d.action}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export function LlmsPage() {
  const [tab, setTab] = useState<LlmsTab>('catalog');

  const tabs: { key: LlmsTab; label: string; icon: string }[] = [
    { key: 'catalog', label: 'Catalog & Roles', icon: '📚' },
    { key: 'discovery', label: 'Discovery', icon: '🛰️' },
    { key: 'radar', label: 'Tech Radar', icon: '🔬' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">LLMs</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Model catalog, role assignments, automated discovery, and tech-stack radar.
        </p>
      </div>

      <div className="flex gap-1 bg-[#111820] rounded-lg p-1 border border-[#1e2738] w-fit">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-1.5 rounded-md text-sm transition-colors flex items-center gap-2 ${
              tab === t.key
                ? 'bg-[#60a5fa]/15 text-[#60a5fa] font-medium'
                : 'text-[#7a8599] hover:text-[#e2e8f0]'
            }`}
          >
            <span>{t.icon}</span>
            <span>{t.label}</span>
          </button>
        ))}
      </div>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
        {tab === 'catalog' && <CatalogTab />}
        {tab === 'discovery' && <DiscoveryTab />}
        {tab === 'radar' && <TechRadarTab />}
      </div>
    </div>
  );
}

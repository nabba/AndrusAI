import { useMemo, useState } from 'react';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useLlmCatalogQuery,
  useLlmRolesQuery,
  useLlmDiscoveryQuery,
  useRunLlmDiscovery,
  useTechRadarQuery,
  useLlmModeQuery,
  useSetLlmMode,
  useLlmPromotionsQuery,
  useLlmPinsQuery,
  usePromoteModel,
  useDemoteModel,
  usePinRole,
  useUnpinRole,
  type LlmModel,
  type DiscoveredModel,
  type TechDiscovery,
  type LlmMode,
} from '../api/queries';

// Roles the resolver knows about — used by the pin dialog.
const PINNABLE_ROLES = [
  'commander', 'coding', 'research', 'writing', 'media', 'critic',
  'vetting', 'synthesis', 'introspector', 'self_improve',
  'planner', 'evo_critic', 'default',
] as const;
const COST_MODES = ['budget', 'balanced', 'quality'] as const;

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
  const promotionsQ = useLlmPromotionsQuery();
  const pinsQ = useLlmPinsQuery();
  const promote = usePromoteModel();
  const demote = useDemoteModel();
  const pin = usePinRole();
  const unpin = useUnpinRole();
  const [pinDialog, setPinDialog] = useState<
    | null
    | { model: string; role: string; cost_mode: string; reason: string }
  >(null);

  if (catQ.isLoading) return <Skeleton className="h-64" />;
  if (catQ.error) return <ErrorPanel error={catQ.error} onRetry={catQ.refetch} />;

  const models = catQ.data?.models ?? [];
  const roleAssignments = catQ.data?.role_assignments ?? {};
  const costMode = catQ.data?.cost_mode ?? 'balanced';
  const overrides = rolesQ.data?.assignments ?? [];
  const promotedSet = new Set(
    (promotionsQ.data?.promotions ?? []).map((p) => p.model),
  );
  const pinsByModel: Record<string, { role: string; cost_mode: string }[]> = {};
  for (const p of pinsQ.data?.pins ?? []) {
    (pinsByModel[p.model] ||= []).push({ role: p.role, cost_mode: p.cost_mode });
  }

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
                  {rows.map((m) => (
                    <ModelCard
                      key={m.name}
                      model={m}
                      assignedRoles={Object.entries(roleAssignments).filter(([, mod]) => mod === m.name).map(([r]) => r)}
                      promoted={promotedSet.has(m.name)}
                      pins={pinsByModel[m.name] ?? []}
                      onTogglePromote={(model) => {
                        if (promotedSet.has(model)) demote.mutate({ model });
                        else promote.mutate({ model });
                      }}
                      onOpenPin={(model) =>
                        setPinDialog({
                          model,
                          role: PINNABLE_ROLES[0],
                          cost_mode: costMode,
                          reason: '',
                        })
                      }
                      promoteBusy={promote.isPending || demote.isPending}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Pin dialog (opens from any ModelCard) */}
      {pinDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setPinDialog(null)}
        >
          <div
            className="bg-[#0a0e14] border border-[#1e2738] rounded-lg p-4 w-full max-w-md space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <h4 className="text-sm font-medium text-[#e2e8f0]">
              Pin <span className="text-[#a78bfa]">{pinDialog.model}</span>
            </h4>
            <p className="text-[11px] text-[#7a8599]">
              Hand-pin overrides the resolver for the chosen role + cost_mode.
              Remove the pin to let scoring take over again.
            </p>

            <label className="block text-[10px] uppercase tracking-wider text-[#7a8599]">Role</label>
            <select
              value={pinDialog.role}
              onChange={(e) => setPinDialog({ ...pinDialog, role: e.target.value })}
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1.5 text-sm text-[#e2e8f0]"
            >
              {PINNABLE_ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>

            <label className="block text-[10px] uppercase tracking-wider text-[#7a8599]">Cost mode</label>
            <select
              value={pinDialog.cost_mode}
              onChange={(e) => setPinDialog({ ...pinDialog, cost_mode: e.target.value })}
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1.5 text-sm text-[#e2e8f0]"
            >
              {COST_MODES.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>

            <label className="block text-[10px] uppercase tracking-wider text-[#7a8599]">Reason (optional)</label>
            <input
              value={pinDialog.reason}
              onChange={(e) => setPinDialog({ ...pinDialog, reason: e.target.value })}
              placeholder="why this pin?"
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1.5 text-sm text-[#e2e8f0]"
            />

            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setPinDialog(null)}
                className="px-3 py-1.5 text-sm text-[#7a8599] hover:text-[#e2e8f0]"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  pin.mutate({
                    role: pinDialog.role,
                    cost_mode: pinDialog.cost_mode,
                    model: pinDialog.model,
                    reason: pinDialog.reason || undefined,
                  });
                  setPinDialog(null);
                }}
                disabled={pin.isPending}
                className="px-3 py-1.5 text-sm bg-[#a78bfa]/20 border border-[#a78bfa]/40 text-[#a78bfa] rounded hover:bg-[#a78bfa]/30 disabled:opacity-50"
              >
                {pin.isPending ? 'Pinning…' : 'Pin'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Active hand-pins (layer 3 overrides) */}
      {(pinsQ.data?.pins?.length ?? 0) > 0 && (
        <section>
          <h3 className="text-xs font-medium text-[#7a8599] uppercase tracking-wider mb-2">
            Active Hand Pins ({pinsQ.data?.pins.length})
          </h3>
          <div className="bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1e2738]">
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Role</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Cost Mode</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Model</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Reason</th>
                  <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e2738]">
                {(pinsQ.data?.pins ?? []).map((p) => (
                  <tr key={`${p.role}-${p.cost_mode}-${p.model}`}>
                    <td className="px-3 py-2 text-[#a78bfa]">{p.role}</td>
                    <td className="px-3 py-2 text-[#7a8599]">{p.cost_mode}</td>
                    <td className="px-3 py-2 text-[#e2e8f0]">{p.model}</td>
                    <td className="px-3 py-2 text-[#7a8599] truncate max-w-xs">{p.reason ?? '—'}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={() => unpin.mutate({ role: p.role, cost_mode: p.cost_mode })}
                        disabled={unpin.isPending}
                        className="text-xs text-[#f87171] hover:underline disabled:opacity-50"
                      >
                        unpin
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

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

function ModelCard({
  model,
  assignedRoles,
  promoted,
  pins,
  onTogglePromote,
  onOpenPin,
  promoteBusy,
}: {
  model: LlmModel;
  assignedRoles: string[];
  promoted: boolean;
  pins: { role: string; cost_mode: string }[];
  onTogglePromote: (model: string) => void;
  onOpenPin: (model: string) => void;
  promoteBusy: boolean;
}) {
  const tier = (model.tier as string) ?? 'unknown';
  return (
    <div
      className={`bg-[#0a0e14] border rounded-lg p-3 ${
        promoted ? 'border-[#34d399]/50 ring-1 ring-[#34d399]/30' : 'border-[#1e2738]'
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="min-w-0 flex-1">
          <div className="text-sm text-[#e2e8f0] font-medium truncate flex items-center gap-1.5">
            {promoted && <span title="Promoted — resolver's first choice">🚀</span>}
            {model.name}
          </div>
          <div className="text-[10px] text-[#7a8599] truncate">
            {model.provider ?? '?'} · ctx {model.context ?? '?'}
          </div>
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
      {pins.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {pins.map((p, i) => (
            <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-[#fbbf24]/10 text-[#fbbf24] border border-[#fbbf24]/30 uppercase tracking-wider">
              📌 {p.role}·{p.cost_mode}
            </span>
          ))}
        </div>
      )}
      {model.description ? (
        <p className="text-[10px] text-[#7a8599] mt-2 line-clamp-2">{String(model.description)}</p>
      ) : null}
      {/* Actions */}
      <div className="flex gap-2 mt-3 pt-2 border-t border-[#1e2738]">
        <button
          onClick={() => onTogglePromote(model.name)}
          disabled={promoteBusy}
          className={
            promoted
              ? 'text-[10px] px-2 py-1 rounded bg-[#34d399]/20 border border-[#34d399]/40 text-[#34d399] hover:bg-[#34d399]/30 disabled:opacity-50'
              : 'text-[10px] px-2 py-1 rounded bg-[#0a0e14] border border-[#1e2738] text-[#7a8599] hover:border-[#34d399]/40 hover:text-[#34d399] disabled:opacity-50'
          }
          title={promoted ? 'Click to demote' : 'Promote — becomes resolver first choice where it fits'}
        >
          {promoted ? '🚀 promoted · demote' : 'promote'}
        </button>
        <button
          onClick={() => onOpenPin(model.name)}
          className="text-[10px] px-2 py-1 rounded bg-[#0a0e14] border border-[#1e2738] text-[#7a8599] hover:border-[#fbbf24]/40 hover:text-[#fbbf24]"
          title="Pin to a specific role + cost_mode — hard resolver override"
        >
          📌 pin to role
        </button>
      </div>
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

// ── Runtime mode switch ─────────────────────────────────────────────────────

interface ModePreset {
  key: LlmMode;
  label: string;
  icon: string;
  desc: string;
}

const MODE_PRESETS: ModePreset[] = [
  { key: 'free',      label: 'Free',      icon: '🆓', desc: 'Zero-cost only — local Ollama + OpenRouter free tier. Chooser picks per role within that pool.' },
  { key: 'hybrid',    label: 'Hybrid',    icon: '⚖️', desc: 'Default. LLM chooser picks the best model using its full algorithm. Cascades local → API → Claude.' },
  { key: 'insane',    label: 'Insane',    icon: '🚀', desc: 'Premium-only. Chooser picks the strongest model for each role (Opus / Gemini 3.1 Pro / ...).' },
  { key: 'anthropic', label: 'Anthropic', icon: '🅰️', desc: 'Anthropic provider only. Chooser picks the best Anthropic model per role from strengths map.' },
];

function ModeSwitch() {
  const modeQ = useLlmModeQuery();
  const setMode = useSetLlmMode();
  const current = modeQ.data?.mode;
  const validModes = modeQ.data?.valid_modes ?? [];

  // Some modes (local, cloud) aren't exposed in the preset grid but can still
  // be active (e.g. set from Signal). Show them as a read-only chip when active.
  const activePreset = MODE_PRESETS.find((p) => p.key === current);
  const activeLegacy = current && !activePreset ? current : null;

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4 space-y-3">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-sm font-semibold text-[#e2e8f0]">Runtime Mode</h2>
          <p className="text-xs text-[#7a8599] mt-0.5">
            Constrains the candidate pool for every role. The LLM chooser still picks per-role within the mode.
          </p>
        </div>
        {modeQ.isLoading ? (
          <span className="text-xs text-[#7a8599]">loading…</span>
        ) : modeQ.error ? (
          <span className="text-xs text-[#f87171]">{(modeQ.error as Error).message}</span>
        ) : (
          <span className="text-xs text-[#7a8599]">
            active: <span className="text-[#60a5fa] font-medium">{current ?? '?'}</span>
          </span>
        )}
      </div>

      {activeLegacy && (
        <div className="text-[10px] text-[#fbbf24]">
          Current mode <code>{activeLegacy}</code> isn't one of the presets below.
          Switching to a preset will replace it.
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
        {MODE_PRESETS.map((p) => {
          const active = current === p.key;
          const disabled = validModes.length > 0 && !validModes.includes(p.key);
          return (
            <button
              key={p.key}
              type="button"
              disabled={disabled || setMode.isPending}
              onClick={() => setMode.mutate(p.key)}
              className={`text-left p-3 rounded-lg border transition-colors ${
                active
                  ? 'border-[#60a5fa] bg-[#60a5fa]/10 text-[#e2e8f0]'
                  : 'border-[#1e2738] bg-[#0a0e14] text-[#7a8599] hover:border-[#60a5fa]/40 hover:text-[#e2e8f0]'
              } ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base">{p.icon}</span>
                <span className="text-sm font-semibold">{p.label}</span>
                {active && (
                  <span className="ml-auto text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-[#34d399]/15 text-[#34d399]">
                    active
                  </span>
                )}
              </div>
              <p className="text-[11px] text-[#7a8599] leading-snug">{p.desc}</p>
            </button>
          );
        })}
      </div>

      {setMode.isError && (
        <p className="text-xs text-[#f87171]">Switch failed: {(setMode.error as Error).message}</p>
      )}
      <p className="text-[10px] text-[#7a8599]">
        Writes to <code>POST /config/llm_mode</code>. Requires <code>GATEWAY_SECRET</code> to be
        injected by the dashboard proxy (already wired in <code>server.mjs</code> / <code>vite.config.ts</code>).
      </p>
    </div>
  );
}

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

      <ModeSwitch />

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

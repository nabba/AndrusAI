import { useMemo, useState } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { Line, Bar, Doughnut } from 'react-chartjs-2';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useEvolutionSummaryQuery,
  useEvolutionResultsQuery,
  useEvolutionEngineQuery,
  useEvolutionVariantsQuery,
  useEvolutionVariantLineageQuery,
  type Variant,
} from '../api/queries';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler,
);

const CHART_COLORS = {
  blue: '#60a5fa',
  green: '#34d399',
  red: '#f87171',
  yellow: '#fbbf24',
  purple: '#a78bfa',
} as const;

const CHART_DEFAULTS = {
  plugins: {
    legend: { labels: { color: '#7a8599', font: { size: 12 } } },
    tooltip: {
      backgroundColor: '#111820',
      borderColor: '#1e2738',
      borderWidth: 1,
      titleColor: '#e2e8f0',
      bodyColor: '#7a8599',
    },
  },
  scales: {
    x: { ticks: { color: '#7a8599', font: { size: 11 } }, grid: { color: '#1e2738' } },
    y: { ticks: { color: '#7a8599', font: { size: 11 } }, grid: { color: '#1e2738' } },
  },
  responsive: true,
  maintainAspectRatio: false,
};

function StatCard({ label, value, sub, color = 'text-[#e2e8f0]' }: {
  label: string; value: string | number; sub?: string; color?: string;
}) {
  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
      <div className="text-xs text-[#7a8599] mb-1">{label}</div>
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
      {sub && <div className="text-xs text-[#7a8599] mt-1">{sub}</div>}
    </div>
  );
}

function EngineTag({ engine }: { engine: string }) {
  const styles: Record<string, string> = {
    avo: 'bg-[#60a5fa]/15 text-[#60a5fa] border-[#60a5fa]/30',
    shinka: 'bg-[#a78bfa]/15 text-[#a78bfa] border-[#a78bfa]/30',
    meta: 'bg-[#fbbf24]/15 text-[#fbbf24] border-[#fbbf24]/30',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${styles[engine] || 'bg-[#1e2738] text-[#7a8599] border-[#1e2738]'}`}>
      {engine.toUpperCase()}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    keep: 'bg-[#34d399]/15 text-[#34d399]',
    discard: 'bg-[#f87171]/15 text-[#f87171]',
    crash: 'bg-[#fbbf24]/15 text-[#fbbf24]',
    pending: 'bg-[#7a8599]/15 text-[#7a8599]',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-medium ${styles[status] || styles.pending}`}>
      {status}
    </span>
  );
}

function SafetyGauge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = value > 0.92 ? CHART_COLORS.green : value > 0.7 ? CHART_COLORS.blue : CHART_COLORS.red;
  const label = value > 0.92 ? 'Aggressive' : value > 0.7 ? 'Normal' : 'Conservative';
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 bg-[#1e2738] rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-medium" style={{ color }}>{pct}% {label}</span>
    </div>
  );
}

export function EvolutionMonitor() {
  const [tab, setTab] = useState<'overview' | 'history' | 'engines' | 'genealogy'>('overview');
  const [engineFilter, setEngineFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const summaryQ = useEvolutionSummaryQuery();
  const resultsQ = useEvolutionResultsQuery(engineFilter, statusFilter);
  const engineQ = useEvolutionEngineQuery();

  const summary = summaryQ.data;
  const resultsList = useMemo(() => resultsQ.data?.results ?? [], [resultsQ.data?.results]);
  const engineInfo = engineQ.data;
  const scoreTrend = summary?.score_trend;
  const engineStats = summary?.engines;

  const trendData = useMemo(() => {
    if (!scoreTrend) return null;
    return {
      labels: scoreTrend.map((_, i) => `${i + 1}`),
      datasets: [{
        label: 'Composite Score',
        data: scoreTrend,
        borderColor: CHART_COLORS.blue,
        backgroundColor: 'rgba(96,165,250,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 3,
      }],
    };
  }, [scoreTrend]);

  const deltaData = useMemo(() => {
    const recentDeltas = resultsList.slice(0, 30);
    if (recentDeltas.length === 0) return null;
    return {
      labels: recentDeltas.map((r) => r.experiment_id.split('_').pop()?.slice(0, 4) || ''),
      datasets: [{
        label: 'Delta',
        data: recentDeltas.map((r) => r.delta),
        backgroundColor: recentDeltas.map((r) =>
          r.status === 'keep' ? 'rgba(52,211,153,0.6)' :
          r.status === 'crash' ? 'rgba(251,191,36,0.6)' :
          'rgba(248,113,113,0.6)',
        ),
        borderWidth: 0,
        borderRadius: 2,
      }],
    };
  }, [resultsList]);

  const engineDoughnut = useMemo(() => {
    if (!engineStats) return null;
    return {
      labels: ['AVO', 'ShinkaEvolve', 'Meta-Evolution'],
      datasets: [{
        data: [
          engineStats.avo?.total || 0,
          engineStats.shinka?.total || 0,
          engineStats.meta?.total || 0,
        ],
        backgroundColor: [CHART_COLORS.blue, CHART_COLORS.purple, CHART_COLORS.yellow],
        borderColor: '#0a0e14',
        borderWidth: 2,
      }],
    };
  }, [engineStats]);

  const tabs = [
    { key: 'overview' as const, label: 'Overview' },
    { key: 'history' as const, label: 'Experiment History' },
    { key: 'engines' as const, label: 'Engine Analysis' },
    { key: 'genealogy' as const, label: '🧬 Genealogy' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-[#e2e8f0]">Evolution Monitor</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Self-improvement system: AVO pipeline + ShinkaEvolve island model + Meta-evolution
        </p>
      </div>

      <div className="flex gap-1 bg-[#111820] rounded-lg p-1 border border-[#1e2738] w-fit">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-1.5 rounded-md text-sm transition-colors ${
              tab === t.key
                ? 'bg-[#60a5fa]/15 text-[#60a5fa] font-medium'
                : 'text-[#7a8599] hover:text-[#e2e8f0]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {summaryQ.error && <ErrorPanel error={summaryQ.error} onRetry={summaryQ.refetch} />}

      {tab === 'overview' && (
        <>
          {summaryQ.isLoading ? (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
            </div>
          ) : summary ? (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              <StatCard label="Total Experiments" value={summary.total_experiments} />
              <StatCard label="Kept" value={summary.kept} color="text-[#34d399]" sub={`${(summary.kept_ratio * 100).toFixed(0)}% rate`} />
              <StatCard label="Discarded" value={summary.discarded} color="text-[#f87171]" />
              <StatCard label="Current Score" value={summary.current_score.toFixed(4)} color="text-[#60a5fa]" />
              <StatCard label="Best Score" value={summary.best_score.toFixed(4)} color="text-[#34d399]" />
              <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
                <div className="text-xs text-[#7a8599] mb-1">Active Engine</div>
                <div className="mt-1"><EngineTag engine={summary.current_engine} /></div>
                <div className="text-xs text-[#7a8599] mt-2">
                  Mode: {engineInfo?.config_mode || '?'}
                </div>
              </div>
            </div>
          ) : null}

          {summary && (
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <div className="text-xs text-[#7a8599] mb-2">SUBIA Homeostatic Safety (controls engine aggressiveness)</div>
              <SafetyGauge value={summary.subia_safety} />
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Score Trend (kept experiments)</h3>
              <div className="h-48">
                {trendData ? (
                  <Line data={trendData} options={{ ...CHART_DEFAULTS, plugins: { ...CHART_DEFAULTS.plugins, legend: { display: false } } }} />
                ) : (
                  <div className="h-full flex items-center justify-center text-[#7a8599] text-sm">No data</div>
                )}
              </div>
            </div>

            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Recent Experiment Deltas</h3>
              <div className="h-48">
                {deltaData ? (
                  <Bar data={deltaData} options={{ ...CHART_DEFAULTS, plugins: { ...CHART_DEFAULTS.plugins, legend: { display: false } } }} />
                ) : (
                  <div className="h-full flex items-center justify-center text-[#7a8599] text-sm">No data</div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {tab === 'history' && (
        <>
          <div className="flex gap-2 flex-wrap">
            <select
              value={engineFilter}
              onChange={(e) => setEngineFilter(e.target.value)}
              className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-1.5 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
            >
              <option value="">All Engines</option>
              <option value="avo">AVO</option>
              <option value="shinka">ShinkaEvolve</option>
              <option value="meta">Meta-Evolution</option>
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-1.5 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
            >
              <option value="">All Statuses</option>
              <option value="keep">Kept</option>
              <option value="discard">Discarded</option>
              <option value="crash">Crashed</option>
            </select>
          </div>

          {resultsQ.error && <ErrorPanel error={resultsQ.error} onRetry={resultsQ.refetch} />}

          <div className="bg-[#111820] border border-[#1e2738] rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#1e2738]">
                    <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599]">Time</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599]">Engine</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599]">Status</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599]">Delta</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599]">Type</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599]">Hypothesis</th>
                  </tr>
                </thead>
                <tbody>
                  {resultsQ.isLoading ? (
                    Array.from({ length: 5 }).map((_, i) => (
                      <tr key={i} className="border-b border-[#1e2738]/50">
                        <td colSpan={6} className="px-4 py-3"><Skeleton className="h-4 w-full" /></td>
                      </tr>
                    ))
                  ) : resultsList.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center text-[#7a8599]">
                        No experiments found
                      </td>
                    </tr>
                  ) : (
                    resultsList.map((r, i) => (
                      <tr key={i} className="border-b border-[#1e2738]/50 hover:bg-[#1e2738]/30 transition-colors">
                        <td className="px-4 py-2.5 text-[#7a8599] whitespace-nowrap">
                          {new Date(r.ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </td>
                        <td className="px-4 py-2.5"><EngineTag engine={r.engine} /></td>
                        <td className="px-4 py-2.5"><StatusBadge status={r.status} /></td>
                        <td className={`px-4 py-2.5 font-mono text-xs ${
                          r.delta > 0 ? 'text-[#34d399]' : r.delta < 0 ? 'text-[#f87171]' : 'text-[#7a8599]'
                        }`}>
                          {r.delta > 0 ? '+' : ''}{r.delta.toFixed(4)}
                        </td>
                        <td className="px-4 py-2.5 text-[#7a8599]">{r.change_type}</td>
                        <td className="px-4 py-2.5 text-[#e2e8f0] max-w-md truncate" title={r.hypothesis}>
                          {r.hypothesis}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {tab === 'engines' && summary && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {(['avo', 'shinka', 'meta'] as const).map((engine) => {
              const stat = summary.engines[engine];
              const isActive = summary.current_engine === engine;
              const ringColor = engine === 'avo' ? 'border-[#60a5fa]/50 ring-[#60a5fa]/20' :
                                 engine === 'shinka' ? 'border-[#a78bfa]/50 ring-[#a78bfa]/20' :
                                 'border-[#fbbf24]/50 ring-[#fbbf24]/20';
              const meta = ENGINE_META[engine];
              return (
                <div key={engine} className={`bg-[#111820] border rounded-xl p-5 ${isActive ? `${ringColor} ring-1` : 'border-[#1e2738]'}`}>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <EngineTag engine={engine} />
                      {isActive && <span className="text-[10px] text-[#34d399] font-medium">ACTIVE</span>}
                    </div>
                  </div>
                  <div className="text-xs text-[#7a8599] mb-3">{meta.subtitle}</div>
                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="text-[#7a8599]">{meta.countLabel}</span>
                      <span className="text-[#e2e8f0] font-medium">{stat?.total || 0}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-[#7a8599]">{meta.keptLabel}</span>
                      <span className="text-[#34d399] font-medium">{stat?.kept || 0}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-[#7a8599]">Success Rate</span>
                      <span className="text-[#e2e8f0] font-medium">{((stat?.kept_ratio || 0) * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                  <div className="mt-3 text-xs text-[#7a8599]">{meta.footer}</div>
                </div>
              );
            })}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Engine Usage Distribution</h3>
              <div className="h-48 flex items-center justify-center">
                {engineDoughnut && engineDoughnut.datasets[0].data.some((d) => d > 0) ? (
                  <Doughnut data={engineDoughnut} options={{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                      legend: { position: 'bottom' as const, labels: { color: '#7a8599', font: { size: 12 } } },
                      tooltip: CHART_DEFAULTS.plugins.tooltip,
                    },
                    cutout: '60%',
                  }} />
                ) : (
                  <div className="text-[#7a8599] text-sm">No engine data yet</div>
                )}
              </div>
            </div>

            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Auto-Selection Logic</h3>
              <div className="space-y-2 text-xs">
                {SELECTION_RULES.map((rule, i) => (
                  <div key={i} className="flex items-start gap-2 py-1.5 border-b border-[#1e2738]/50 last:border-0">
                    <span className="text-[#7a8599] w-40 flex-shrink-0">{rule.condition}</span>
                    <EngineTag engine={rule.engine} />
                    <span className="text-[#7a8599]">{rule.reason}</span>
                  </div>
                ))}
              </div>
              <div className="mt-3 pt-3 border-t border-[#1e2738] flex items-center gap-2">
                <span className="text-xs text-[#7a8599]">Current selection:</span>
                <EngineTag engine={summary.current_engine || 'avo'} />
                <span className="text-xs text-[#7a8599]">
                  (mode: {engineInfo?.config_mode || '?'}, shinka: {engineInfo?.shinka_available ? 'available' : 'unavailable'})
                </span>
              </div>
            </div>
          </div>
        </>
      )}

      {tab === 'genealogy' && <GenealogyTab />}
    </div>
  );
}

// ── Genealogy tab ───────────────────────────────────────────────────────────

const VARIANT_STATUS_STYLES: Record<string, string> = {
  keep:    'bg-[#34d399]/15 text-[#34d399]',
  discard: 'bg-[#f87171]/15 text-[#f87171]',
  crash:   'bg-[#fbbf24]/15 text-[#fbbf24]',
};

function VariantLineagePanel({ id }: { id: string | null }) {
  const { data, isLoading } = useEvolutionVariantLineageQuery(id);
  if (!id) {
    return (
      <div className="text-xs text-[#7a8599] italic p-4">
        Select a variant on the left to view its ancestry chain.
      </div>
    );
  }
  if (isLoading) return <Skeleton className="h-40 m-4" />;
  const lineage = data?.lineage ?? [];
  if (lineage.length === 0) {
    return <div className="text-xs text-[#7a8599] italic p-4">No lineage data for this variant.</div>;
  }
  return (
    <div className="p-4 space-y-3">
      <div className="text-xs text-[#7a8599] uppercase tracking-wider">
        Ancestry — {lineage.length} generation{lineage.length === 1 ? '' : 's'}
      </div>
      <div className="space-y-2">
        {lineage.map((v, i) => (
          <div key={v.id} className="flex items-start gap-2">
            <span className="text-[10px] text-[#7a8599] w-12 flex-shrink-0 pt-1">
              {i === 0 ? 'ROOT' : `G${v.generation ?? i}`}
            </span>
            <div className="flex-1 bg-[#0a0e14] border border-[#1e2738] rounded-md p-2">
              <div className="flex items-center gap-2 mb-1">
                {v.status && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider font-medium ${VARIANT_STATUS_STYLES[v.status] ?? 'bg-[#7a8599]/15 text-[#7a8599]'}`}>
                    {v.status}
                  </span>
                )}
                {v.delta != null && (
                  <span className={`text-[10px] font-mono ${v.delta > 0 ? 'text-[#34d399]' : v.delta < 0 ? 'text-[#f87171]' : 'text-[#7a8599]'}`}>
                    {v.delta > 0 ? '+' : ''}{v.delta.toFixed(4)}
                  </span>
                )}
                <span className="text-[10px] text-[#7a8599] truncate">{v.change_type ?? '—'}</span>
              </div>
              <div className="text-xs text-[#e2e8f0] line-clamp-2">{v.hypothesis ?? '—'}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GenealogyTab() {
  const { data, isLoading, error, refetch } = useEvolutionVariantsQuery(50);
  const [selected, setSelected] = useState<string | null>(null);

  if (isLoading) return <Skeleton className="h-64" />;
  if (error) return <ErrorPanel error={error} onRetry={refetch} />;

  const variants = data?.variants ?? [];
  const drift = data?.drift_score ?? 0;
  const kept = variants.filter((v) => v.status === 'keep').length;
  const maxGen = variants.reduce((m, v) => Math.max(m, v.generation ?? 0), 0);

  return (
    <div className="space-y-4">
      {data?.error && <div className="text-xs text-[#fbbf24]">{data.error}</div>}

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-2">
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">Drift</div>
          <div className="text-xl font-bold text-[#60a5fa]">{drift}</div>
          <div className="text-[10px] text-[#7a8599]">mutations from root</div>
        </div>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">Max Gen</div>
          <div className="text-xl font-bold text-[#a78bfa]">G{maxGen}</div>
        </div>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">Kept</div>
          <div className="text-xl font-bold text-[#34d399]">{kept}</div>
          <div className="text-[10px] text-[#7a8599]">of {variants.length}</div>
        </div>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">Archive</div>
          <div className="text-xl font-bold text-[#e2e8f0]">{variants.length}</div>
          <div className="text-[10px] text-[#7a8599]">recent variants</div>
        </div>
      </div>

      {variants.length === 0 ? (
        <p className="text-sm text-[#7a8599] italic">No variants archived yet.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-[#111820] border border-[#1e2738] rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-[#1e2738] text-xs text-[#7a8599] uppercase tracking-wider">
              Variants (click to view lineage)
            </div>
            <div className="divide-y divide-[#1e2738] max-h-[500px] overflow-y-auto">
              {variants.map((v) => (
                <VariantListRow
                  key={v.id}
                  v={v}
                  selected={selected === v.id}
                  onSelect={() => setSelected(v.id)}
                />
              ))}
            </div>
          </div>
          <div className="bg-[#111820] border border-[#1e2738] rounded-xl">
            <VariantLineagePanel id={selected} />
          </div>
        </div>
      )}
    </div>
  );
}

function VariantListRow({ v, selected, onSelect }: { v: Variant; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full text-left px-3 py-2 transition-colors ${
        selected ? 'bg-[#60a5fa]/10' : 'hover:bg-[#1e2738]/50'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[10px] bg-[#a78bfa]/15 text-[#a78bfa] border border-[#a78bfa]/30 px-1.5 py-0.5 rounded font-medium">
          G{v.generation ?? 0}
        </span>
        {v.status && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider font-medium ${VARIANT_STATUS_STYLES[v.status] ?? 'bg-[#7a8599]/15 text-[#7a8599]'}`}>
            {v.status}
          </span>
        )}
        {v.delta != null && (
          <span className={`text-[10px] font-mono ${v.delta > 0 ? 'text-[#34d399]' : v.delta < 0 ? 'text-[#f87171]' : 'text-[#7a8599]'}`}>
            {v.delta > 0 ? '+' : ''}{v.delta.toFixed(4)}
          </span>
        )}
        {v.test_pass_rate != null && (
          <span className="text-[10px] text-[#7a8599]">
            tests {(v.test_pass_rate * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <div className="text-xs text-[#e2e8f0] line-clamp-2">{v.hypothesis ?? '—'}</div>
      {v.parent_id && v.parent_id !== 'root' && (
        <div className="text-[10px] text-[#7a8599] mt-1 font-mono truncate">← {v.parent_id}</div>
      )}
    </button>
  );
}

const ENGINE_META = {
  avo: {
    subtitle: '5-phase pipeline: Plan, Implement, Test, Critique, Submit',
    countLabel: 'Experiments',
    keptLabel: 'Kept',
    footer: 'Best for: targeted bug fixes, error-driven improvements',
  },
  shinka: {
    subtitle: 'Island-model MAP-Elites with LLM-generated patches',
    countLabel: 'Experiments',
    keptLabel: 'Kept',
    footer: 'Best for: breaking stagnation, population diversity',
  },
  meta: {
    subtitle: "Second-order: evolves the evolution engine's parameters",
    countLabel: 'Cycles',
    keptLabel: 'Promoted',
    footer: 'Runs at 1/5 frequency, 3/week limit, 3-consecutive-win gate',
  },
} as const;

const SELECTION_RULES = [
  { condition: 'SUBIA safety < 0.70', engine: 'avo', reason: 'Conservative: single-mutation safer' },
  { condition: '5 consecutive failures', engine: 'shinka', reason: 'Stagnation: population diversity' },
  { condition: 'Kept ratio > 60%', engine: 'avo', reason: 'Working well, keep going' },
  { condition: 'Kept ratio < 20%', engine: 'shinka', reason: 'Too ambitious, try islands' },
  { condition: '3+ undiagnosed errors', engine: 'avo', reason: 'Has error context for targeting' },
  { condition: 'Every 4th session', engine: 'shinka', reason: 'Diversity rotation' },
] as const;

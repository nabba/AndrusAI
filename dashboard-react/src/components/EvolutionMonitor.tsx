import { useState } from 'react';
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
import { useApi } from '../hooks/useApi';

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, ArcElement, Title, Tooltip, Legend, Filler
);

// ── Types ───────────────────────────────────────────────────────────────────

interface EvolutionResult {
  ts: string;
  experiment_id: string;
  hypothesis: string;
  change_type: string;
  status: string;
  delta: number;
  metric_before: number;
  metric_after: number;
  detail: string;
  engine: string;
  files_changed: string[];
}

interface EngineStat {
  total: number;
  kept: number;
  kept_ratio: number;
}

interface EvolutionSummary {
  total_experiments: number;
  kept: number;
  discarded: number;
  crashed: number;
  kept_ratio: number;
  best_score: number;
  current_score: number;
  score_trend: number[];
  current_engine: string;
  subia_safety: number;
  engines: Record<string, EngineStat>;
}

interface EngineInfo {
  config_mode: string;
  selected_engine: string;
  shinka_available: boolean;
}

// ── Chart config ────────────────────────────────────────────────────────────

const CHART_COLORS = {
  blue: '#60a5fa',
  green: '#34d399',
  red: '#f87171',
  yellow: '#fbbf24',
  purple: '#a78bfa',
  cyan: '#22d3ee',
};

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

// ── Helpers ─────────────────────────────────────────────────────────────────

function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse bg-[#1e2738] rounded ${className}`} />;
}

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
  const color = value > 0.92 ? CHART_COLORS.green : value > 0.70 ? CHART_COLORS.blue : CHART_COLORS.red;
  const label = value > 0.92 ? 'Aggressive' : value > 0.70 ? 'Normal' : 'Conservative';
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 bg-[#1e2738] rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-medium" style={{ color }}>{pct}% {label}</span>
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────

export function EvolutionMonitor() {
  const [tab, setTab] = useState<'overview' | 'history' | 'engines'>('overview');
  const [engineFilter, setEngineFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const { data: summary, loading: summaryLoading } = useApi<EvolutionSummary>('/evolution/summary', 15000);
  const { data: resultsData, loading: resultsLoading } = useApi<{ results: EvolutionResult[] }>(
    `/evolution/results?limit=100${engineFilter ? `&engine=${engineFilter}` : ''}${statusFilter ? `&status=${statusFilter}` : ''}`,
    15000
  );
  const { data: engineInfo } = useApi<EngineInfo>('/evolution/engine', 30000);

  const results = resultsData?.results ?? [];

  // ── Score trend chart data ────────────────────────────────────────────
  const trendData = summary?.score_trend
    ? {
        labels: summary.score_trend.map((_, i) => `${i + 1}`),
        datasets: [{
          label: 'Composite Score',
          data: summary.score_trend,
          borderColor: CHART_COLORS.blue,
          backgroundColor: 'rgba(96,165,250,0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        }],
      }
    : null;

  // ── Delta distribution chart ──────────────────────────────────────────
  const recentDeltas = results.slice(0, 30);
  const deltaData = recentDeltas.length > 0
    ? {
        labels: recentDeltas.map(r => r.experiment_id.split('_').pop()?.slice(0, 4) || ''),
        datasets: [{
          label: 'Delta',
          data: recentDeltas.map(r => r.delta),
          backgroundColor: recentDeltas.map(r =>
            r.status === 'keep' ? 'rgba(52,211,153,0.6)' :
            r.status === 'crash' ? 'rgba(251,191,36,0.6)' :
            'rgba(248,113,113,0.6)'
          ),
          borderWidth: 0,
          borderRadius: 2,
        }],
      }
    : null;

  // ── Engine distribution doughnut ──────────────────────────────────────
  const engineDoughnut = summary?.engines
    ? {
        labels: ['AVO', 'ShinkaEvolve', 'Meta-Evolution'],
        datasets: [{
          data: [
            summary.engines.avo?.total || 0,
            summary.engines.shinka?.total || 0,
            summary.engines.meta?.total || 0,
          ],
          backgroundColor: [CHART_COLORS.blue, CHART_COLORS.purple, CHART_COLORS.yellow],
          borderColor: '#0a0e14',
          borderWidth: 2,
        }],
      }
    : null;

  // ── Tabs ──────────────────────────────────────────────────────────────

  const tabs = [
    { key: 'overview' as const, label: 'Overview' },
    { key: 'history' as const, label: 'Experiment History' },
    { key: 'engines' as const, label: 'Engine Analysis' },
  ];

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-xl font-bold text-[#e2e8f0]">Evolution Monitor</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Self-improvement system: AVO pipeline + ShinkaEvolve island model + Meta-evolution
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 bg-[#111820] rounded-lg p-1 border border-[#1e2738] w-fit">
        {tabs.map(t => (
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

      {/* ── OVERVIEW TAB ─────────────────────────────────────────────────── */}
      {tab === 'overview' && (
        <>
          {/* Stat cards */}
          {summaryLoading ? (
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

          {/* SUBIA Safety */}
          {summary && (
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <div className="text-xs text-[#7a8599] mb-2">SUBIA Homeostatic Safety (controls engine aggressiveness)</div>
              <SafetyGauge value={summary.subia_safety} />
            </div>
          )}

          {/* Charts row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Score trend */}
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Score Trend (kept experiments)</h3>
              <div className="h-48">
                {trendData ? (
                  <Line data={trendData} options={{ ...CHART_DEFAULTS, plugins: { ...CHART_DEFAULTS.plugins, legend: { display: false } } } as any} />
                ) : (
                  <div className="h-full flex items-center justify-center text-[#7a8599] text-sm">No data</div>
                )}
              </div>
            </div>

            {/* Delta distribution */}
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Recent Experiment Deltas</h3>
              <div className="h-48">
                {deltaData ? (
                  <Bar data={deltaData} options={{ ...CHART_DEFAULTS, plugins: { ...CHART_DEFAULTS.plugins, legend: { display: false } } } as any} />
                ) : (
                  <div className="h-full flex items-center justify-center text-[#7a8599] text-sm">No data</div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* ── HISTORY TAB ──────────────────────────────────────────────────── */}
      {tab === 'history' && (
        <>
          {/* Filters */}
          <div className="flex gap-2 flex-wrap">
            <select
              value={engineFilter}
              onChange={e => setEngineFilter(e.target.value)}
              className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-1.5 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
            >
              <option value="">All Engines</option>
              <option value="avo">AVO</option>
              <option value="shinka">ShinkaEvolve</option>
              <option value="meta">Meta-Evolution</option>
            </select>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-1.5 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
            >
              <option value="">All Statuses</option>
              <option value="keep">Kept</option>
              <option value="discard">Discarded</option>
              <option value="crash">Crashed</option>
            </select>
          </div>

          {/* Results table */}
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
                  {resultsLoading ? (
                    Array.from({ length: 5 }).map((_, i) => (
                      <tr key={i} className="border-b border-[#1e2738]/50">
                        <td colSpan={6} className="px-4 py-3"><Skeleton className="h-4 w-full" /></td>
                      </tr>
                    ))
                  ) : results.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center text-[#7a8599]">
                        No experiments found
                      </td>
                    </tr>
                  ) : (
                    results.map((r, i) => (
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

      {/* ── ENGINES TAB ──────────────────────────────────────────────────── */}
      {tab === 'engines' && summary && (
        <>
          {/* Engine comparison cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* AVO */}
            <div className={`bg-[#111820] border rounded-xl p-5 ${
              summary.current_engine === 'avo' ? 'border-[#60a5fa]/50 ring-1 ring-[#60a5fa]/20' : 'border-[#1e2738]'
            }`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <EngineTag engine="avo" />
                  {summary.current_engine === 'avo' && (
                    <span className="text-[10px] text-[#34d399] font-medium">ACTIVE</span>
                  )}
                </div>
              </div>
              <div className="text-xs text-[#7a8599] mb-3">5-phase pipeline: Plan, Implement, Test, Critique, Submit</div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Experiments</span>
                  <span className="text-[#e2e8f0] font-medium">{summary.engines.avo?.total || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Kept</span>
                  <span className="text-[#34d399] font-medium">{summary.engines.avo?.kept || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Success Rate</span>
                  <span className="text-[#e2e8f0] font-medium">{((summary.engines.avo?.kept_ratio || 0) * 100).toFixed(0)}%</span>
                </div>
              </div>
              <div className="mt-3 text-xs text-[#7a8599]">
                Best for: targeted bug fixes, error-driven improvements
              </div>
            </div>

            {/* ShinkaEvolve */}
            <div className={`bg-[#111820] border rounded-xl p-5 ${
              summary.current_engine === 'shinka' ? 'border-[#a78bfa]/50 ring-1 ring-[#a78bfa]/20' : 'border-[#1e2738]'
            }`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <EngineTag engine="shinka" />
                  {summary.current_engine === 'shinka' && (
                    <span className="text-[10px] text-[#34d399] font-medium">ACTIVE</span>
                  )}
                </div>
              </div>
              <div className="text-xs text-[#7a8599] mb-3">Island-model MAP-Elites with LLM-generated patches</div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Experiments</span>
                  <span className="text-[#e2e8f0] font-medium">{summary.engines.shinka?.total || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Kept</span>
                  <span className="text-[#34d399] font-medium">{summary.engines.shinka?.kept || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Success Rate</span>
                  <span className="text-[#e2e8f0] font-medium">{((summary.engines.shinka?.kept_ratio || 0) * 100).toFixed(0)}%</span>
                </div>
              </div>
              <div className="mt-3 text-xs text-[#7a8599]">
                Best for: breaking stagnation, population diversity
              </div>
            </div>

            {/* Meta-Evolution */}
            <div className={`bg-[#111820] border rounded-xl p-5 ${
              summary.current_engine === 'meta' ? 'border-[#fbbf24]/50 ring-1 ring-[#fbbf24]/20' : 'border-[#1e2738]'
            }`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <EngineTag engine="meta" />
                </div>
              </div>
              <div className="text-xs text-[#7a8599] mb-3">Second-order: evolves the evolution engine's parameters</div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Cycles</span>
                  <span className="text-[#e2e8f0] font-medium">{summary.engines.meta?.total || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Promoted</span>
                  <span className="text-[#34d399] font-medium">{summary.engines.meta?.kept || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-[#7a8599]">Success Rate</span>
                  <span className="text-[#e2e8f0] font-medium">{((summary.engines.meta?.kept_ratio || 0) * 100).toFixed(0)}%</span>
                </div>
              </div>
              <div className="mt-3 text-xs text-[#7a8599]">
                Runs at 1/5 frequency, 3/week limit, 3-consecutive-win gate
              </div>
            </div>
          </div>

          {/* Engine distribution chart */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Engine Usage Distribution</h3>
              <div className="h-48 flex items-center justify-center">
                {engineDoughnut && (engineDoughnut.datasets[0].data.some(d => d > 0)) ? (
                  <Doughnut data={engineDoughnut} options={{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                      legend: { position: 'bottom' as const, labels: { color: '#7a8599', font: { size: 12 } } },
                      tooltip: CHART_DEFAULTS.plugins.tooltip as any,
                    },
                    cutout: '60%',
                  }} />
                ) : (
                  <div className="text-[#7a8599] text-sm">No engine data yet</div>
                )}
              </div>
            </div>

            {/* Engine selection logic */}
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <h3 className="text-sm font-medium text-[#e2e8f0] mb-3">Auto-Selection Logic</h3>
              <div className="space-y-2 text-xs">
                {[
                  { condition: 'SUBIA safety < 0.70', result: 'AVO', reason: 'Conservative: single-mutation safer' },
                  { condition: '5 consecutive failures', result: 'ShinkaEvolve', reason: 'Stagnation: population diversity' },
                  { condition: 'Kept ratio > 60%', result: 'AVO', reason: 'Working well, keep going' },
                  { condition: 'Kept ratio < 20%', result: 'ShinkaEvolve', reason: 'Too ambitious, try islands' },
                  { condition: '3+ undiagnosed errors', result: 'AVO', reason: 'Has error context for targeting' },
                  { condition: 'Every 4th session', result: 'ShinkaEvolve', reason: 'Diversity rotation' },
                ].map((rule, i) => (
                  <div key={i} className="flex items-start gap-2 py-1.5 border-b border-[#1e2738]/50 last:border-0">
                    <span className="text-[#7a8599] w-40 flex-shrink-0">{rule.condition}</span>
                    <EngineTag engine={rule.result.toLowerCase().replace('shinkaevolve', 'shinka')} />
                    <span className="text-[#7a8599]">{rule.reason}</span>
                  </div>
                ))}
              </div>
              <div className="mt-3 pt-3 border-t border-[#1e2738] flex items-center gap-2">
                <span className="text-xs text-[#7a8599]">Current selection:</span>
                <EngineTag engine={summary?.current_engine || 'avo'} />
                <span className="text-xs text-[#7a8599]">
                  (mode: {engineInfo?.config_mode || '?'}, shinka: {engineInfo?.shinka_available ? 'available' : 'unavailable'})
                </span>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

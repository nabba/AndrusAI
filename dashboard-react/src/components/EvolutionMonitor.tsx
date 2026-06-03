import { useMemo, useState } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useEvolutionSummaryQuery,
  useEvolutionResultsQuery,
  useEvolutionVariantsQuery,
  type Variant,
} from '../api/queries';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
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

export function EvolutionMonitor() {
  const [tab, setTab] = useState<'overview' | 'history' | 'selfmods'>('overview');
  const [statusFilter, setStatusFilter] = useState('');

  const summaryQ = useEvolutionSummaryQuery();
  const resultsQ = useEvolutionResultsQuery(statusFilter);

  const summary = summaryQ.data;
  // Chronological (oldest → newest) — used by left-to-right charts.
  const resultsList = useMemo(() => resultsQ.data?.results ?? [], [resultsQ.data?.results]);
  // Newest-first — used by the history list so the top row is the most recent.
  const resultsListDesc = useMemo(
    () => [...(resultsQ.data?.results ?? [])].reverse(),
    [resultsQ.data?.results],
  );
  const scoreTrend = summary?.score_trend;

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

  const tabs = [
    { key: 'overview' as const, label: 'Overview' },
    { key: 'history' as const, label: 'Experiment History' },
    { key: 'selfmods' as const, label: 'Self-modifications' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-[#e2e8f0]">Evolution Monitor</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Self-improvement via the verified mutation engine — execution-verified, operator-gated self-modifications
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
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
            </div>
          ) : summary ? (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
              <StatCard label="Total Experiments" value={summary.total_experiments} />
              <StatCard label="Kept" value={summary.kept} color="text-[#34d399]" sub={`${(summary.kept_ratio * 100).toFixed(0)}% rate`} />
              <StatCard label="Discarded" value={summary.discarded} color="text-[#f87171]" />
              <StatCard label="Current Score" value={summary.current_score.toFixed(4)} color="text-[#60a5fa]" />
              <StatCard label="Best Score" value={summary.best_score.toFixed(4)} color="text-[#34d399]" />
            </div>
          ) : null}

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
                        <td colSpan={5} className="px-4 py-3"><Skeleton className="h-4 w-full" /></td>
                      </tr>
                    ))
                  ) : resultsListDesc.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-[#7a8599]">
                        No experiments found
                      </td>
                    </tr>
                  ) : (
                    resultsListDesc.map((r, i) => (
                      <tr key={i} className="border-b border-[#1e2738]/50 hover:bg-[#1e2738]/30 transition-colors">
                        <td className="px-4 py-2.5 text-[#7a8599] whitespace-nowrap">
                          {new Date(r.ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </td>
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

      {tab === 'selfmods' && <SelfModificationsTab />}
    </div>
  );
}

// ── Self-modifications tab ───────────────────────────────────────────────────
// Verified, operator-gated change-requests the system applied to (or rolled
// back from) its own code. Sourced from /variants → app/self_improvement/
// history.py. No fabricated fitness deltas: the signal is applied vs rolled-back.

const SELFMOD_STATUS: Record<string, { label: string; cls: string }> = {
  keep:    { label: 'applied',     cls: 'bg-[#34d399]/15 text-[#34d399]' },
  discard: { label: 'rolled back', cls: 'bg-[#f87171]/15 text-[#f87171]' },
};

function SelfModificationsTab() {
  const { data, isLoading, error, refetch } = useEvolutionVariantsQuery(50);

  if (isLoading) return <Skeleton className="h-64" />;
  if (error) return <ErrorPanel error={error} onRetry={refetch} />;

  // history.py returns newest-first (CRs sorted by created_at desc).
  const mods = data?.variants ?? [];
  const applied = mods.filter((v) => v.status === 'keep').length;
  const rolledBack = mods.filter((v) => v.status === 'discard').length;

  return (
    <div className="space-y-4">
      {data?.error && <div className="text-xs text-[#fbbf24]">{data.error}</div>}

      <p className="text-sm text-[#7a8599]">
        Verified self-modifications — operator-gated change-requests the system applied to its
        own code, or rolled back. No fabricated fitness deltas; the signal is the real
        applied-vs-rolled-back outcome.
      </p>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">Applied</div>
          <div className="text-xl font-bold text-[#34d399]">{applied}</div>
        </div>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">Rolled back</div>
          <div className="text-xl font-bold text-[#f87171]">{rolledBack}</div>
        </div>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">Total shown</div>
          <div className="text-xl font-bold text-[#e2e8f0]">{mods.length}</div>
        </div>
      </div>

      {mods.length === 0 ? (
        <p className="text-sm text-[#7a8599] italic">No verified self-modifications yet.</p>
      ) : (
        <div className="bg-[#111820] border border-[#1e2738] rounded-xl overflow-hidden">
          <div className="px-3 py-2 border-b border-[#1e2738] text-xs text-[#7a8599] uppercase tracking-wider">
            Recent self-modifications
          </div>
          <div className="divide-y divide-[#1e2738] max-h-[600px] overflow-y-auto">
            {mods.map((v) => (
              <SelfModRow key={v.id} v={v} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SelfModRow({ v }: { v: Variant }) {
  const meta = SELFMOD_STATUS[v.status ?? ''] ?? {
    label: v.status ?? 'pending',
    cls: 'bg-[#7a8599]/15 text-[#7a8599]',
  };
  const files = v.files_changed ?? [];
  return (
    <div className="px-3 py-2.5">
      <div className="flex items-center gap-2 mb-1">
        <span className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider font-medium ${meta.cls}`}>
          {meta.label}
        </span>
        {v.timestamp && (
          <span className="text-[10px] text-[#7a8599]">
            {new Date(v.timestamp).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
        {v.id && <span className="text-[10px] text-[#7a8599] font-mono truncate">{v.id}</span>}
      </div>
      <div className="text-xs text-[#e2e8f0] line-clamp-2">{v.hypothesis || '—'}</div>
      {files.length > 0 && (
        <div className="text-[10px] text-[#7a8599] mt-1 font-mono truncate">{files.join(', ')}</div>
      )}
    </div>
  );
}

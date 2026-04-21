import { useMemo } from 'react';
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
import { useDailyCostsQuery, useAgentCostsQuery } from '../api/queries';
import { useProject } from '../context/useProject';
import { TokenUsageCard } from './TokenUsageCard';
import { MonthlyProjectionCard } from './MonthlyProjectionCard';

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

const CHART_DEFAULTS = {
  plugins: {
    legend: {
      labels: { color: '#7a8599', font: { size: 12 } },
    },
    tooltip: {
      backgroundColor: '#111820',
      borderColor: '#1e2738',
      borderWidth: 1,
      titleColor: '#e2e8f0',
      bodyColor: '#7a8599',
    },
  },
  scales: {
    x: {
      ticks: { color: '#7a8599', font: { size: 11 } },
      grid: { color: '#1e2738' },
    },
    y: {
      ticks: { color: '#7a8599', font: { size: 11 } },
      grid: { color: '#1e2738' },
    },
  },
  responsive: true,
  maintainAspectRatio: false,
};

const BAR_COLORS = [
  'rgba(96, 165, 250, 0.7)',
  'rgba(52, 211, 153, 0.7)',
  'rgba(167, 139, 250, 0.7)',
  'rgba(251, 191, 36, 0.7)',
  'rgba(248, 113, 113, 0.7)',
  'rgba(251, 146, 60, 0.7)',
  'rgba(34, 211, 238, 0.7)',
  'rgba(244, 114, 182, 0.7)',
];
const BAR_BORDERS = ['#60a5fa', '#34d399', '#a78bfa', '#fbbf24', '#f87171', '#fb923c', '#22d3ee', '#f472b6'];

export function CostCharts() {
  const { activeProject, isAllProjects } = useProject();
  const dailyQ = useDailyCostsQuery(30, activeProject?.id);
  const agentsQ = useAgentCostsQuery(activeProject?.id);

  const agentCosts = useMemo(() => agentsQ.data?.by_actor ?? [], [agentsQ.data?.by_actor]);

  const dailyChartData = useMemo(() => {
    if (!dailyQ.data || dailyQ.data.length === 0) return null;
    // Backend returns newest-first; reverse so the chart runs
    // left → right = oldest → newest.
    const chronological = [...dailyQ.data].reverse();
    return {
      labels: chronological.map((c) => {
        const d = new Date(c.day);
        return `${d.getMonth() + 1}/${d.getDate()}`;
      }),
      datasets: [
        {
          label: 'Daily Cost ($)',
          data: chronological.map((c) => c.total_cost ?? 0),
          borderColor: '#60a5fa',
          backgroundColor: 'rgba(96, 165, 250, 0.1)',
          pointBackgroundColor: '#60a5fa',
          pointRadius: 3,
          fill: true,
          tension: 0.4,
        },
      ],
    };
  }, [dailyQ.data]);

  const agentChartData = useMemo(() => {
    if (agentCosts.length === 0) return null;
    return {
      labels: agentCosts.map((c) => c.actor ?? '?'),
      datasets: [
        {
          label: 'Total Cost ($)',
          data: agentCosts.map((c) => c.total_cost ?? 0),
          backgroundColor: BAR_COLORS,
          borderColor: BAR_BORDERS,
          borderWidth: 1,
          borderRadius: 4,
        },
      ],
    };
  }, [agentCosts]);

  const totalCost = useMemo(() => agentCosts.reduce((s, c) => s + (c.total_cost ?? 0), 0), [agentCosts]);
  const topAgent = useMemo(
    () => (agentCosts.length ? agentCosts.reduce((top, c) => ((c.total_cost ?? 0) > (top.total_cost ?? 0) ? c : top), agentCosts[0]) : null),
    [agentCosts],
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Cost Analytics</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          {isAllProjects ? 'All projects' : activeProject ? `Project: ${activeProject.name}` : 'All projects'}
          <span className="opacity-60"> · spending trends and per-agent breakdown</span>
        </p>
        <p className="text-[10px] text-[#7a8599] mt-0.5 opacity-60">
          Token-level telemetry follows the selector. Pre-migration rows have no project tag and appear only under "All projects".
        </p>
      </div>

      <MonthlyProjectionCard />

      <TokenUsageCard />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
          <div className="text-xs text-[#7a8599] mb-1">Total Spend (all time)</div>
          {agentsQ.isLoading ? (
            <Skeleton className="h-7 w-24" />
          ) : (
            <div className="text-xl font-bold text-[#34d399]">${totalCost.toFixed(4)}</div>
          )}
        </div>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
          <div className="text-xs text-[#7a8599] mb-1">Top Spending Agent</div>
          {agentsQ.isLoading ? (
            <Skeleton className="h-7 w-24" />
          ) : (
            <div className="text-xl font-bold text-[#60a5fa]">{topAgent?.actor ?? '—'}</div>
          )}
        </div>
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
          <div className="text-xs text-[#7a8599] mb-1">Active Agents</div>
          {agentsQ.isLoading ? (
            <Skeleton className="h-7 w-16" />
          ) : (
            <div className="text-xl font-bold text-[#a78bfa]">{agentCosts.length}</div>
          )}
        </div>
      </div>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
        <h2 className="text-sm font-medium text-[#e2e8f0] mb-4">Daily Costs — Last 30 Days</h2>
        {dailyQ.isLoading ? (
          <Skeleton className="h-64" />
        ) : dailyQ.error ? (
          <ErrorPanel error={dailyQ.error} onRetry={dailyQ.refetch} />
        ) : !dailyChartData ? (
          <div className="h-64 flex items-center justify-center text-[#7a8599] text-sm">
            No cost data available.
          </div>
        ) : (
          <div className="h-64">
            <Line
              data={dailyChartData}
              options={{
                ...CHART_DEFAULTS,
                plugins: {
                  ...CHART_DEFAULTS.plugins,
                  legend: { display: false },
                  tooltip: {
                    ...CHART_DEFAULTS.plugins.tooltip,
                    callbacks: {
                      label: (ctx) => ` $${(ctx.raw as number).toFixed(4)}`,
                    },
                  },
                },
              }}
            />
          </div>
        )}
      </div>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
        <h2 className="text-sm font-medium text-[#e2e8f0] mb-4">Cost by Agent</h2>
        {agentsQ.isLoading ? (
          <Skeleton className="h-64" />
        ) : agentsQ.error ? (
          <ErrorPanel error={agentsQ.error} onRetry={agentsQ.refetch} />
        ) : !agentChartData ? (
          <div className="h-64 flex items-center justify-center text-[#7a8599] text-sm">
            No agent cost data available.
          </div>
        ) : (
          <div className="h-64">
            <Bar
              data={agentChartData}
              options={{
                ...CHART_DEFAULTS,
                plugins: {
                  ...CHART_DEFAULTS.plugins,
                  legend: { display: false },
                  tooltip: {
                    ...CHART_DEFAULTS.plugins.tooltip,
                    callbacks: {
                      label: (ctx) => ` $${(ctx.raw as number).toFixed(4)}`,
                    },
                  },
                },
                scales: {
                  ...CHART_DEFAULTS.scales,
                  x: {
                    ...CHART_DEFAULTS.scales.x,
                    ticks: {
                      ...CHART_DEFAULTS.scales.x.ticks,
                      maxRotation: 45,
                    },
                  },
                },
              }}
            />
          </div>
        )}
      </div>

      {!agentsQ.isLoading && agentCosts.length > 0 && (
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#1e2738]">
                <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Agent</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Total Cost</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Calls</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Avg/Call</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1e2738]">
              {[...agentCosts]
                .sort((a, b) => b.total_cost - a.total_cost)
                .map((ac) => (
                  <tr key={ac.actor} className="hover:bg-[#1e2738]/50 transition-colors">
                    <td className="px-4 py-2.5 text-[#e2e8f0]">{ac.actor}</td>
                    <td className="px-4 py-2.5 text-right text-[#34d399]">${ac.total_cost.toFixed(4)}</td>
                    <td className="px-4 py-2.5 text-right text-[#7a8599]">{ac.calls ?? '—'}</td>
                    <td className="px-4 py-2.5 text-right text-[#7a8599]">
                      {ac.calls && ac.calls > 0 ? `$${(ac.total_cost / ac.calls).toFixed(4)}` : '—'}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

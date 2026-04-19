import { useMemo, useState } from 'react';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import { useTokenUsageQuery, type TokenPeriod, type TokenStat } from '../api/queries';
import { useProject } from '../context/useProject';

const PERIODS: { value: TokenPeriod; label: string }[] = [
  { value: 'hour', label: 'Last Hour' },
  { value: 'day', label: 'Today' },
  { value: 'week', label: 'This Week' },
  { value: 'month', label: 'This Month' },
  { value: 'year', label: 'Year' },
];

function fmtNum(n: number): string {
  return n.toLocaleString();
}

function sumTotals(rows: TokenStat[]) {
  return rows.reduce(
    (acc, r) => {
      acc.tokens += r.total || 0;
      acc.prompt += r.prompt_tokens || 0;
      acc.completion += r.completion_tokens || 0;
      acc.cost += r.cost_usd || 0;
      acc.calls += r.calls || 0;
      return acc;
    },
    { tokens: 0, prompt: 0, completion: 0, cost: 0, calls: 0 },
  );
}

export function TokenUsageCard() {
  const [period, setPeriod] = useState<TokenPeriod>('day');
  const { activeProject } = useProject();
  const { data, isLoading, error, refetch } = useTokenUsageQuery(activeProject?.id);

  const rows = useMemo(() => data?.stats?.[period] ?? [], [data, period]);
  const totals = useMemo(() => sumTotals(rows), [rows]);

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-[#7a8599] uppercase tracking-wider">
          Token Usage & Cost
        </h2>
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value as TokenPeriod)}
          className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-1.5 text-xs text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </select>
      </div>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4 space-y-4">
        {isLoading ? (
          <Skeleton className="h-32" />
        ) : error ? (
          <ErrorPanel error={error} onRetry={refetch} />
        ) : data?.error ? (
          <div className="text-xs text-[#f87171]">{data.error}</div>
        ) : rows.length === 0 ? (
          <div className="text-sm text-[#7a8599] italic">No token usage recorded for this period.</div>
        ) : (
          <>
            {/* Summary headline */}
            <div className="flex items-end justify-between">
              <div>
                <div className="text-[11px] text-[#7a8599] uppercase tracking-wider">Total tokens</div>
                <div className="text-2xl font-bold text-[#60a5fa]">{fmtNum(totals.tokens)}</div>
                <div className="text-[10px] text-[#7a8599] mt-0.5">
                  {fmtNum(totals.prompt)} in · {fmtNum(totals.completion)} out
                </div>
              </div>
              <div className="text-right">
                <div className="text-[11px] text-[#7a8599] uppercase tracking-wider">Cost</div>
                <div className="text-2xl font-bold text-[#34d399]">${totals.cost.toFixed(4)}</div>
                <div className="text-[10px] text-[#7a8599] mt-0.5">{fmtNum(totals.calls)} calls</div>
              </div>
            </div>

            {/* Per-model table */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#1e2738]">
                    <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Model</th>
                    <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Input</th>
                    <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Output</th>
                    <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Total</th>
                    <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Cost</th>
                    <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Calls</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1e2738]">
                  {rows.map((r) => (
                    <tr key={r.model} className="hover:bg-[#1e2738]/50 transition-colors">
                      <td className="px-3 py-2 text-[#e2e8f0]">{r.model}</td>
                      <td className="px-3 py-2 text-right text-[#7a8599]">{fmtNum(r.prompt_tokens)}</td>
                      <td className="px-3 py-2 text-right text-[#7a8599]">{fmtNum(r.completion_tokens)}</td>
                      <td className="px-3 py-2 text-right text-[#e2e8f0]">{fmtNum(r.total)}</td>
                      <td className="px-3 py-2 text-right text-[#34d399]">
                        {r.cost_usd > 0 ? `$${r.cost_usd.toFixed(4)}` : '—'}
                      </td>
                      <td className="px-3 py-2 text-right text-[#7a8599]">{fmtNum(r.calls)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

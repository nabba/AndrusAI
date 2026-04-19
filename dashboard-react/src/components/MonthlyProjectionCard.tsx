import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import { useTokenUsageQuery } from '../api/queries';
import { useProject } from '../context/useProject';
import { crewIcon, crewLabel } from '../crews';

function fmtNum(n: number | undefined): string {
  return (n ?? 0).toLocaleString();
}

function Metric({ label, value, color = 'text-[#e2e8f0]' }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] text-[#7a8599] uppercase tracking-wider">{label}</div>
      <div className={`text-base font-semibold mt-0.5 ${color}`}>{value}</div>
    </div>
  );
}

export function MonthlyProjectionCard() {
  const { activeProject } = useProject();
  const { data, isLoading, error, refetch } = useTokenUsageQuery(activeProject?.id);

  const projection = data?.projection;
  const reqDay = data?.request_costs?.day;

  return (
    <section>
      <h2 className="text-sm font-medium text-[#7a8599] uppercase tracking-wider mb-3">
        Monthly Cost Projection
      </h2>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4 space-y-5">
        {isLoading ? (
          <Skeleton className="h-24" />
        ) : error ? (
          <ErrorPanel error={error} onRetry={refetch} />
        ) : data?.error ? (
          <div className="text-xs text-[#f87171]">{data.error}</div>
        ) : (
          <>
            {/* Projection headline */}
            <div className="flex items-end justify-between gap-4">
              <div>
                <div className="text-2xl font-bold text-[#34d399]">
                  ${(projection?.projected_monthly_usd ?? 0).toFixed(2)}<span className="text-sm font-normal text-[#7a8599]">/mo</span>
                </div>
                <div className="text-[10px] text-[#7a8599] mt-1">
                  Today: ${(projection?.day_cost_usd ?? 0).toFixed(4)} · MTD: ${(projection?.mtd_cost_usd ?? 0).toFixed(4)}
                </div>
                <div className="text-[10px] text-[#7a8599]">
                  Simple linear extrapolation: today × 30
                </div>
              </div>
            </div>

            {/* Per-request cost row */}
            <div>
              <h3 className="text-xs font-medium text-[#7a8599] uppercase tracking-wider mb-2">
                Per-Request Cost (Today)
              </h3>
              {!reqDay || reqDay.requests === 0 ? (
                <p className="text-sm text-[#7a8599] italic">No completed requests today.</p>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <Metric label="Requests" value={fmtNum(reqDay.requests)} />
                  <Metric
                    label="Avg Cost"
                    value={`$${reqDay.avg_cost_usd.toFixed(6)}`}
                    color="text-[#34d399]"
                  />
                  <Metric label="Avg Calls" value={reqDay.avg_calls.toFixed(1)} />
                  <Metric label="Avg Tokens" value={fmtNum(Math.round(reqDay.avg_tokens))} />
                </div>
              )}
            </div>

            {/* By-crew breakdown (today) */}
            {data?.by_crew?.day && data.by_crew.day.length > 0 && (
              <div>
                <h3 className="text-xs font-medium text-[#7a8599] uppercase tracking-wider mb-2">
                  Today by Crew
                </h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[#1e2738]">
                        <th className="text-left px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Crew</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Requests</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Total Cost</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Avg Cost</th>
                        <th className="text-right px-3 py-2 text-xs font-medium text-[#7a8599] uppercase tracking-wider">Avg Tokens</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#1e2738]">
                      {data.by_crew.day.map((c) => (
                        <tr key={c.crew} className="hover:bg-[#1e2738]/50 transition-colors">
                          <td className="px-3 py-2 text-[#e2e8f0]">
                            <span className="mr-1.5">{crewIcon(c.crew)}</span>
                            {crewLabel(c.crew)}
                          </td>
                          <td className="px-3 py-2 text-right text-[#7a8599]">{fmtNum(c.requests)}</td>
                          <td className="px-3 py-2 text-right text-[#34d399]">${c.total_cost_usd.toFixed(4)}</td>
                          <td className="px-3 py-2 text-right text-[#7a8599]">${c.avg_cost_usd.toFixed(6)}</td>
                          <td className="px-3 py-2 text-right text-[#7a8599]">{fmtNum(Math.round(c.avg_tokens))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

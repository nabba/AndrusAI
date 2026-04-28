import type { CalibrationHistoryReport } from '../../types/affect';

interface CalibrationHistoryProps {
  report: CalibrationHistoryReport;
}

const STATUS_STYLE: Record<string, { color: string; bg: string }> = {
  applied: { color: '#34d399', bg: '#34d3991a' },
  rejected: { color: '#f87171', bg: '#f871711a' },
  deferred: { color: '#fbbf24', bg: '#fbbf241a' },
  no_change: { color: '#7a8599', bg: '#7a85991a' },
};

function fmtTs(ts: string): string {
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

function fmtDelta(delta: number): string {
  const sign = delta >= 0 ? '+' : '';
  return `${sign}${delta.toFixed(3)}`;
}

export function CalibrationHistory({ report }: CalibrationHistoryProps) {
  const { history, current_setpoints, ratchet_state } = report;
  const sorted = [...history].sort((a, b) => (a.ts > b.ts ? -1 : 1));

  const ratchetEntries = Object.entries(ratchet_state ?? {}).filter(
    ([, v]) => (v?.loosen_streak ?? 0) > 0,
  );

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-xs text-[#7a8599] uppercase tracking-wider">Calibration history</div>
        <div className="text-xs text-[#7a8599]">
          {history.length} entries · {Object.keys(current_setpoints).length} variables tracked
        </div>
      </div>

      {ratchetEntries.length > 0 ? (
        <div className="rounded bg-[#0a0e14] border border-[#1e2738] p-2 mb-3">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mb-1">Ratchet pending</div>
          <div className="flex flex-wrap gap-1">
            {ratchetEntries.map(([variable, info]) => (
              <span
                key={variable}
                className="text-[10px] px-1.5 py-0.5 rounded font-mono text-[#fbbf24] bg-[#fbbf241a]"
                title={`Last loosen proposal ${info?.last_loosen_proposal_ts ?? '?'}`}
              >
                {variable} ×{info?.loosen_streak ?? 0}
              </span>
            ))}
          </div>
          <div className="text-[10px] text-[#7a8599] mt-1.5 italic">
            Loosen proposals require 3 consecutive passes + 2× evidence to apply.
          </div>
        </div>
      ) : null}

      {sorted.length === 0 ? (
        <div className="text-sm text-[#7a8599]">No calibration cycles run yet.</div>
      ) : (
        <div className="space-y-2 max-h-[420px] overflow-y-auto">
          {sorted.map((entry, i) => {
            const style = STATUS_STYLE[entry.status] ?? STATUS_STYLE.no_change;
            const deltas = Object.entries(entry.delta ?? {});
            return (
              <div
                key={`${entry.ts}-${i}`}
                className="rounded-lg border border-[#1e2738] bg-[#0a0e14] p-3"
              >
                <div className="flex items-baseline justify-between gap-3 mb-1">
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-mono"
                    style={{ color: style.color, background: style.bg }}
                  >
                    {entry.status.toUpperCase()}
                  </span>
                  <span className="text-[11px] text-[#7a8599]">{fmtTs(entry.ts)}</span>
                </div>
                {entry.reason ? (
                  <div className="text-[12px] text-[#7a8599] italic mb-1">{entry.reason}</div>
                ) : null}
                {deltas.length > 0 ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 text-[11px] font-mono">
                    {deltas.map(([variable, d]) => (
                      <div key={variable} className="flex justify-between gap-2">
                        <span className="text-[#e2e8f0] truncate">{variable}</span>
                        <span className="text-[#7a8599] whitespace-nowrap">
                          {d.old.toFixed(3)} → {d.new.toFixed(3)}
                          <span className={d.delta >= 0 ? 'text-[#34d399]' : 'text-[#fb923c]'}> ({fmtDelta(d.delta)})</span>
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

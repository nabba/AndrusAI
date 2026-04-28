import type { WelfareBreach } from '../../types/affect';

interface WelfareAuditLogProps {
  breaches: WelfareBreach[];
}

const SEVERITY_STYLE: Record<string, { color: string; bg: string; label: string }> = {
  info: { color: '#60a5fa', bg: '#60a5fa1a', label: 'INFO' },
  warn: { color: '#fbbf24', bg: '#fbbf241a', label: 'WARN' },
  critical: { color: '#f87171', bg: '#f871711a', label: 'CRITICAL' },
};

function fmtTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function fmtKind(k: string): string {
  return k.replace(/_/g, ' ');
}

export function WelfareAuditLog({ breaches }: WelfareAuditLogProps) {
  if (!breaches.length) {
    return (
      <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5">
        <div className="text-xs text-[#7a8599] uppercase tracking-wider mb-2">
          Welfare audit
        </div>
        <div className="flex items-center gap-2 text-sm text-[#34d399]">
          <span>·</span>
          <span>No welfare breaches recorded.</span>
        </div>
        <div className="text-[11px] text-[#7a8599] mt-1">
          Hard envelope healthy: max negative valence duration, variance floor, drift detector all
          within bounds.
        </div>
      </div>
    );
  }

  const sorted = [...breaches].sort((a, b) => (a.ts > b.ts ? -1 : 1));

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-xs text-[#7a8599] uppercase tracking-wider">Welfare audit</div>
        <div className="text-xs text-[#7a8599]">{breaches.length} entries</div>
      </div>
      <div className="space-y-2 max-h-[420px] overflow-y-auto">
        {sorted.map((b, i) => {
          const sev = SEVERITY_STYLE[b.severity] ?? SEVERITY_STYLE.warn;
          return (
            <div
              key={`${b.ts}-${i}`}
              className="rounded-lg border border-[#1e2738] bg-[#0a0e14] p-3"
            >
              <div className="flex items-baseline justify-between gap-3 mb-1">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-mono"
                    style={{ color: sev.color, background: sev.bg }}
                  >
                    {sev.label}
                  </span>
                  <span className="text-sm font-medium text-[#e2e8f0] truncate">
                    {fmtKind(b.kind)}
                  </span>
                </div>
                <span className="text-[11px] text-[#7a8599] whitespace-nowrap">
                  {fmtTs(b.ts)}
                </span>
              </div>
              <div className="text-sm text-[#7a8599]">{b.message}</div>
              {b.measured_value !== null || b.threshold !== null ? (
                <div className="text-[11px] font-mono text-[#7a8599] mt-1">
                  {b.measured_value !== null ? `measured ${b.measured_value.toFixed(3)}` : ''}
                  {b.measured_value !== null && b.threshold !== null ? ' · ' : ''}
                  {b.threshold !== null ? `threshold ${b.threshold.toFixed(3)}` : ''}
                  {b.duration_seconds !== null ? ` · ${b.duration_seconds.toFixed(0)}s` : ''}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

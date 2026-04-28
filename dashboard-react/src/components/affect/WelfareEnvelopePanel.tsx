import { useState } from 'react';
import { useWelfareConfigQuery } from '../../api/affect';
import { Skeleton } from '../ui/Skeleton';

function formatValue(v: number): string {
  if (Number.isInteger(v) && v >= 1) return String(v);
  return v.toFixed(v < 1 ? 3 : 2);
}

export function WelfareEnvelopePanel() {
  const q = useWelfareConfigQuery();
  const [open, setOpen] = useState(false);

  if (q.isLoading) return <Skeleton className="h-12" />;
  if (q.isError || !q.data) {
    return (
      <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-3 text-sm text-[#f87171]">
        Could not load welfare config.
      </div>
    );
  }

  const { hard_envelope, descriptions } = q.data;
  const entries = Object.entries(hard_envelope);

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-baseline justify-between p-4 text-left hover:bg-[#1e2738]/30 rounded-lg transition-colors"
      >
        <div>
          <div className="text-xs text-[#7a8599] uppercase tracking-wider">Welfare envelope</div>
          <div className="text-[11px] text-[#7a8599] mt-1">
            {entries.length} infrastructure-level constants · file-edit only · click to {open ? 'hide' : 'show'}
          </div>
        </div>
        <span className="text-[#7a8599]">{open ? '▾' : '▸'}</span>
      </button>
      {open ? (
        <div className="px-4 pb-4 space-y-1.5">
          {entries.map(([k, v]) => (
            <div
              key={k}
              className="flex items-baseline justify-between gap-3 py-1 border-b border-[#1e2738]/50 last:border-0"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-mono text-[#e2e8f0]">{k}</div>
                <div className="text-[10px] text-[#7a8599] mt-0.5">
                  {descriptions[k] ?? ''}
                </div>
              </div>
              <div className="text-sm font-mono text-[#60a5fa] whitespace-nowrap">
                {formatValue(v)}
              </div>
            </div>
          ))}
          <div className="text-[10px] text-[#7a8599] italic mt-3">
            These constants are NOT modifiable by the Self-Improver, the calibration cycle, or
            this dashboard. Editing them requires direct file edit of <code className="px-1 bg-[#1e2738] rounded">app/affect/welfare.py</code>.
          </div>
        </div>
      ) : null}
    </div>
  );
}

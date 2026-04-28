import { useEffect, useState } from 'react';
import { useApplySetpoints } from '../../api/affect';
import type { ViabilityFrame } from '../../types/affect';

interface SetpointEditorProps {
  /** Current viability frame (for current setpoints + display labels). */
  viability: ViabilityFrame;
}

const VARIABLE_LABELS: Record<string, string> = {
  compute_reserve: 'Compute reserve',
  latency_pressure: 'Latency pressure',
  memory_pressure: 'Memory pressure',
  epistemic_uncertainty: 'Epistemic uncertainty',
  attachment_security: 'Attachment security',
  autonomy: 'Autonomy',
  task_coherence: 'Task coherence',
  novelty_pressure: 'Novelty pressure',
  ecological_connectedness: 'Ecological connectedness',
  self_continuity: 'Self continuity',
};

export function SetpointEditor({ viability }: SetpointEditorProps) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState('');
  const [edits, setEdits] = useState<Record<string, number>>({});
  const apply = useApplySetpoints();

  // Reset local edits when the upstream setpoints change (e.g., calibration cycle applied).
  useEffect(() => {
    setEdits({});
  }, [JSON.stringify(viability.setpoints)]);

  const setpoints = viability.setpoints;
  const variables = Object.keys(setpoints);

  const dirty = Object.keys(edits).length > 0;

  const handleChange = (variable: string, value: number) => {
    const original = setpoints[variable];
    if (Math.abs(value - original) < 0.001) {
      setEdits((prev) => {
        const { [variable]: _drop, ...rest } = prev;
        return rest;
      });
    } else {
      setEdits((prev) => ({ ...prev, [variable]: value }));
    }
  };

  const handleApply = () => {
    if (!token || !dirty) return;
    apply.mutate({ setpoints: edits, overrideToken: token });
  };

  const handleReset = () => {
    setEdits({});
    apply.reset();
  };

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-baseline justify-between p-4 text-left hover:bg-[#1e2738]/30 rounded-lg transition-colors"
      >
        <div>
          <div className="text-xs text-[#7a8599] uppercase tracking-wider">Setpoint editor (manual override)</div>
          <div className="text-[11px] text-[#7a8599] mt-1">
            Soft-envelope adjust · clamped to [0.05, 0.95] · auth-gated · click to {open ? 'hide' : 'show'}
          </div>
        </div>
        <span className="text-[#7a8599]">{open ? '▾' : '▸'}</span>
      </button>

      {open ? (
        <div className="px-4 pb-4 space-y-3">
          <div className="text-[11px] text-[#7a8599] italic">
            Setpoints normally adapt via the daily reflection cycle's 6-guardrail flow. Manual
            overrides bypass the ratchet — use sparingly. Hard envelope (welfare bounds, attachment
            caps) is unaffected.
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {variables.map((v) => {
              const current = setpoints[v];
              const proposed = edits[v];
              const value = proposed ?? current;
              const changed = proposed !== undefined;
              return (
                <div key={v} className="rounded bg-[#0a0e14] border border-[#1e2738] p-2.5">
                  <div className="flex items-baseline justify-between mb-1">
                    <span className="text-[12px] text-[#e2e8f0]">{VARIABLE_LABELS[v] ?? v}</span>
                    <span className="text-[12px] font-mono">
                      {changed ? (
                        <>
                          <span className="text-[#7a8599]">{current.toFixed(2)} →</span>{' '}
                          <span className="text-[#fbbf24]">{value.toFixed(2)}</span>
                        </>
                      ) : (
                        <span className="text-[#e2e8f0]">{current.toFixed(2)}</span>
                      )}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0.05}
                    max={0.95}
                    step={0.01}
                    value={value}
                    onChange={(e) => handleChange(v, parseFloat(e.target.value))}
                    className="w-full h-1.5 accent-[#60a5fa]"
                  />
                </div>
              );
            })}
          </div>

          <div className="rounded bg-[#0a0e14] border border-[#1e2738] p-2.5 space-y-2">
            <label className="block text-[11px] text-[#7a8599] uppercase tracking-wider">
              Gateway override token (X-Override-Token)
            </label>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="required to apply"
              className="w-full px-2.5 py-1.5 rounded bg-[#0a0e14] border border-[#1e2738] text-[12px] text-[#e2e8f0] font-mono focus:outline-none focus:border-[#60a5fa]"
            />

            {apply.isError ? (
              <div className="text-[11px] text-[#f87171] font-mono break-all">
                {(apply.error as Error)?.message ?? 'Apply failed.'}
              </div>
            ) : null}

            {apply.data ? (
              <div className="text-[11px] text-[#34d399] font-mono">
                {apply.data.status === 'applied'
                  ? `Applied ${Object.keys(apply.data.setpoints_applied ?? {}).length} setpoint(s).`
                  : `${apply.data.status}`}
                {apply.data.rejected && Object.keys(apply.data.rejected).length > 0 ? (
                  <div className="text-[#f87171] mt-1">
                    Rejected: {Object.entries(apply.data.rejected).map(([k, r]) => `${k} (${r})`).join(', ')}
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={handleReset}
                disabled={!dirty && !apply.data}
                className="text-[12px] px-3 py-1.5 rounded text-[#7a8599] hover:text-[#e2e8f0] hover:bg-[#1e2738] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Reset edits
              </button>
              <button
                type="button"
                onClick={handleApply}
                disabled={!dirty || !token || apply.isPending}
                className="text-[12px] px-3 py-1.5 rounded font-mono text-[#60a5fa] bg-[#60a5fa1a] border border-[#60a5fa]/30 hover:bg-[#60a5fa]/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {apply.isPending ? 'Applying…' : `Apply ${Object.keys(edits).length} change${Object.keys(edits).length === 1 ? '' : 's'}`}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

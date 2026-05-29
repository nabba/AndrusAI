// Autonomous executor master switch.
//
// When ON, the `autonomous-executor` HEAVY idle-scheduler tuple advances
// delegated runs one step per tick: it picks the most-recently-touched
// active run and dispatches it through the Commander adapter (or, for
// self-improvement runs, the verified-mutation-engine adapter). When OFF
// (default), `run_executor_tick` returns immediately — runs filed via
// /delegate stay in CREATED status and never advance.
//
// World-affecting and self-mutating steps still terminate at the existing
// operator gates (change-requests, external-action gate); this switch only
// controls whether the scheduler is allowed to drive runs forward at all.

import { api } from '../api/client';
import type { RuntimeSettings } from '../api/queries';
import { useState } from 'react';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const WARN_BG = '#7f1d1d22';
const ACCENT = '#34d399';
const ACCENT_BG = '#064e3b22';

export function AutonomousExecutorCard({
  settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const update = async (patch: Record<string, unknown>) => {
    setError(null);
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify(patch),
      });
      onSettingsChange();
    } catch (e) {
      setError(String(e));
    }
  };

  const enabled = settings.autonomous_executor_enabled === true;

  return (
    <div
      className="rounded-lg p-4 border space-y-3"
      style={{ background: '#111820', borderColor: '#1e2738' }}
    >
      <div>
        <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Autonomous executor
        </h2>
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Drives delegated runs (filed via <code>/delegate</code> or the
          Delegate page) forward one step per scheduler tick. While OFF, those
          runs land in <code>CREATED</code> and never advance. World-affecting
          and self-mutating steps still pass through the existing operator gates
          (change-requests, external-action gate) — this only controls whether
          the scheduler may advance runs at all.
        </p>
      </div>

      {error && (
        <div
          className="text-xs p-2 rounded"
          style={{ color: WARN, background: WARN_BG }}
        >
          {error}
        </div>
      )}

      {enabled && (
        <div
          className="text-[10px] p-2 rounded"
          style={{ color: ACCENT, background: ACCENT_BG }}
        >
          ON — the scheduler will advance active delegate runs autonomously.
          Per-run hard ceilings ($10 / 200k tokens / 1h) still apply.
        </div>
      )}

      <Toggle
        label="Autonomous executor (master)"
        checked={enabled}
        onChange={(v) => update({ autonomous_executor_enabled: v })}
        caveat="Default OFF. When ON, the autonomous-executor scheduler tuple advances delegated runs without prompting per-step; gated steps still require your approval."
      />
    </div>
  );
}

function Toggle({
  label,
  checked,
  onChange,
  caveat,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  caveat?: string;
}) {
  return (
    <div>
      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span style={{ color: TEXT_BRIGHT, fontWeight: 500 }}>{label}</span>
      </label>
      {caveat && (
        <p className="text-[10px] ml-5 mt-0.5" style={{ color: TEXT_DIM }}>
          {caveat}
        </p>
      )}
    </div>
  );
}

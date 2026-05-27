// Verified mutation engine master switch (2026-05-27).
//
// When ON, evolution.py's legacy AVO/experiment_runner code+skill mutation
// path is HARD-CUT. Self-improvement instead grounds in code_intel, implements
// in a real git worktree (ephemeral evolver container), proves improvement by
// EXECUTION (module imports, covering tests green, public API preserved, a
// held-out benchmark beaten beyond noise), then files an operator-gated
// change-request. Nothing auto-deploys. The judge (worktree_eval) is
// TIER_IMMUTABLE.
//
// Default OFF. Requires the evolver image first:
//   docker compose --profile evolver build evolver

import { api } from '../api/client';
import type { RuntimeSettings } from '../api/queries';
import { useState } from 'react';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const WARN_BG = '#7f1d1d22';
const ACCENT = '#34d399';
const ACCENT_BG = '#064e3b22';

export function VerifiedEngineCard({
  settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [budget, setBudget] = useState<string>(
    String(settings.evolution_verified_per_cycle_budget_usd ?? 5)
  );

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

  const enabled = settings.evolution_verified_engine_enabled === true;

  return (
    <div
      className="rounded-lg p-4 border space-y-3"
      style={{ background: '#111820', borderColor: '#1e2738' }}
    >
      <div>
        <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Verified mutation engine (2026-05-27)
        </h2>
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Replaces the legacy self-improvement loop. A self-modification is
          treated as a coding task on itself: it grounds in{' '}
          <code>code_intel</code>, edits in a real git worktree inside an
          ephemeral evolver container, and must PROVE improvement by execution
          (module imports, covering tests green, public API preserved, a
          held-out benchmark beaten beyond noise) before a change-request is
          filed for your approval. The judge (<code>worktree_eval</code>) is
          TIER_IMMUTABLE; nothing auto-deploys.
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
          ON — the legacy AVO/experiment_runner mutation path is hard-cut.
          Requires the evolver image:{' '}
          <code>docker compose --profile evolver build evolver</code>.
        </div>
      )}

      <Toggle
        label="Verified mutation engine (master)"
        checked={enabled}
        onChange={(v) => update({ evolution_verified_engine_enabled: v })}
        caveat="Default OFF. When ON, the old code+skill mutation path is disabled entirely; self-improvement runs through ground → implement-in-worktree → prove → operator-gated CR. Build the evolver image first."
      />

      <div>
        <label
          className="flex items-center gap-2 text-xs"
          style={{ color: TEXT_BRIGHT, fontWeight: 500 }}
        >
          Per-cycle budget (USD)
          <input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            onBlur={() =>
              update({
                evolution_verified_per_cycle_budget_usd: parseFloat(budget) || 0,
              })
            }
            className="w-20 px-1 py-0.5 rounded text-xs"
            style={{
              background: '#0b1118',
              color: TEXT_BRIGHT,
              border: '1px solid #1e2738',
            }}
          />
        </label>
        <p className="text-[10px] mt-0.5" style={{ color: TEXT_DIM }}>
          Hard cap per self-improvement cycle (LLM editor + judge + any benchmark
          runs in the evolver). Clamped server-side to [0, 100].
        </p>
      </div>
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

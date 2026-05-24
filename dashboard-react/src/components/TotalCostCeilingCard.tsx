// Total monthly cost ceiling — Gap #2 (2026-05-24).
//
// Aggregates spend across every subsystem (LLM cascade, ConnectorBudget,
// embedding costs, vision CU, U5 capability adoption, …) into a single
// monthly figure. Two thresholds:
//   80% → warning Signal alert (one-shot per calendar month).
//   95% → critical alert + engage the brake (MEDIUM + HEAVY idle jobs
//         skip until spend drops below 70%).
//
// LIGHT idle jobs (observability/reconciler work) continue regardless —
// the brake is for the expensive paths, not the cheap ones.

import { useEffect, useState } from 'react';
import { api } from '../api/client';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const TEXT_OK = '#86efac';
const TEXT_WARN = '#fbbf24';
const TEXT_BAD = '#f87171';
const BORDER = '#1e2738';

type CeilingState = {
  available: boolean;
  reason?: string;
  spend_usd?: number;
  cap_usd: number;
  pct?: number;
  level?: 'ok' | 'warn' | 'brake';
  brake_engaged: boolean;
  projected_end_of_month_usd?: number;
  day_of_month?: number;
  days_in_month?: number;
  as_of: string;
};

const SETTINGS_KEY = 'total_cost_monthly_cap_usd';

export function TotalCostCeilingCard() {
  const [state, setState] = useState<CeilingState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingCap, setPendingCap] = useState<string>('');

  const refresh = async () => {
    try {
      const resp = await api('/api/cp/budgets/total');
      setState(resp as CeilingState);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void refresh();
    const id = setInterval(refresh, 60_000);
    return () => clearInterval(id);
  }, []);

  const saveCap = async () => {
    const value = Number.parseFloat(pendingCap);
    if (!Number.isFinite(value) || value < 0 || value > 10000) {
      setError('Cap must be a non-negative number ≤ $10,000.');
      return;
    }
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify({
          [SETTINGS_KEY]: value,
          __reason__: `Adjusted monthly cap to $${value}`,
        }),
      });
      setPendingCap('');
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div
      className="rounded-lg p-4 border space-y-3"
      style={{ background: '#111820', borderColor: BORDER }}
    >
      <div>
        <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Monthly system cost ceiling
        </h2>
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Top-level cap across every subsystem (LLM cascade, ConnectorBudget,
          embeddings, vision CU, capability adoption, briefing evolution, …).
          Per-subsystem caps still apply; this is the guardrail above them.
          80% warns; 95% pauses MEDIUM+HEAVY idle jobs (LIGHT continues);
          releases at 70% (hysteresis).
        </p>
      </div>

      {error && (
        <div className="text-xs" style={{ color: TEXT_BAD }}>
          {error}
        </div>
      )}

      {state && !state.available && (
        <div className="text-xs" style={{ color: TEXT_WARN }}>
          Cost data unavailable: {state.reason ?? 'unknown'}. The brake state
          is still readable: {state.brake_engaged ? 'ENGAGED' : 'released'}.
        </div>
      )}

      {state && state.available && (
        <>
          <div className="space-y-1">
            <div className="flex justify-between items-end">
              <span className="text-2xl font-mono" style={{ color: TEXT_BRIGHT }}>
                ${state.spend_usd?.toFixed(2)} / ${state.cap_usd.toFixed(2)}
              </span>
              <span
                className="text-sm"
                style={{
                  color:
                    state.level === 'brake'
                      ? TEXT_BAD
                      : state.level === 'warn'
                      ? TEXT_WARN
                      : TEXT_OK,
                }}
              >
                {((state.pct ?? 0) * 100).toFixed(1)}%
              </span>
            </div>
            <div
              className="h-2 rounded-full overflow-hidden"
              style={{ background: '#0d141d' }}
            >
              <div
                className="h-full"
                style={{
                  width: `${Math.min(100, (state.pct ?? 0) * 100)}%`,
                  background:
                    state.level === 'brake'
                      ? TEXT_BAD
                      : state.level === 'warn'
                      ? TEXT_WARN
                      : TEXT_OK,
                }}
              />
            </div>
            <div className="text-[10px]" style={{ color: TEXT_DIM }}>
              Day {state.day_of_month} of {state.days_in_month} · projected
              end-of-month: ${state.projected_end_of_month_usd?.toFixed(2)}
            </div>
          </div>

          {state.brake_engaged && (
            <div
              className="text-xs p-2 rounded"
              style={{ color: TEXT_BAD, background: '#7f1d1d22' }}
            >
              🔴 Brake engaged — MEDIUM + HEAVY idle jobs paused. Releases
              automatically when spend drops below 70% of cap.
            </div>
          )}
        </>
      )}

      <div className="border-t pt-2" style={{ borderColor: BORDER }}>
        <label
          className="block text-[10px] mb-1"
          style={{ color: TEXT_DIM }}
        >
          Adjust monthly cap (USD)
        </label>
        <div className="flex gap-2">
          <input
            type="number"
            step="10"
            min="0"
            max="10000"
            value={pendingCap}
            placeholder={state ? state.cap_usd.toFixed(0) : '200'}
            onChange={(e) => setPendingCap(e.target.value)}
            className="flex-1 px-2 py-1 text-xs rounded border"
            style={{
              background: '#0d141d',
              borderColor: BORDER,
              color: TEXT_BRIGHT,
            }}
          />
          <button
            onClick={saveCap}
            disabled={!pendingCap}
            className="px-3 py-1 text-xs rounded"
            style={{
              background: pendingCap ? '#1e40af' : BORDER,
              color: pendingCap ? TEXT_BRIGHT : TEXT_DIM,
              cursor: pendingCap ? 'pointer' : 'not-allowed',
            }}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

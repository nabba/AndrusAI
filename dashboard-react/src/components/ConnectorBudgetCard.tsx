// Connector-budget card (2026-05-22).
//
// Operator visibility into today's spending across @with_connector_budget
// wrapped connectors. Dormant when the master switch is OFF or no
// spend has been recorded today — the card stays compact so it
// doesn't crowd the SettingsPage.

import { useState } from 'react';
import { api } from '../api/client';
import { useConnectorBudgetStateQuery } from '../api/connector_budget';
import type { RuntimeSettings } from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const WARN_BG = '#7f1d1d22';
const ACCENT_BG = '#1e2738';
const ACCENT_BORDER = '#2a3a52';
const OK = '#34d399';

export function ConnectorBudgetCard({
  settings,
  onSettingsChange,
}: {
  settings?: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const q = useConnectorBudgetStateQuery();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Form state for adding a new override
  const [newConnector, setNewConnector] = useState('');
  const [newCap, setNewCap] = useState('');
  const [newEstimate, setNewEstimate] = useState('');

  const state = q.data;
  const enabled = state?.enabled ?? false;
  const connectors = state?.connectors ?? [];
  const overrides = settings?.connector_budget_overrides ?? {};

  const toggle = async () => {
    setError(null);
    setBusy(true);
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify({ connector_budgets_enabled: !enabled }),
      });
      onSettingsChange();
      q.refetch();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // Override mutators — POST the full overrides map. The runtime
  // settings setter on the backend merges fields per connector;
  // we don't have to do that client-side.
  const writeOverrides = async (
    next: Record<string, { daily_cap_usd?: number; estimated_cost_usd?: number }>,
  ) => {
    setError(null);
    setBusy(true);
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify({ connector_budget_overrides: next }),
      });
      onSettingsChange();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const addOverride = async () => {
    const name = newConnector.trim();
    if (!name) {
      setError('Connector name cannot be empty');
      return;
    }
    const cap = newCap.trim() ? Number(newCap) : undefined;
    const est = newEstimate.trim() ? Number(newEstimate) : undefined;
    if (cap !== undefined && (!Number.isFinite(cap) || cap <= 0)) {
      setError('daily_cap_usd must be a positive number');
      return;
    }
    if (est !== undefined && (!Number.isFinite(est) || est < 0)) {
      setError('estimated_cost_usd must be ≥ 0');
      return;
    }
    if (cap === undefined && est === undefined) {
      setError('Provide at least one of daily_cap_usd or estimated_cost_usd');
      return;
    }
    const entry: { daily_cap_usd?: number; estimated_cost_usd?: number } = {
      ...(overrides[name] ?? {}),
    };
    if (cap !== undefined) entry.daily_cap_usd = cap;
    if (est !== undefined) entry.estimated_cost_usd = est;
    const next = { ...overrides, [name]: entry };
    await writeOverrides(next);
    setNewConnector('');
    setNewCap('');
    setNewEstimate('');
  };

  const removeOverride = async (name: string) => {
    const next = { ...overrides };
    delete next[name];
    await writeOverrides(next);
  };

  return (
    <div
      className="rounded-lg p-4 border"
      style={{
        background: ACCENT_BG,
        borderColor: ACCENT_BORDER,
      }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h3
            className="text-sm font-semibold"
            style={{ color: TEXT_BRIGHT }}
          >
            💸 Connector budgets
          </h3>
          <p className="text-xs mt-1" style={{ color: TEXT_DIM }}>
            Per-connector daily USD caps via the{' '}
            <code style={{ color: '#60a5fa' }}>@with_connector_budget</code>{' '}
            decorator. Wrapped connectors refuse new calls when today's
            spend would exceed the cap.
          </p>
        </div>
        <button
          disabled={busy}
          onClick={toggle}
          className="px-3 py-1 rounded text-xs font-medium border transition-colors disabled:opacity-50"
          style={{
            background: enabled ? OK + '22' : ACCENT_BG,
            color: enabled ? OK : TEXT_DIM,
            borderColor: enabled ? OK + '55' : ACCENT_BORDER,
          }}
        >
          {enabled ? '● ON' : '○ OFF'}
        </button>
      </div>

      {error && (
        <div
          className="text-xs rounded p-2 mb-3"
          style={{ background: WARN_BG, color: WARN }}
        >
          {error}
        </div>
      )}

      {!enabled ? (
        <div
          className="text-xs italic"
          style={{ color: TEXT_DIM }}
        >
          Switch is OFF — decorator is a pass-through. Spending is not
          recorded and caps are not enforced.
        </div>
      ) : connectors.length === 0 ? (
        <div
          className="text-xs italic"
          style={{ color: TEXT_DIM }}
        >
          No wrapped-connector calls today. Spending will appear here
          once a decorated function fires.
        </div>
      ) : (
        <>
          {/* Totals row */}
          <div
            className="grid grid-cols-2 gap-2 mb-3 text-xs"
            style={{ color: TEXT_DIM }}
          >
            <Stat
              label="Today total"
              value={`$${state?.total_usd.toFixed(4) ?? '0.0000'}`}
            />
            <Stat
              label="Total calls"
              value={String(state?.total_calls ?? 0)}
            />
          </div>

          {/* Per-connector table */}
          <div className="space-y-1">
            {connectors.map((c) => (
              <div
                key={c.connector}
                className="rounded p-2 text-xs flex items-center justify-between gap-2"
                style={{ background: '#0a0e14' }}
              >
                <div className="min-w-0 flex-1">
                  <code
                    className="font-mono text-[12px]"
                    style={{ color: TEXT_BRIGHT }}
                  >
                    {c.connector}
                  </code>
                  <div
                    className="text-[10px] mt-0.5"
                    style={{ color: TEXT_DIM }}
                  >
                    {c.today_calls} call(s)
                    {c.today_estimated_calls > 0 && (
                      <>
                        {' · '}
                        <span style={{ color: '#fbbf24' }}>
                          {c.today_estimated_calls} estimated
                        </span>
                      </>
                    )}
                  </div>
                </div>
                <div
                  className="font-mono text-sm flex-shrink-0 text-right"
                  style={{ color: TEXT_BRIGHT }}
                >
                  <div>${c.today_spend_usd.toFixed(4)}</div>
                  {/* Show 7-day total when distinct from today's value */}
                  {c.recent_spend_usd > c.today_spend_usd && (
                    <div
                      className="text-[10px] mt-0.5"
                      style={{ color: TEXT_DIM }}
                    >
                      {c.recent_window_days}d: ${c.recent_spend_usd.toFixed(4)}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          {/* Window total when there's history beyond today */}
          {state && state.total_recent_usd > state.total_usd && (
            <div
              className="text-[10px] mt-2 text-right font-mono"
              style={{ color: TEXT_DIM }}
            >
              Last {state.recent_window_days}d total: $
              {state.total_recent_usd.toFixed(4)} (
              {state.total_recent_calls} call
              {state.total_recent_calls === 1 ? '' : 's'})
            </div>
          )}
        </>
      )}

      {/* Overrides editor — operator-tunable caps per connector */}
      {enabled && (
        <div
          className="mt-4 pt-3 border-t"
          style={{ borderColor: ACCENT_BORDER }}
        >
          <h4
            className="text-[11px] uppercase tracking-wide mb-2"
            style={{ color: TEXT_DIM }}
          >
            Overrides ({Object.keys(overrides).length})
          </h4>
          <p
            className="text-[11px] italic mb-2"
            style={{ color: TEXT_DIM }}
          >
            Tune caps without redeploying. Effective for the next call —
            no restart needed. Empty fields mean "fall back to the
            decorator's hardcoded default."
          </p>

          {/* Existing overrides */}
          {Object.keys(overrides).length > 0 && (
            <div className="space-y-1 mb-3">
              {Object.entries(overrides).map(([name, vals]) => (
                <div
                  key={name}
                  className="rounded p-2 text-xs flex items-center justify-between gap-2"
                  style={{ background: '#0a0e14' }}
                >
                  <code
                    className="font-mono text-[12px]"
                    style={{ color: TEXT_BRIGHT }}
                  >
                    {name}
                  </code>
                  <div
                    className="text-[10px] font-mono"
                    style={{ color: TEXT_DIM }}
                  >
                    {vals.daily_cap_usd !== undefined && (
                      <span>
                        cap=${vals.daily_cap_usd.toFixed(4)}
                      </span>
                    )}
                    {vals.daily_cap_usd !== undefined &&
                      vals.estimated_cost_usd !== undefined && ' · '}
                    {vals.estimated_cost_usd !== undefined && (
                      <span>
                        est=${vals.estimated_cost_usd.toFixed(4)}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => removeOverride(name)}
                    disabled={busy}
                    className="px-2 py-0.5 rounded text-[10px] border disabled:opacity-50"
                    style={{
                      background: WARN_BG,
                      color: WARN,
                      borderColor: WARN + '55',
                    }}
                    title="Remove override; decorator default takes over."
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Add-form */}
          <div className="flex flex-wrap gap-2 items-end">
            <div className="flex-1 min-w-[140px]">
              <label
                className="block text-[10px] mb-0.5"
                style={{ color: TEXT_DIM }}
              >
                Connector
              </label>
              <input
                value={newConnector}
                onChange={(e) => setNewConnector(e.target.value)}
                placeholder="e.g. aviationstack"
                className="w-full px-2 py-1 rounded text-xs font-mono border"
                style={{
                  background: '#0a0e14',
                  color: TEXT_BRIGHT,
                  borderColor: ACCENT_BORDER,
                }}
              />
            </div>
            <div className="w-28">
              <label
                className="block text-[10px] mb-0.5"
                style={{ color: TEXT_DIM }}
              >
                Daily cap $
              </label>
              <input
                value={newCap}
                onChange={(e) => setNewCap(e.target.value)}
                placeholder="0.005"
                className="w-full px-2 py-1 rounded text-xs font-mono border"
                style={{
                  background: '#0a0e14',
                  color: TEXT_BRIGHT,
                  borderColor: ACCENT_BORDER,
                }}
              />
            </div>
            <div className="w-28">
              <label
                className="block text-[10px] mb-0.5"
                style={{ color: TEXT_DIM }}
              >
                Per-call $
              </label>
              <input
                value={newEstimate}
                onChange={(e) => setNewEstimate(e.target.value)}
                placeholder="0.001"
                className="w-full px-2 py-1 rounded text-xs font-mono border"
                style={{
                  background: '#0a0e14',
                  color: TEXT_BRIGHT,
                  borderColor: ACCENT_BORDER,
                }}
              />
            </div>
            <button
              onClick={addOverride}
              disabled={busy}
              className="px-3 py-1 rounded text-xs font-medium border disabled:opacity-50"
              style={{
                background: OK + '22',
                color: OK,
                borderColor: OK + '55',
              }}
            >
              {busy ? '…' : '+ Add'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        className="text-lg font-semibold font-mono"
        style={{ color: TEXT_BRIGHT }}
      >
        {value}
      </div>
      <div className="text-[10px]" style={{ color: TEXT_DIM }}>
        {label}
      </div>
    </div>
  );
}

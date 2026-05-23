// Anthropic per-day cap card — Phase D.3 (2026-05-22).
//
// Operator-visible vendor-level cost ceiling for Claude calls. Sits
// next to the connector-budget card on /cp/settings. Three controls:
//
//   - Current state header (enabled badge + spent + cap + headroom)
//   - Set / clear cap inline form
//   - "Would this estimate be refused?" dry-run probe
//
// Default OFF — the card communicates "no cap set" by graying the
// numeric fields and showing an explicit "Enable cap" form.

import { useState } from 'react';
import {
  useAnthropicBudgetStateQuery,
  useSetAnthropicCapMutation,
  usePreCheckAnthropicCapMutation,
} from '../api/anthropic_budget';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT_BG = '#1e2738';
const ACCENT_BORDER = '#2a3a52';
const OK = '#34d399';
const WARN = '#f87171';

function usd(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  if (n === 0) return '$0';
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function pct(spent: number, cap: number | null): number | null {
  if (cap === null || cap <= 0) return null;
  return (spent / cap) * 100;
}

export function AnthropicBudgetCard() {
  const q = useAnthropicBudgetStateQuery();
  const setCap = useSetAnthropicCapMutation();
  const preCheck = usePreCheckAnthropicCapMutation();
  const [newCap, setNewCap] = useState('');
  const [probeEstimate, setProbeEstimate] = useState('0.50');
  const [probeResult, setProbeResult] = useState<string>('');
  const [err, setErr] = useState<string | null>(null);

  const state = q.data;
  const enabled = state?.enabled ?? false;
  const cap = state?.cap_usd ?? null;
  const spent = state?.spent_usd_24h ?? 0;
  const headroom = state?.headroom_usd ?? null;
  const usage = pct(spent, cap);

  const handleSetCap = async () => {
    setErr(null);
    const v = newCap.trim();
    const num = v === '' ? null : Number(v);
    if (num !== null && (Number.isNaN(num) || num <= 0)) {
      setErr('Cap must be a positive number, or blank to disable.');
      return;
    }
    try {
      await setCap.mutateAsync(num);
      setNewCap('');
    } catch (e) {
      setErr(String(e));
    }
  };

  const handleClearCap = async () => {
    setErr(null);
    try {
      await setCap.mutateAsync(null);
    } catch (e) {
      setErr(String(e));
    }
  };

  const handleProbe = async () => {
    setProbeResult('');
    const est = Number(probeEstimate);
    if (Number.isNaN(est) || est < 0) {
      setProbeResult('Estimate must be a non-negative number.');
      return;
    }
    try {
      const r = await preCheck.mutateAsync(est);
      if (!r.enabled) {
        setProbeResult('Cap is disabled — call would proceed.');
      } else if (r.would_refuse) {
        setProbeResult(`🚫 Would REFUSE: ${r.reason}`);
      } else {
        setProbeResult(
          `✅ Would proceed. Headroom after: ${usd(
            (r.headroom_usd ?? 0) - est,
          )}`,
        );
      }
    } catch (e) {
      setProbeResult(`Error: ${String(e)}`);
    }
  };

  return (
    <div
      style={{
        background: ACCENT_BG,
        border: `1px solid ${ACCENT_BORDER}`,
        borderRadius: '6px',
        padding: '16px',
        marginBottom: '16px',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '12px',
        }}
      >
        <div>
          <h3
            style={{
              margin: 0, fontSize: '15px', fontWeight: 600,
              color: TEXT_BRIGHT,
            }}
          >
            Anthropic per-day cap
          </h3>
          <p
            style={{
              margin: '4px 0 0 0', color: TEXT_DIM, fontSize: '12px',
            }}
          >
            Vendor-level USD ceiling on rolling-24h Claude spend. Sits
            above the per-call breakers as a proactive refuse-gate.
          </p>
        </div>
        <span
          style={{
            padding: '4px 10px',
            background: enabled ? OK : TEXT_DIM,
            color: '#fff',
            borderRadius: '12px',
            fontSize: '11px',
            fontWeight: 600,
          }}
        >
          {enabled ? 'ENABLED' : 'DISABLED'}
        </span>
      </div>

      {/* State */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: '12px',
          marginBottom: '16px',
          fontSize: '13px',
        }}
      >
        <div>
          <div style={{ color: TEXT_DIM, fontSize: '11px' }}>Spent (24h)</div>
          <div style={{ color: TEXT_BRIGHT, fontWeight: 600 }}>
            {usd(spent)}
          </div>
        </div>
        <div>
          <div style={{ color: TEXT_DIM, fontSize: '11px' }}>Cap</div>
          <div style={{ color: TEXT_BRIGHT, fontWeight: 600 }}>
            {usd(cap)}
          </div>
        </div>
        <div>
          <div style={{ color: TEXT_DIM, fontSize: '11px' }}>Headroom</div>
          <div
            style={{
              color:
                headroom === 0 ? WARN :
                  usage !== null && usage > 90 ? WARN :
                  TEXT_BRIGHT,
              fontWeight: 600,
            }}
          >
            {usd(headroom)}
            {usage !== null && (
              <span
                style={{
                  color: TEXT_DIM, fontSize: '11px', marginLeft: '6px',
                }}
              >
                ({usage.toFixed(1)}%)
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Set cap form */}
      <div style={{ marginBottom: '12px' }}>
        <label
          style={{
            display: 'block', color: TEXT_DIM, fontSize: '11px',
            marginBottom: '4px',
          }}
        >
          Set cap (USD; blank = disable):
        </label>
        <div style={{ display: 'flex', gap: '6px' }}>
          <input
            type="number"
            min="0"
            step="0.01"
            value={newCap}
            placeholder={cap === null ? 'e.g. 25.00' : `current: ${cap}`}
            onChange={e => setNewCap(e.target.value)}
            style={{
              flex: 1, padding: '6px 10px',
              background: '#0f1623',
              border: `1px solid ${ACCENT_BORDER}`,
              color: TEXT_BRIGHT, borderRadius: '4px', fontSize: '13px',
            }}
          />
          <button
            onClick={handleSetCap}
            disabled={setCap.isPending}
            style={{
              padding: '6px 14px',
              background: '#3b82f6', color: '#fff',
              border: 'none', borderRadius: '4px',
              cursor: setCap.isPending ? 'not-allowed' : 'pointer',
              fontSize: '12px', fontWeight: 600,
            }}
          >
            {setCap.isPending ? '…' : 'Set'}
          </button>
          {cap !== null && (
            <button
              onClick={handleClearCap}
              disabled={setCap.isPending}
              style={{
                padding: '6px 14px',
                background: ACCENT_BG, color: TEXT_BRIGHT,
                border: `1px solid ${ACCENT_BORDER}`, borderRadius: '4px',
                cursor: 'pointer', fontSize: '12px',
              }}
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Dry-run probe */}
      <div>
        <label
          style={{
            display: 'block', color: TEXT_DIM, fontSize: '11px',
            marginBottom: '4px',
          }}
        >
          Probe: would this estimate be refused right now?
        </label>
        <div style={{ display: 'flex', gap: '6px' }}>
          <input
            type="number"
            min="0"
            step="0.01"
            value={probeEstimate}
            onChange={e => setProbeEstimate(e.target.value)}
            style={{
              width: '100px', padding: '6px 10px',
              background: '#0f1623',
              border: `1px solid ${ACCENT_BORDER}`,
              color: TEXT_BRIGHT, borderRadius: '4px', fontSize: '13px',
            }}
          />
          <button
            onClick={handleProbe}
            disabled={preCheck.isPending}
            style={{
              padding: '6px 14px',
              background: ACCENT_BG, color: TEXT_BRIGHT,
              border: `1px solid ${ACCENT_BORDER}`, borderRadius: '4px',
              cursor: 'pointer', fontSize: '12px',
            }}
          >
            {preCheck.isPending ? 'Checking…' : 'Probe'}
          </button>
        </div>
        {probeResult && (
          <div
            style={{
              marginTop: '6px', fontSize: '12px', color: TEXT_BRIGHT,
            }}
          >
            {probeResult}
          </div>
        )}
      </div>

      {err && (
        <div
          style={{
            marginTop: '10px', padding: '6px 10px',
            background: '#7f1d1d22', border: `1px solid ${WARN}`,
            color: WARN, borderRadius: '4px', fontSize: '12px',
          }}
        >
          {err}
        </div>
      )}
    </div>
  );
}

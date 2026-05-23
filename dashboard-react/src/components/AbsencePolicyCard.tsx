// B1-P2 — Absence-policy onboarding card.
//
// Closes the "operator must pre-arm" architectural gap (P2-8). The
// card surfaces:
//   1. Current state of the absence policy (off / armed / firing)
//   2. What the policy actually does in plain English
//   3. A toggle to enable/disable
//   4. A pointer to the successor-declaration mechanism for the
//      long-tail "operator gone" case
//
// Visible at /cp/settings. The operator decides at peak engagement
// (not under duress when they're about to go silent).

import { useState } from 'react';
import { api } from '../api/client';
import type { RuntimeSettings } from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT = '#5ec8a8';
const WARN = '#f87171';
const PANEL_BG = '#111820';
const BORDER = '#1e2738';

export function AbsencePolicyCard({
  settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const enabled = settings.upgrade_lifecycle_absence_policy_enabled === true;
  const parentEnabled = settings.upgrade_lifecycle_enabled !== false;

  const handleToggle = async (next: boolean) => {
    setError(null);
    setSaving(true);
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify({
          upgrade_lifecycle_absence_policy_enabled: next,
        }),
      });
      onSettingsChange();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="rounded-lg border p-4 space-y-3"
      style={{ background: PANEL_BG, borderColor: enabled ? ACCENT : BORDER }}
    >
      <div>
        <div className="flex items-center gap-2">
          <span style={{ fontSize: 18 }}>🕯️</span>
          <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
            Operator-absence policy
          </h2>
          {enabled && (
            <span
              className="px-2 py-0.5 text-[10px] uppercase rounded"
              style={{ background: '#065f4644', color: ACCENT }}
            >
              armed
            </span>
          )}
        </div>
        <p className="text-[11px] mt-2 leading-relaxed" style={{ color: TEXT_DIM }}>
          For decade-scale unattended operation. When this is ON AND{' '}
          <code style={{ color: TEXT_BRIGHT }}>operator_transition</code>
          {' '}reports ABSENT_90D or TRANSITIONED, the upgrade-lifecycle
          subsystem will auto-promote a narrow class of patch-level CRs
          to AUTO_APPLY so the system doesn't grind to a halt while
          you're away.
        </p>
      </div>

      {/* The "what it does" matrix */}
      <div
        className="rounded p-2 text-[10px] space-y-1"
        style={{ background: '#0a0e14', color: TEXT_DIM }}
      >
        <div>
          <strong style={{ color: ACCENT }}>Auto-applies when ALL:</strong>{' '}
          patch-level + trusted requestor + ≥14d PENDING + no license
          change + you've been silent for 90+ days
        </div>
        <div>
          <strong style={{ color: WARN }}>Never auto-applies:</strong>{' '}
          MINOR, MAJOR, framework, license-shift, or anything during
          ACTIVE/ABSENT_30D/READ_MOSTLY phases
        </div>
        <div>
          <strong style={{ color: TEXT_BRIGHT }}>Each promotion:</strong>{' '}
          fires a Signal alert + ledger event so you see exactly what
          landed when you return. Rolls back automatically (30-min
          window) on regression.
        </div>
      </div>

      <label
        className="flex items-start gap-2 text-xs cursor-pointer"
        style={{ color: parentEnabled ? TEXT_BRIGHT : TEXT_DIM }}
      >
        <input
          type="checkbox"
          checked={enabled}
          disabled={!parentEnabled || saving}
          onChange={(e) => handleToggle(e.target.checked)}
        />
        <span>
          <strong>Arm absence policy</strong>
          {!parentEnabled && (
            <span style={{ color: WARN }}>
              {' '}— (parent <code>upgrade_lifecycle_enabled</code> must be ON first)
            </span>
          )}
        </span>
      </label>

      {/* Successor-declaration pointer */}
      <details
        className="rounded border p-2"
        style={{ borderColor: BORDER }}
      >
        <summary
          className="text-[11px] cursor-pointer"
          style={{ color: TEXT_BRIGHT }}
        >
          For the long-tail case: designating a successor
        </summary>
        <div
          className="mt-2 text-[10px] leading-relaxed space-y-2"
          style={{ color: TEXT_DIM }}
        >
          <p>
            The absence policy covers the &quot;operator is silent but the
            system keeps going&quot; case. For the harder &quot;operator
            never returns&quot; case (multi-year), declare a successor at
            {' '}
            <code style={{ color: TEXT_BRIGHT }}>
              workspace/operator_transition/successor.json
            </code>
            . The successor is operator-authored, never system-acted; a
            designated successor with gateway-secret + Signal-bot access
            can flip switches on your behalf in your absence.
          </p>
          <p>
            See{' '}
            <code style={{ color: TEXT_BRIGHT }}>
              app/operator_transition/successor.py
            </code>{' '}
            for the file schema (
            <code style={{ color: TEXT_BRIGHT }}>SuccessorDeclaration</code>
            ).
          </p>
        </div>
      </details>

      {error && (
        <p
          className="text-xs px-2 py-1 rounded"
          style={{ color: WARN, background: '#7f1d1d22' }}
        >
          {error}
        </p>
      )}
    </div>
  );
}

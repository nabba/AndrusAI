// Capability-regression card (2026-05-22).
//
// Operator visibility into the hourly snapshot of registered tools +
// effective LLM models. The daemon alerts via Signal when a shrink is
// detected; this card surfaces the same data on the dashboard so
// operators can inspect history + flip the master switch.
//
// Safety semantics:
//   - Read-only over a JSONL ledger written by the daemon.
//   - Master-switch toggle goes through /api/cp/settings, same as the
//     other observational subsystems.
//   - Card stays compact when there's no regression to review — only
//     lights up when ``last_regression`` is populated.

import { useState } from 'react';
import { api } from '../api/client';
import {
  type RegressionReport,
  useCapabilityRegressionRegressionsQuery,
  useCapabilityRegressionStateQuery,
  useForceCapabilitySnapshotMutation,
} from '../api/capability_regression';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const WARN_BG = '#7f1d1d22';
const ACCENT_BG = '#1e2738';
const ACCENT_BORDER = '#2a3a52';
const OK = '#34d399';

export function CapabilityRegressionCard({
  onSettingsChange,
}: {
  onSettingsChange: () => void;
}) {
  const stateQ = useCapabilityRegressionStateQuery();
  const regsQ = useCapabilityRegressionRegressionsQuery(10);
  const forceSnapshot = useForceCapabilitySnapshotMutation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const state = stateQ.data;
  const regressions = regsQ.data?.regressions ?? [];
  const enabled = state?.enabled ?? false;
  const snap = state?.current_snapshot ?? null;
  const last = state?.last_regression ?? null;
  const snapshotResult = forceSnapshot.data;

  const toggle = async () => {
    setError(null);
    setBusy(true);
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify({ capability_regression_enabled: !enabled }),
      });
      onSettingsChange();
      // Refetch so the next render picks up the new state
      stateQ.refetch();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleForceSnapshot = () => {
    setError(null);
    forceSnapshot.mutate(undefined, {
      onError: (e) => setError(String(e)),
    });
  };

  return (
    <div
      className="rounded-lg p-4 border"
      style={{
        background: ACCENT_BG,
        borderColor: last?.has_regression ? WARN : ACCENT_BORDER,
      }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h3
            className="text-sm font-semibold"
            style={{ color: TEXT_BRIGHT }}
          >
            🔧 Capability regression
          </h3>
          <p className="text-xs mt-1" style={{ color: TEXT_DIM }}>
            Hourly snapshot of registered tools + effective LLM models.
            Alerts on SHRINK; growth + operator-blocked models are silent.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {enabled && (
            <button
              disabled={forceSnapshot.isPending}
              onClick={handleForceSnapshot}
              className="px-2 py-1 rounded text-[11px] font-medium border transition-colors disabled:opacity-50"
              style={{
                background: ACCENT_BG,
                color: TEXT_DIM,
                borderColor: ACCENT_BORDER,
              }}
              title="Force a one-shot snapshot pass right now instead of waiting for the next hourly idle run."
            >
              {forceSnapshot.isPending ? '⟳ …' : '⟳ Snapshot now'}
            </button>
          )}
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
      </div>

      {snapshotResult && !snapshotResult.ran && (
        <div
          className="text-xs italic mb-3"
          style={{ color: TEXT_DIM }}
        >
          Snapshot did not run: {snapshotResult.reason}
        </div>
      )}
      {snapshotResult && snapshotResult.ran && (
        <div
          className="text-xs mb-3"
          style={{ color: OK }}
        >
          ✓ Snapshot ran. {snapshotResult.regression
            ? 'New regression detected — see below.'
            : 'No regression vs prior snapshot.'}
        </div>
      )}

      {error && (
        <div
          className="text-xs rounded p-2 mb-3"
          style={{ background: WARN_BG, color: WARN }}
        >
          {error}
        </div>
      )}

      {/* Current snapshot summary */}
      {snap ? (
        <div
          className="grid grid-cols-3 gap-2 text-xs mb-3"
          style={{ color: TEXT_DIM }}
        >
          <Stat label="Tools" value={snap.tools.length} />
          <Stat label="Effective models" value={snap.models.length} />
          <Stat label="Blocked models" value={snap.blocked_models.length} />
          <div
            className="col-span-3 text-[10px] font-mono"
            style={{ color: TEXT_DIM }}
          >
            captured {new Date(snap.captured_at).toLocaleString()}
          </div>
        </div>
      ) : (
        <div
          className="text-xs italic mb-3"
          style={{ color: TEXT_DIM }}
        >
          No snapshot yet — daemon writes the first baseline on next idle pass.
        </div>
      )}

      {/* Last regression highlight (only when there IS one) */}
      {last?.has_regression && (
        <div
          className="rounded p-2 mb-3 text-xs border"
          style={{
            background: WARN_BG,
            borderColor: WARN + '55',
            color: TEXT_BRIGHT,
          }}
        >
          <div className="font-semibold mb-1" style={{ color: WARN }}>
            ⚠ Last regression detected
          </div>
          <RegressionRow row={last} />
        </div>
      )}

      {/* Recent regressions table */}
      {regressions.length > 0 && (
        <div>
          <h4
            className="text-[11px] uppercase tracking-wide mb-1"
            style={{ color: TEXT_DIM }}
          >
            Recent regressions ({regressions.length})
          </h4>
          <div className="space-y-1">
            {regressions.map((r, i) => (
              <div
                key={`${r.curr_captured_at}-${i}`}
                className="rounded p-2 text-xs"
                style={{ background: '#0a0e14' }}
              >
                <RegressionRow row={r} />
              </div>
            ))}
          </div>
        </div>
      )}

      {regressions.length === 0 && snap && !last?.has_regression && (
        <div
          className="text-xs italic"
          style={{ color: TEXT_DIM }}
        >
          ✓ No regressions on record.
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div
        className="text-lg font-semibold"
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

function RegressionRow({ row }: { row: RegressionReport }) {
  return (
    <div className="space-y-0.5">
      <div className="text-[10px] font-mono" style={{ color: TEXT_DIM }}>
        {new Date(row.curr_captured_at).toLocaleString()}
      </div>
      {row.tools_deleted.length > 0 && (
        <div style={{ color: TEXT_BRIGHT }}>
          <span style={{ color: WARN }}>−{row.tools_deleted.length}</span>{' '}
          tool(s):{' '}
          <code className="font-mono text-[11px]">
            {row.tools_deleted.slice(0, 5).join(', ')}
            {row.tools_deleted.length > 5
              ? ` (+${row.tools_deleted.length - 5} more)`
              : ''}
          </code>
        </div>
      )}
      {row.models_truly_deleted.length > 0 && (
        <div style={{ color: TEXT_BRIGHT }}>
          <span style={{ color: WARN }}>
            −{row.models_truly_deleted.length}
          </span>{' '}
          model(s):{' '}
          <code className="font-mono text-[11px]">
            {row.models_truly_deleted.slice(0, 5).join(', ')}
            {row.models_truly_deleted.length > 5
              ? ` (+${row.models_truly_deleted.length - 5} more)`
              : ''}
          </code>
        </div>
      )}
      {row.models_newly_blocked.length > 0 && (
        <div style={{ color: TEXT_DIM }}>
          (also {row.models_newly_blocked.length} model(s) newly blocked —
          operator action)
        </div>
      )}
    </div>
  );
}

// Capability-regression dedicated page (2026-05-22).
//
// Settings card surfaces "now"; this page surfaces "over time" —
// full snapshot history + every recorded regression with per-row
// detail. Operators come here to investigate "when did we lose
// capability X" or "how often are regressions happening?"

import { Link } from 'react-router-dom';
import {
  type CapabilitySnapshot,
  type RegressionReport,
  useCapabilityRegressionHistoryQuery,
  useCapabilityRegressionRegressionsQuery,
  useCapabilityRegressionStateQuery,
  useForceCapabilitySnapshotMutation,
} from '../api/capability_regression';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const OK = '#34d399';
const ACCENT_BG = '#111820';
const ACCENT_BORDER = '#1e2738';

export function CapabilityRegressionPage() {
  const stateQ = useCapabilityRegressionStateQuery();
  const historyQ = useCapabilityRegressionHistoryQuery(50);
  const regsQ = useCapabilityRegressionRegressionsQuery(100);
  const forceSnapshot = useForceCapabilitySnapshotMutation();

  const state = stateQ.data;
  const snapshots = historyQ.data?.snapshots ?? [];
  const regressions = regsQ.data?.regressions ?? [];
  const enabled = state?.enabled ?? false;

  // Latest snapshot stats for the KPI tiles
  const latest = state?.current_snapshot;
  const regressionsLast30d = regressions.filter((r) => {
    if (!r.curr_captured_at) return false;
    const ts = new Date(r.curr_captured_at).getTime();
    return Date.now() - ts < 30 * 24 * 3600 * 1000;
  });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">
          Capability regression
        </h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Hourly snapshots of registered tools + effective LLM models.
          The detector alerts on SHRINK — growth and operator-blocked
          models are silent. Audit lives in{' '}
          <code className="font-mono">
            workspace/capability_regression/
          </code>
          .
          {!enabled && (
            <>
              {' '}
              The subsystem is currently <strong>OFF</strong> — flip
              it on in{' '}
              <Link to="/settings" className="text-[#60a5fa] hover:underline">
                /cp/settings
              </Link>
              .
            </>
          )}
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-3">
        <Kpi
          label="Tools"
          value={latest?.tools.length ?? 0}
          dim={!enabled}
        />
        <Kpi
          label="Effective models"
          value={latest?.models.length ?? 0}
          dim={!enabled}
        />
        <Kpi
          label="Blocked models"
          value={latest?.blocked_models.length ?? 0}
          dim={!enabled}
        />
        <Kpi
          label="Regressions (30d)"
          value={regressionsLast30d.length}
          warn={regressionsLast30d.length > 0}
          dim={!enabled}
        />
      </div>

      {/* Force-snapshot row (operator action) */}
      {enabled && (
        <div className="flex items-center gap-3">
          <button
            disabled={forceSnapshot.isPending}
            onClick={() => forceSnapshot.mutate()}
            className="px-3 py-1 rounded text-xs font-medium border disabled:opacity-50"
            style={{
              background: ACCENT_BG,
              color: TEXT_DIM,
              borderColor: ACCENT_BORDER,
            }}
          >
            {forceSnapshot.isPending ? '⟳ Running…' : '⟳ Snapshot now'}
          </button>
          {forceSnapshot.data && !forceSnapshot.data.ran && (
            <span className="text-xs italic" style={{ color: TEXT_DIM }}>
              Did not run: {forceSnapshot.data.reason}
            </span>
          )}
          {forceSnapshot.data && forceSnapshot.data.ran && (
            <span className="text-xs" style={{ color: OK }}>
              ✓ Snapshot ran
              {forceSnapshot.data.regression?.has_regression
                ? ' — NEW regression detected'
                : ' — no regression vs prior'}
            </span>
          )}
        </div>
      )}

      {/* Regressions table */}
      <div>
        <h2
          className="text-sm font-semibold mb-2"
          style={{ color: TEXT_BRIGHT }}
        >
          Detected regressions ({regressions.length})
        </h2>
        {regressions.length === 0 ? (
          <div
            className="rounded p-3 text-sm italic"
            style={{ background: ACCENT_BG, color: TEXT_DIM }}
          >
            ✓ No regressions on record.
          </div>
        ) : (
          <div className="space-y-1.5">
            {regressions.map((r, i) => (
              <RegressionDetail key={`reg-${i}`} row={r} />
            ))}
          </div>
        )}
      </div>

      {/* Snapshot history */}
      <div>
        <h2
          className="text-sm font-semibold mb-2"
          style={{ color: TEXT_BRIGHT }}
        >
          Snapshot history ({snapshots.length} shown, newest first)
        </h2>
        {snapshots.length === 0 ? (
          <div
            className="rounded p-3 text-sm italic"
            style={{ background: ACCENT_BG, color: TEXT_DIM }}
          >
            No snapshots yet — daemon writes the first baseline on next
            idle pass.
          </div>
        ) : (
          <div className="space-y-1">
            {snapshots.map((s, i) => (
              <SnapshotRow key={`snap-${i}`} snap={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  warn,
  dim,
}: {
  label: string;
  value: number;
  warn?: boolean;
  dim?: boolean;
}) {
  return (
    <div
      className="rounded-lg p-3 border"
      style={{
        background: ACCENT_BG,
        borderColor: warn ? WARN + '55' : ACCENT_BORDER,
        opacity: dim ? 0.5 : 1,
      }}
    >
      <div
        className="text-2xl font-semibold font-mono"
        style={{ color: warn ? WARN : TEXT_BRIGHT }}
      >
        {value}
      </div>
      <div
        className="text-[11px] uppercase tracking-wide mt-1"
        style={{ color: TEXT_DIM }}
      >
        {label}
      </div>
    </div>
  );
}

function RegressionDetail({ row }: { row: RegressionReport }) {
  return (
    <div
      className="rounded p-3 text-xs border"
      style={{
        background: ACCENT_BG,
        borderColor: WARN + '55',
      }}
    >
      <div
        className="text-[10px] font-mono mb-2"
        style={{ color: TEXT_DIM }}
      >
        {new Date(row.curr_captured_at).toLocaleString()}
        {' · '}vs {row.prev_captured_at}
      </div>
      {row.tools_deleted.length > 0 && (
        <div className="mb-1" style={{ color: TEXT_BRIGHT }}>
          <span style={{ color: WARN }}>
            −{row.tools_deleted.length} tool(s):
          </span>{' '}
          <code className="font-mono text-[11px]">
            {row.tools_deleted.join(', ')}
          </code>
        </div>
      )}
      {row.models_truly_deleted.length > 0 && (
        <div className="mb-1" style={{ color: TEXT_BRIGHT }}>
          <span style={{ color: WARN }}>
            −{row.models_truly_deleted.length} model(s):
          </span>{' '}
          <code className="font-mono text-[11px]">
            {row.models_truly_deleted.join(', ')}
          </code>
        </div>
      )}
      {row.models_newly_blocked.length > 0 && (
        <div style={{ color: TEXT_DIM }}>
          ({row.models_newly_blocked.length} also newly operator-blocked —
          not counted as regression)
        </div>
      )}
    </div>
  );
}

function SnapshotRow({ snap }: { snap: CapabilitySnapshot }) {
  return (
    <div
      className="rounded p-2 text-xs flex items-center justify-between gap-3"
      style={{ background: ACCENT_BG }}
    >
      <div
        className="text-[10px] font-mono"
        style={{ color: TEXT_DIM }}
      >
        {new Date(snap.captured_at).toLocaleString()}
      </div>
      <div className="flex items-center gap-3" style={{ color: TEXT_BRIGHT }}>
        <span>
          {snap.tools.length}{' '}
          <span className="text-[10px]" style={{ color: TEXT_DIM }}>
            tools
          </span>
        </span>
        <span>
          {snap.models.length}{' '}
          <span className="text-[10px]" style={{ color: TEXT_DIM }}>
            models
          </span>
        </span>
        {snap.blocked_models.length > 0 && (
          <span>
            {snap.blocked_models.length}{' '}
            <span className="text-[10px]" style={{ color: TEXT_DIM }}>
              blocked
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

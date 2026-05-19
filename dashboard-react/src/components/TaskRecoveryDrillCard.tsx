// Task-recovery drill — survey response to arXiv:2604.27096 §4.3.4.
//
// Measures CrewAI agent/task-layer recovery rate when realistic
// failures hit. Three switches:
//   - master         (default ON; drill is registered + scheduled)
//   - live           (default OFF; gates real LLM spend — without
//                    this, the drill returns SKIPPED to the scheduler)
//   - llm_variants   (default ON; controls whether injection
//                    payloads are fresh-LLM-generated each run vs.
//                    picked from the curated fallback pool)

import { useState } from 'react';
import { api } from '../api/client';
import { useDrillsRegistryQuery } from '../api/queries';
import type { RuntimeSettings } from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const WARN_BG = '#7f1d1d22';
const INFO_BG = '#1e3a5f22';
const PASS_GREEN = '#4ade80';
const STALE_AMBER = '#fbbf24';

export function TaskRecoveryDrillCard({
  settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [runState, setRunState] = useState<
    'idle' | 'running' | 'done' | 'error'
  >('idle');
  const [runResult, setRunResult] = useState<unknown>(null);
  const drillsQ = useDrillsRegistryQuery();

  const update = async (patch: Record<string, unknown>) => {
    setError(null);
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify(patch),
      });
      onSettingsChange();
      drillsQ.refetch();
    } catch (e) {
      setError(String(e));
    }
  };

  const runNow = async () => {
    setError(null);
    setRunState('running');
    setRunResult(null);
    try {
      const result = await api('/api/cp/drills/run/task_recovery', {
        method: 'POST',
      });
      setRunResult(result);
      setRunState('done');
      drillsQ.refetch();
    } catch (e) {
      setError(String(e));
      setRunState('error');
    }
  };

  const master = !!settings.drill_task_recovery_enabled;
  const live = !!settings.drill_task_recovery_live_enabled;
  const llmVariants = !!settings.drill_task_recovery_llm_variants_enabled;

  const drill = drillsQ.data?.drills?.find((d) => d.name === 'task_recovery');
  const state = drill ? drillStateFor(drill) : null;

  return (
    <div
      className="rounded-lg p-4 border space-y-3"
      style={{ background: '#111820', borderColor: '#1e2738' }}
    >
      <div>
        <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Task-recovery drill (arXiv:2604.27096 §4.3.4)
        </h2>
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Quarterly. Injects 4 failure classes (type mismatch, missing
          field, numerical anomaly, transient timeout) into a synthetic
          agent task and measures recovery via named mechanisms
          (tool_supervisor / structured_diagnosis / recovery_loop).
          Paper target: ≥0.75 recovery rate.
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

      <div className="flex items-center gap-2">
        <Toggle
          label="task_recovery drill (master)"
          checked={master}
          onChange={(v) => update({ drill_task_recovery_enabled: v })}
          caveat="When OFF, the scheduler doesn't invoke the drill at all."
        />
        {state && (
          <span
            className="text-[10px] ml-2 px-1.5 py-0.5 rounded"
            style={{
              color: state.color,
              background: state.bg,
              border: `1px solid ${state.color}33`,
            }}
            title={state.tooltip}
          >
            {state.label}
          </span>
        )}
      </div>

      {master && (
        <div
          className="pl-4 space-y-3 border-l-2"
          style={{ borderColor: '#1e2738' }}
        >
          <div>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={live}
                onChange={(e) =>
                  update({ drill_task_recovery_live_enabled: e.target.checked })
                }
              />
              <span style={{ color: TEXT_BRIGHT, fontWeight: 500 }}>
                Live mode (real LLM calls)
              </span>
            </label>
            <div
              className="text-[10px] mt-1 p-2 rounded"
              style={{ color: TEXT_BRIGHT, background: INFO_BG }}
            >
              When OFF (default), the drill returns SKIPPED to the scheduler
              with <code>reason=live_mode_off</code>. When ON, each quarterly
              run kicks off the synthetic crew through the budget-tier LLM
              cascade. Per-run cost cap is <code>$0.10</code> (typical actual
              cost <code>~$0.02</code>). Recovery rate is meaningful only
              under LIVE mode.
            </div>
          </div>

          <Toggle
            label="LLM-generated injection variants"
            checked={llmVariants}
            onChange={(v) =>
              update({ drill_task_recovery_llm_variants_enabled: v })
            }
            caveat="When ON (default), each run asks a budget-tier LLM for a fresh injection variant per class — the anti-Goodhart layer. When OFF, the drill picks from a curated fallback pool. Only consulted in LIVE mode."
          />

          <div className="pt-1">
            <button
              type="button"
              onClick={runNow}
              disabled={runState === 'running'}
              className="text-xs px-3 py-1.5 rounded border cursor-pointer disabled:cursor-wait disabled:opacity-50"
              style={{
                background: '#1e2738',
                borderColor: '#2d3748',
                color: TEXT_BRIGHT,
              }}
            >
              {runState === 'running' ? 'Running…' : 'Run now'}
            </button>
            <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
              Bypasses scheduler backoff; QUARANTINED / MUTED states still
              respected. If LIVE mode is OFF, the drill returns SKIPPED
              immediately — no cost incurred.
            </p>
          </div>

          {runState === 'done' && runResult != null && (
            <RunResult result={runResult} />
          )}
        </div>
      )}
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


type RunResultShape = {
  status?: string;
  detail?: {
    reason?: string;
    mode?: string;
    recovery_rate?: number;
    by_class?: Record<string, {
      baseline_ok?: boolean;
      injected_recovered?: boolean;
      mechanism?: string | null;
      variant_source?: string | null;
      status?: string;
    }>;
  };
  observation?: {
    recovery_rate?: number;
    cost_usd_estimate?: number;
  };
};

function RunResult({ result }: { result: unknown }) {
  const r = (result as RunResultShape) || {};
  const status = r.status || 'unknown';
  const detail = r.detail || {};
  const recoveryRate = r.observation?.recovery_rate ?? detail.recovery_rate;
  const cost = r.observation?.cost_usd_estimate;
  const byClass = detail.by_class;

  const statusColor =
    status === 'pass'
      ? PASS_GREEN
      : status === 'skipped'
        ? TEXT_DIM
        : WARN;

  return (
    <div
      className="text-xs p-2 rounded space-y-1.5"
      style={{ background: INFO_BG, color: TEXT_BRIGHT }}
    >
      <div className="flex items-center gap-3 text-[11px]">
        <span style={{ color: statusColor, fontWeight: 600 }}>
          {status.toUpperCase()}
        </span>
        {recoveryRate != null && (
          <span>
            recovery_rate: <strong>{(recoveryRate * 100).toFixed(1)}%</strong>
          </span>
        )}
        {cost != null && (
          <span style={{ color: TEXT_DIM }}>
            est. cost: ${cost.toFixed(4)}
          </span>
        )}
        {detail.mode && (
          <span style={{ color: TEXT_DIM }}>mode: {detail.mode}</span>
        )}
      </div>
      {detail.reason && status === 'skipped' && (
        <div className="text-[10px]" style={{ color: TEXT_DIM }}>
          reason: <code>{detail.reason}</code>
        </div>
      )}
      {byClass && (
        <div className="text-[10px] space-y-0.5 mt-1">
          {Object.entries(byClass).map(([cls, info]) => (
            <div key={cls} className="flex items-center gap-2">
              <span
                style={{
                  color: info.injected_recovered ? PASS_GREEN : WARN,
                  width: '0.8rem',
                  display: 'inline-block',
                }}
              >
                {info.injected_recovered ? '✓' : '✗'}
              </span>
              <span
                style={{ color: TEXT_BRIGHT, fontWeight: 500, minWidth: '7rem' }}
              >
                {cls}
              </span>
              <span style={{ color: TEXT_DIM }}>
                {info.mechanism || (info.status === 'text_ok_no_mechanism'
                  ? 'text ok, no mechanism'
                  : info.status || '—')}
              </span>
              {info.variant_source && (
                <span
                  className="ml-auto text-[9px] px-1 rounded"
                  style={{
                    color: TEXT_DIM,
                    border: `1px solid ${TEXT_DIM}33`,
                  }}
                >
                  {info.variant_source}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function drillStateFor(d: {
  name: string;
  last_status: string | null;
  last_run_at: string | null;
  days_since_last_success: number | null;
  cadence_days: number;
  grace_days: number;
}) {
  if (d.last_run_at === null) {
    return {
      label: 'never run',
      color: TEXT_DIM,
      bg: '#1e273866',
      tooltip: `Drill ${d.name} has not been run yet`,
    };
  }
  const last = d.last_status || 'unknown';
  // Last status checks come BEFORE the staleness check: a drill
  // that has been SKIPPED recently (e.g. live_mode_off) should show
  // 'skipped', not STALE — even though days_since_last_success is
  // null because no PASS has ever happened.
  if (last === 'skipped') {
    return {
      label: 'skipped',
      color: TEXT_DIM,
      bg: '#1e273866',
      tooltip: 'Last run was skipped (live mode off, lock held, or master switch off)',
    };
  }
  if (last === 'fail' || last === 'error') {
    return {
      label: last.toUpperCase(),
      color: WARN,
      bg: WARN_BG,
      tooltip: `Last run ${last}: ${d.last_run_at}`,
    };
  }
  const days = d.days_since_last_success;
  const stale = days === null || days > d.cadence_days + d.grace_days;
  if (last === 'pass' && !stale) {
    return {
      label: `${days?.toFixed(0) ?? '?'}d ago`,
      color: PASS_GREEN,
      bg: '#16653422',
      tooltip: `Last pass: ${d.last_run_at}`,
    };
  }
  if (stale) {
    return {
      label: 'STALE',
      color: STALE_AMBER,
      bg: '#78350f22',
      tooltip: `Past cadence (${d.cadence_days}d) + grace (${d.grace_days}d)`,
    };
  }
  return {
    label: last,
    color: TEXT_DIM,
    bg: '#1e273866',
    tooltip: `Last status: ${last}`,
  };
}

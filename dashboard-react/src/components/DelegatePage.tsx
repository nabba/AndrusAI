// Autonomous executor operator surface — /cp/delegate.
//
// Phase 2 piece 2f (2026-05-20). Three sections:
//
//   1. "New run" composer (top): goal input + optional budget knobs +
//      submit button → POST /api/cp/delegate.
//   2. Active runs panel: list of non-terminal runs with status,
//      per-run drawer (click to expand) showing plan + budget + notes,
//      and an Abort button.
//   3. Terminal runs panel: same shape, no Abort.
//
// Auto-refreshes every 5s via the React Query hooks in api/delegate.ts.
// When the autonomous_executor master switch is OFF, the page still
// works (you can file runs) but a warning banner explains that the
// scheduler isn't advancing anything yet.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  formatStatus,
  formatStepStatus,
  useAbortDelegateRunMutation,
  useCreateDelegateRunMutation,
  useDelegateRunsQuery,
  type ExecutorRun,
  type ExecutorStatus,
} from '../api/delegate';
import { useRuntimeSettingsQuery } from '../api/queries';
import { Skeleton } from './ui/Skeleton';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT_BG = '#111820';
const ACCENT_BORDER = '#1e2738';

const TERMINAL_STATUSES: ExecutorStatus[] = [
  'completed',
  'failed',
  'budget_exhausted',
  'aborted',
];

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const delta = (Date.now() - d.getTime()) / 1000;
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return d.toLocaleDateString();
}

function StatusBadge({ status }: { status: ExecutorStatus }) {
  const s = formatStatus(status);
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${s.bg} ${s.fg}`}
    >
      {s.label}
    </span>
  );
}

// ── Run row + expandable drawer ────────────────────────────────────

function RunRow({
  run,
  expanded,
  onToggle,
}: {
  run: ExecutorRun;
  expanded: boolean;
  onToggle: () => void;
}) {
  const abortMut = useAbortDelegateRunMutation();
  const stepsDone = run.plan.filter((s) => s.status === 'completed').length;
  const stepsFailed = run.plan.filter((s) => s.status === 'failed').length;
  const isTerminal = TERMINAL_STATUSES.includes(run.status);

  return (
    <div
      className="rounded-lg border"
      style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
    >
      <button
        onClick={onToggle}
        className="w-full px-3 py-2 text-left flex items-center gap-3 hover:bg-[#1a2230]/40 transition-colors"
      >
        <code
          className="text-[10px] font-mono"
          style={{ color: TEXT_DIM, minWidth: '5rem' }}
        >
          {run.run_id.slice(0, 8)}
        </code>
        <StatusBadge status={run.status} />
        <span
          className="text-xs flex-1 truncate"
          style={{ color: TEXT_BRIGHT }}
        >
          {run.goal}
        </span>
        <span className="text-[10px]" style={{ color: TEXT_DIM }}>
          {stepsDone}/{run.plan.length} steps
          {stepsFailed > 0 ? ` · ${stepsFailed} failed` : ''}
        </span>
        <span className="text-[10px]" style={{ color: TEXT_DIM }}>
          ${run.budget.spent_usd.toFixed(3)}/${run.budget.cap_usd.toFixed(2)}
        </span>
        <span className="text-[10px]" style={{ color: TEXT_DIM, minWidth: '4rem', textAlign: 'right' }}>
          {formatRelative(run.last_touched_at)}
        </span>
        <span className="text-[10px]" style={{ color: TEXT_DIM }}>
          {expanded ? '▼' : '▶'}
        </span>
      </button>

      {expanded && (
        <div
          className="px-3 py-3 border-t space-y-3 text-xs"
          style={{ borderColor: ACCENT_BORDER, color: TEXT_DIM }}
        >
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Run ID
              </div>
              <code className="text-xs font-mono" style={{ color: TEXT_BRIGHT }}>
                {run.run_id}
              </code>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Requestor / Zone
              </div>
              <div style={{ color: TEXT_BRIGHT }}>
                {run.requestor} · {run.zone}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Created
              </div>
              <div style={{ color: TEXT_BRIGHT }}>{run.created_at.slice(0, 19)}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Last touched
              </div>
              <div style={{ color: TEXT_BRIGHT }}>
                {run.last_touched_at.slice(0, 19)}
              </div>
            </div>
          </div>

          {/* Budget bar */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="text-[10px] uppercase tracking-wider">
                Budget
              </div>
              <div style={{ color: TEXT_BRIGHT }}>
                ${run.budget.spent_usd.toFixed(4)} / $
                {run.budget.cap_usd.toFixed(2)} · {run.budget.spent_tokens.toLocaleString()} /{' '}
                {run.budget.cap_tokens.toLocaleString()} tokens
              </div>
            </div>
            <BudgetBar
              spent={run.budget.spent_usd}
              cap={run.budget.cap_usd}
            />
          </div>

          {/* Plan */}
          {run.plan.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Plan ({run.plan.length} step{run.plan.length === 1 ? '' : 's'})
              </div>
              <ul className="space-y-1 font-mono text-[11px]">
                {run.plan.map((step) => (
                  <li
                    key={step.step_id}
                    className="flex flex-col gap-1 px-2 py-1 rounded bg-[#0a1018]/40"
                  >
                    <div className="flex items-start gap-2">
                      <span style={{ color: TEXT_BRIGHT, minWidth: '1.2em' }}>
                        {formatStepStatus(step.status)}
                      </span>
                      <code style={{ color: TEXT_DIM, minWidth: '4em' }}>
                        {step.step_id}
                      </code>
                      {step.crew_hint && (
                        <code
                          className="px-1 text-[10px] rounded"
                          style={{
                            background: '#1e2738',
                            color: TEXT_BRIGHT,
                          }}
                        >
                          {step.crew_hint}
                        </code>
                      )}
                      <span style={{ color: TEXT_BRIGHT }}>{step.description}</span>
                    </div>
                    {/* Phase A.2 (2026-05-22) — CRs this step produced */}
                    {step.cr_ids && step.cr_ids.length > 0 && (
                      <div
                        className="text-[10px] pl-[5.5em] flex flex-wrap gap-1"
                        style={{ color: TEXT_DIM }}
                      >
                        Produced:
                        {step.cr_ids.map((cr) => (
                          <Link
                            key={cr}
                            to="/changes"
                            className="px-1.5 rounded font-mono border"
                            style={{
                              background: '#fbbf24' + '22',
                              color: '#fbbf24',
                              borderColor: '#fbbf24' + '55',
                            }}
                            title={`Open /cp/changes (CR ${cr})`}
                          >
                            {cr.slice(0, 8)}…
                          </Link>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Failure / abort reasons */}
          {run.failure_reason && (
            <div className="text-[#f87171]">
              ⚠ {run.failure_reason}
            </div>
          )}
          {run.abort_reason && (
            <div style={{ color: TEXT_DIM }}>
              ⚫ {run.abort_reason}
            </div>
          )}

          {/* Notes */}
          {run.notes.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Notes ({run.notes.length})
              </div>
              <ul className="space-y-0.5 text-[11px]" style={{ color: TEXT_DIM }}>
                {run.notes.slice(-5).map((n, i) => (
                  <li key={i}>• {n}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Abort button */}
          {!isTerminal && (
            <div className="pt-1">
              <button
                onClick={() => {
                  if (
                    window.confirm(
                      `Abort run ${run.run_id.slice(0, 8)}? This transitions to ABORTED.`,
                    )
                  ) {
                    abortMut.mutate({
                      runId: run.run_id,
                      reason: 'react-operator-abort',
                    });
                  }
                }}
                disabled={abortMut.isPending}
                className="text-xs px-3 py-1 rounded border border-[#f87171]/30 bg-[#f87171]/15 text-[#f87171] hover:bg-[#f87171]/25 disabled:opacity-50"
              >
                {abortMut.isPending ? 'Aborting…' : '⚫ Abort run'}
              </button>
              {abortMut.error && (
                <span className="text-[10px] ml-2 text-[#f87171]">
                  {String(abortMut.error)}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BudgetBar({ spent, cap }: { spent: number; cap: number }) {
  const pct = cap > 0 ? Math.min(100, (spent / cap) * 100) : 0;
  const color =
    pct >= 100
      ? '#f87171'
      : pct >= 80
      ? '#fbbf24'
      : pct >= 50
      ? '#22d3ee'
      : '#34d399';
  return (
    <div
      className="h-1.5 w-full rounded overflow-hidden"
      style={{ background: '#0a1018' }}
    >
      <div
        className="h-full transition-all"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

// ── New-run composer ──────────────────────────────────────────────

function NewRunComposer() {
  const createMut = useCreateDelegateRunMutation();
  const [goal, setGoal] = useState('');
  const [budgetUsd, setBudgetUsd] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (goal.trim().length < 4) {
      setError('Goal too short (need ≥4 characters).');
      return;
    }
    try {
      await createMut.mutateAsync({
        goal: goal.trim(),
        budget_usd: budgetUsd ? Number(budgetUsd) : undefined,
        zone: 'autonomous',
        requestor: 'react-operator',
      });
      setGoal('');
      setBudgetUsd('');
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <form
      onSubmit={submit}
      className="rounded-lg p-3 border space-y-2"
      style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
    >
      <h2
        className="text-sm font-medium"
        style={{ color: TEXT_BRIGHT }}
      >
        New autonomous run
      </h2>
      <p className="text-[10px]" style={{ color: TEXT_DIM }}>
        File a goal; the scheduler will pick it up on the next tick when
        the master switch is on. The deterministic v1 planner emits one
        step; the optional LLM planner v2 decomposes into 1-5 sub-goals.
      </p>
      <div className="flex gap-2">
        <input
          type="text"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. summarise today's news and draft a reply"
          className="flex-1 px-2 py-1 text-xs rounded"
          style={{
            background: '#0a1018',
            color: TEXT_BRIGHT,
            border: `1px solid ${ACCENT_BORDER}`,
          }}
        />
        <input
          type="number"
          value={budgetUsd}
          onChange={(e) => setBudgetUsd(e.target.value)}
          placeholder="$ budget"
          step="0.1"
          min="0"
          max="10"
          className="px-2 py-1 text-xs rounded"
          style={{
            width: '7rem',
            background: '#0a1018',
            color: TEXT_BRIGHT,
            border: `1px solid ${ACCENT_BORDER}`,
          }}
        />
        <button
          type="submit"
          disabled={createMut.isPending || goal.trim().length < 4}
          className="px-3 py-1 text-xs rounded border border-[#60a5fa]/30 bg-[#60a5fa]/15 text-[#60a5fa] hover:bg-[#60a5fa]/25 disabled:opacity-50"
        >
          {createMut.isPending ? 'Filing…' : 'File run'}
        </button>
      </div>
      {error && (
        <div className="text-[10px] text-[#f87171]">{error}</div>
      )}
    </form>
  );
}

// ── Master-switch warning banner ───────────────────────────────────

function MasterSwitchBanner() {
  const settingsQ = useRuntimeSettingsQuery();
  const enabled = settingsQ.data?.autonomous_executor_enabled === true;
  if (settingsQ.isLoading || enabled) return null;
  return (
    <div
      className="rounded-lg p-3 border text-xs"
      style={{
        background: '#7f1d1d22',
        borderColor: '#f87171',
        color: '#f87171',
      }}
    >
      ⚠{' '}
      <strong>Autonomous executor is OFF.</strong> Runs filed here land
      in <code>CREATED</code> status but won't advance until you flip{' '}
      <code>autonomous_executor_enabled</code> in /cp/settings.
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────

export function DelegatePage() {
  const activeQ = useDelegateRunsQuery('active');
  const terminalQ = useDelegateRunsQuery('terminal');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggle = (runId: string) => {
    setExpanded((prev) => ({ ...prev, [runId]: !prev[runId] }));
  };

  const activeRuns = activeQ.data?.runs ?? [];
  const terminalRuns = terminalQ.data?.runs ?? [];

  return (
    <div className="space-y-4 max-w-5xl">
      <div>
        <h1
          className="text-xl font-semibold"
          style={{ color: TEXT_BRIGHT }}
        >
          Delegate
        </h1>
        <p className="text-xs mt-1" style={{ color: TEXT_DIM }}>
          Autonomous executor runs. File a goal, watch the scheduler
          advance it step-by-step. Auto-refreshes every 5 s. Same data
          backs the <code>/delegate</code> Signal slash command.
        </p>
      </div>

      <MasterSwitchBanner />
      <NewRunComposer />

      {/* Active runs */}
      <section>
        <h2
          className="text-sm font-medium mb-2"
          style={{ color: TEXT_BRIGHT }}
        >
          Active runs{' '}
          <span style={{ color: TEXT_DIM, fontWeight: 'normal' }}>
            ({activeRuns.length})
          </span>
        </h2>
        {activeQ.isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : activeQ.error ? (
          <div className="text-xs text-[#f87171]">
            Failed to load active runs: {String(activeQ.error)}
          </div>
        ) : activeRuns.length === 0 ? (
          <div
            className="text-xs italic px-3 py-2 rounded border"
            style={{
              color: TEXT_DIM,
              background: ACCENT_BG,
              borderColor: ACCENT_BORDER,
            }}
          >
            (no active runs — file one above)
          </div>
        ) : (
          <div className="space-y-2">
            {activeRuns.map((run) => (
              <RunRow
                key={run.run_id}
                run={run}
                expanded={!!expanded[run.run_id]}
                onToggle={() => toggle(run.run_id)}
              />
            ))}
          </div>
        )}
      </section>

      {/* Terminal runs */}
      <section>
        <h2
          className="text-sm font-medium mb-2"
          style={{ color: TEXT_BRIGHT }}
        >
          Recent terminal runs{' '}
          <span style={{ color: TEXT_DIM, fontWeight: 'normal' }}>
            ({terminalRuns.length})
          </span>
        </h2>
        {terminalQ.isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : terminalRuns.length === 0 ? (
          <div
            className="text-xs italic px-3 py-2 rounded border"
            style={{
              color: TEXT_DIM,
              background: ACCENT_BG,
              borderColor: ACCENT_BORDER,
            }}
          >
            (no terminal runs yet)
          </div>
        ) : (
          <div className="space-y-2">
            {terminalRuns.map((run) => (
              <RunRow
                key={run.run_id}
                run={run}
                expanded={!!expanded[run.run_id]}
                onToggle={() => toggle(run.run_id)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

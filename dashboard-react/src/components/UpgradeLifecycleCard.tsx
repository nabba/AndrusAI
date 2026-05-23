// Upgrade-lifecycle subsystem control card — PROGRAM §62.
//
// One panel surfaces:
//   * 7 master switches (top + 6 stage-level)
//   * quarterly LLM budget slider
//   * current spend + remaining + CRs filed this week
//   * latest ecosystem snapshot link
//
// The six stage switches are conjunctive at the operator-policy
// level — when the top-level `upgrade_lifecycle_enabled` is off,
// every stage's effective work pipeline goes dark regardless of its
// own toggle (no CRs produced upstream → apply-hook has nothing to
// dispatch). Disabled-but-on toggles are rendered dim so the
// operator sees the inheritance.

import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { RuntimeSettings } from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT = '#5ec8a8';
const WARN = '#f87171';

type LifecycleState = {
  switches: Record<string, boolean>;
  quarterly_budget_usd: number;
  budget_used_usd: number | null;
  budget_remaining_usd: number | null;
  crs_this_week: number | null;
  latest_snapshot_year: number | null;
  available_snapshot_years: number[];
  capability_packages_count: number;
};

export function UpgradeLifecycleCard({
  settings: _settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const [state, setState] = useState<LifecycleState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [budgetInput, setBudgetInput] = useState<string>('');

  const refresh = async () => {
    setError(null);
    try {
      const data = await api('/api/cp/upgrade-lifecycle/state');
      setState(data as LifecycleState);
      setBudgetInput(String((data as LifecycleState).quarterly_budget_usd));
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const updateSwitch = async (key: string, value: boolean) => {
    setError(null);
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify({ [key]: value }),
      });
      onSettingsChange();
      void refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const updateBudget = async () => {
    setError(null);
    const v = parseFloat(budgetInput);
    if (Number.isNaN(v) || v < 0 || v > 500) {
      setError('Budget must be between 0 and 500 USD');
      return;
    }
    try {
      await api('/api/cp/settings', {
        method: 'POST',
        body: JSON.stringify({
          upgrade_lifecycle_capability_budget_usd_quarterly: v,
        }),
      });
      onSettingsChange();
      void refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  if (state === null) {
    return (
      <div
        className="rounded-lg p-4 border"
        style={{ background: '#111820', borderColor: '#1e2738' }}
      >
        <p className="text-sm" style={{ color: TEXT_DIM }}>
          {error ? error : 'Loading upgrade-lifecycle state…'}
        </p>
      </div>
    );
  }

  const topEnabled = state.switches.upgrade_lifecycle_enabled;
  const applyHookOn = !!state.switches.upgrade_lifecycle_apply_hook_enabled;
  const stageSwitches: { key: string; label: string; nested?: boolean }[] = [
    {
      key: 'upgrade_lifecycle_capability_extraction_enabled',
      label: 'U1 — capability extraction (changelog LLM parse)',
    },
    {
      key: 'upgrade_lifecycle_trial_enabled',
      label: 'U3 — upgrade trial harness (pip + pytest in worktree)',
    },
    {
      key: 'upgrade_lifecycle_major_auto_cr_enabled',
      label: 'U4 — MAJOR auto-CR gate (5 conditions)',
    },
    {
      key: 'upgrade_lifecycle_capability_adoption_enabled',
      label: 'U5 — capability adoption (1 CR/wk, quarterly budget)',
    },
    {
      key: 'ecosystem_snapshot_enabled',
      label: 'U6 — annual ecosystem snapshot (January)',
    },
    {
      key: 'upgrade_lifecycle_apply_hook_enabled',
      label:
        'Apply-hook daemon — dispatches approved upgrade CRs to ' +
        'requirements/pyproject/Dockerfile writers. Only path that ' +
        'auto-mutates code on the absence-policy lane.',
    },
    // Writer switches. Visually nested under apply-hook so the
    // operator sees they're the actual mutation points the daemon
    // dispatches to. Independently switchable so the operator can
    // pre-stage them OR keep apply-hook on for a path subset.
    {
      key: 'upgrade_lifecycle_requirements_writer_enabled',
      label:
        'requirements.txt writer (pip projects) — mutates the single ' +
        'matching line on approved bump_requirement CRs.',
      nested: true,
    },
    {
      key: 'upgrade_lifecycle_pyproject_writer_enabled',
      label:
        'pyproject.toml writer (uv / poetry / pdm) — mutates ' +
        '[project.dependencies] or [tool.poetry.dependencies]. ' +
        'Lockfile regen still required after apply.',
      nested: true,
    },
    {
      key: 'upgrade_lifecycle_dockerfile_writer_enabled',
      label:
        'Dockerfile writer — mutates `FROM python:` on approved ' +
        'bump_python CRs. SHA digest pin is dropped on bump; operator ' +
        're-pins before next deploy (TODO comment inserted).',
      nested: true,
    },
  ];

  return (
    <div
      className="rounded-lg p-4 border space-y-3"
      style={{ background: '#111820', borderColor: '#1e2738' }}
    >
      <div>
        <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Upgrade lifecycle (PROGRAM §62)
        </h2>
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Closes the dependency_radar gap on capability extraction +
          impact analysis + upgrade trials + auto-CR for clean MAJORs
          + annual ecosystem snapshot. All LLM calls go through the
          factory. Operator decides what to take.
        </p>
      </div>

      {/* Top-level switch */}
      <label
        className="flex items-start gap-2 text-xs"
        style={{ color: TEXT_BRIGHT }}
      >
        <input
          type="checkbox"
          checked={topEnabled}
          onChange={(e) =>
            updateSwitch('upgrade_lifecycle_enabled', e.target.checked)
          }
        />
        <span>
          <strong>Master switch</strong> — when off, every stage below
          short-circuits regardless of its own toggle.
        </span>
      </label>

      {/* Stage switches */}
      <div className="space-y-1.5 pl-3 border-l" style={{ borderColor: '#1e2738' }}>
        {stageSwitches.map((row) => {
          const isNested = row.nested === true;
          // Dim writer rows when apply-hook is off — they remain
          // clickable so the operator can pre-stage, but the visual
          // dependency on apply-hook is obvious.
          const rowEnabled = topEnabled && (!isNested || applyHookOn);
          return (
            <label
              key={row.key}
              className={`flex items-start gap-2 text-xs ${isNested ? 'ml-6' : ''}`}
              style={{ color: rowEnabled ? TEXT_BRIGHT : TEXT_DIM }}
            >
              <input
                type="checkbox"
                checked={state.switches[row.key] || false}
                disabled={!topEnabled}
                onChange={(e) => updateSwitch(row.key, e.target.checked)}
              />
              <span>{row.label}</span>
            </label>
          );
        })}
      </div>

      {/* Budget */}
      <div className="pt-2 border-t" style={{ borderColor: '#1e2738' }}>
        <div className="text-xs mb-1" style={{ color: TEXT_BRIGHT }}>
          Quarterly LLM budget (U5)
        </div>
        <div className="flex items-center gap-2">
          <input
            type="number"
            min="0"
            max="500"
            step="0.5"
            value={budgetInput}
            onChange={(e) => setBudgetInput(e.target.value)}
            className="px-2 py-1 text-xs rounded"
            style={{ background: '#0c1219', color: TEXT_BRIGHT, width: '6rem' }}
          />
          <span className="text-xs" style={{ color: TEXT_DIM }}>USD</span>
          <button
            onClick={updateBudget}
            className="px-2 py-1 text-xs rounded"
            style={{ background: ACCENT, color: '#0a0e14' }}
          >
            Save
          </button>
        </div>
        <p className="text-[10px] mt-2" style={{ color: TEXT_DIM }}>
          Spend this quarter:{' '}
          <span style={{ color: TEXT_BRIGHT }}>
            ${state.budget_used_usd?.toFixed(2) ?? '0.00'}
          </span>{' '}
          /
          <span style={{ color: TEXT_BRIGHT }}>
            {' '}${state.quarterly_budget_usd.toFixed(2)}
          </span>{' '}
          (remaining{' '}
          <span style={{ color: TEXT_BRIGHT }}>
            ${state.budget_remaining_usd?.toFixed(2) ?? '?'}
          </span>
          ). CRs filed this ISO week:{' '}
          <span style={{ color: TEXT_BRIGHT }}>
            {state.crs_this_week ?? 0}
          </span>
          /1 hard cap.
        </p>
      </div>

      {/* Snapshot link */}
      <div className="pt-2 border-t" style={{ borderColor: '#1e2738' }}>
        <p className="text-[10px]" style={{ color: TEXT_DIM }}>
          Latest annual snapshot:{' '}
          {state.latest_snapshot_year ? (
            <a
              href={`/cp/ecosystem`}
              style={{ color: ACCENT, textDecoration: 'underline' }}
            >
              {state.latest_snapshot_year}
            </a>
          ) : (
            <span style={{ color: TEXT_BRIGHT }}>not yet generated</span>
          )}
          . Capabilities tracked for{' '}
          <span style={{ color: TEXT_BRIGHT }}>
            {state.capability_packages_count}
          </span>{' '}
          package(s).
        </p>
      </div>

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

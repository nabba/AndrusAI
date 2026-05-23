// Verification Extension card — Phase 1 piece 1 (2026-05-20).
//
// Surfaces the four runtime knobs that gate the new evaluators inside
// `app.epistemic.orchestrator_hook.gate_output`:
//   1. Master switch (verification_extension_enabled, default OFF)
//   2. Per-zone confidence thresholds (chat / autonomous / financial)
//   3. Per-task retrieval budget (0 disables retrieval entirely)
//   4. Optional override of EPISTEMIC_ENABLED / EPISTEMIC_BLOCKING_MODE
//      env vars (null → fall through to env)
//
// Safety semantics:
//   - Master switch OFF → evaluators are no-ops; gate behaves
//     bit-identically to pre-extension behaviour.
//   - Master switch ON → evaluators can only ESCALATE the verdict
//     (claim-source consistency, retrieval-on-low-confidence,
//     zone-aware threshold). They never weaken calibration's verdict.
//   - Thresholds in [0.0, 1.0]; refused at the setter outside that
//     range.
//   - Retrieval budget in [0, 10]; v1 default is 1.

import { useState } from 'react';
import { api } from '../api/client';
import type { RuntimeSettings } from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const WARN_BG = '#7f1d1d22';
const ACCENT_BG = '#1e2738';
const ACCENT_BORDER = '#2a3a52';

type Zone = 'chat' | 'autonomous' | 'financial';

const ZONE_LABELS: Record<Zone, { title: string; description: string }> = {
  chat: {
    title: 'Chat zone',
    description:
      'Default zone for ordinary user-facing replies. Conservative starter ' +
      'threshold — claims with no registered source produce a hedge.',
  },
  autonomous: {
    title: 'Autonomous zone',
    description:
      'For tasks dispatched without an operator in the loop (the autonomous ' +
      'executor in Phase 2). Stricter threshold; low-confidence sources may ' +
      'escalate to peer review.',
  },
  financial: {
    title: 'Financial zone',
    description:
      'Highest strictness — for replies that touch real-money state. ' +
      'Retrieval that finds no supporting evidence escalates to peer_review.',
  },
};

export function VerificationExtensionCard({
  settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const enabled = settings.verification_extension_enabled === true;
  const thresholds: Record<Zone, number> = {
    chat: settings.verification_threshold_chat ?? 0.6,
    autonomous: settings.verification_threshold_autonomous ?? 0.9,
    financial: settings.verification_threshold_financial ?? 0.95,
  };
  const retrievalBudget =
    settings.verification_extension_retrieval_budget_per_task ?? 1;

  // null / undefined → fall through to env. Local component state lets
  // the user choose between "use env" and "override true/false".
  const enabledOverride =
    settings.epistemic_enabled_override === undefined
      ? null
      : (settings.epistemic_enabled_override as boolean | null);
  const blockingOverride =
    settings.epistemic_blocking_mode_override === undefined
      ? null
      : (settings.epistemic_blocking_mode_override as boolean | null);

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

  return (
    <div
      className="rounded-lg p-4 border space-y-4"
      style={{ background: '#111820', borderColor: '#1e2738' }}
    >
      <div>
        <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Verification extension (gate_output evaluators)
        </h2>
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Four additive evaluators inside{' '}
          <code>app.epistemic.orchestrator_hook.gate_output</code> that
          sit between <code>calibration_check</code> and the dispatch
          arms. With the master switch OFF (default), evaluators are
          no-ops and the gate behaves bit-identically to today. With ON,
          evaluators can only ESCALATE the calibration verdict —
          never weaken it. Hot-path safe; any internal failure falls
          through to the original verdict.
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

      <Toggle
        label="Verification extension (master)"
        checked={enabled}
        onChange={(v) => update({ verification_extension_enabled: v })}
        caveat={
          'When OFF, the gate runs calibration_check only and dispatches ' +
          'directly. When ON, the 4 evaluators (claim-source consistency, ' +
          'retrieval-on-low-confidence, zone-aware threshold, aggregator) ' +
          'run between calibration and dispatch. They can only escalate ' +
          'the suggested action; never weaken it.'
        }
      />

      {/* Per-zone thresholds */}
      <section
        className="rounded p-3 border space-y-3"
        style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
      >
        <h3 className="text-xs font-medium" style={{ color: TEXT_BRIGHT }}>
          Per-zone confidence thresholds
        </h3>
        <p className="text-[10px]" style={{ color: TEXT_DIM }}>
          Each zone (chat / autonomous / financial) sets the minimum
          source-confidence below which the claim-source evaluator
          escalates from ship → hedge → verify. Range 0.0 – 1.0.
        </p>
        {(Object.keys(ZONE_LABELS) as Zone[]).map((zone) => (
          <ZoneSlider
            key={zone}
            zone={zone}
            value={thresholds[zone]}
            onChange={(v) =>
              update({ [`verification_threshold_${zone}`]: v })
            }
          />
        ))}
      </section>

      {/* Retrieval budget */}
      <section
        className="rounded p-3 border"
        style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
      >
        <h3 className="text-xs font-medium mb-2" style={{ color: TEXT_BRIGHT }}>
          Retrieval budget per task
        </h3>
        <p className="text-[10px] mb-2" style={{ color: TEXT_DIM }}>
          Max number of <code>web_search</code> retrievals the
          retrieval-on-low-confidence evaluator may run per task.{' '}
          <strong>0 disables retrieval entirely</strong> (the evaluator
          returns None without consuming budget). Range 0 – 10.
        </p>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={0}
            max={10}
            step={1}
            value={retrievalBudget}
            onChange={(e) =>
              update({
                verification_extension_retrieval_budget_per_task: Number(
                  e.target.value,
                ),
              })
            }
            className="flex-1"
          />
          <code
            className="text-xs font-mono"
            style={{ color: TEXT_BRIGHT, minWidth: '2rem', textAlign: 'right' }}
          >
            {retrievalBudget}
          </code>
        </div>
      </section>

      {/* Env-var overlays */}
      <section
        className="rounded p-3 border space-y-3"
        style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
      >
        <h3 className="text-xs font-medium" style={{ color: TEXT_BRIGHT }}>
          Env-var overlays
        </h3>
        <p className="text-[10px]" style={{ color: TEXT_DIM }}>
          Lets you override <code>EPISTEMIC_ENABLED</code> and{' '}
          <code>EPISTEMIC_BLOCKING_MODE</code> without restarting the
          gateway. "Use env" (default) reads the env var; True/False
          force the value regardless of env.
        </p>
        <TriState
          label="EPISTEMIC_ENABLED override"
          value={enabledOverride}
          onChange={(v) => update({ epistemic_enabled_override: v })}
        />
        <TriState
          label="EPISTEMIC_BLOCKING_MODE override"
          value={blockingOverride}
          onChange={(v) => update({ epistemic_blocking_mode_override: v })}
        />
      </section>
    </div>
  );
}

function ZoneSlider({
  zone,
  value,
  onChange,
}: {
  zone: Zone;
  value: number;
  onChange: (v: number) => void;
}) {
  const meta = ZONE_LABELS[zone];
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs" style={{ color: TEXT_BRIGHT, fontWeight: 500 }}>
          {meta.title}
        </span>
        <code
          className="text-xs font-mono"
          style={{ color: TEXT_BRIGHT }}
        >
          {value.toFixed(2)}
        </code>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
      <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
        {meta.description}
      </p>
    </div>
  );
}

function TriState({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null;
  onChange: (v: boolean | null) => void;
}) {
  // null = "use env" (default). Three radio buttons.
  return (
    <div>
      <p className="text-xs mb-1" style={{ color: TEXT_BRIGHT, fontWeight: 500 }}>
        {label}
      </p>
      <div className="flex gap-3 text-xs" style={{ color: TEXT_DIM }}>
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="radio"
            checked={value === null}
            onChange={() => onChange(null)}
          />
          <span>Use env</span>
        </label>
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="radio"
            checked={value === true}
            onChange={() => onChange(true)}
          />
          <span style={{ color: TEXT_BRIGHT }}>True</span>
        </label>
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="radio"
            checked={value === false}
            onChange={() => onChange(false)}
          />
          <span>False</span>
        </label>
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

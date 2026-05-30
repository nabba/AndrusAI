// CR-quality gates card (2026-05-30).
//
// Operator control surface for the three gates that keep worthless
// change-requests out of the review queue:
//
//   Gate A — semantic rejection suppression. Re-files of ideas already
//     rejected multiple times (semantic match against the lessons KB) are
//     suppressed for observational producers. off / advisory / enforcing.
//   Gate B — evidence-gated library promotion. library_radar markdown-doc
//     proposals are only surfaced after a real PyPI resolution + green venv
//     smoke-import; the trial-backed adoption CR is the operator surface.
//   Gate C — per-producer approval-rate auto-pause. A producer whose rolling
//     operator-approval rate craters is auto-paused (self-releasing).
//
// All controls POST to /api/cp/settings via useUpdateRuntimeSettings, same
// path as every other settings card.

import { useEffect, useState } from 'react';
import { type RuntimeSettings, useUpdateRuntimeSettings } from '../api/queries';

type Mode = 'off' | 'advisory' | 'enforcing';

const MODE_OPTIONS: Array<{ value: Mode; label: string; detail: string; color: string }> = [
  {
    value: 'off',
    label: 'Off',
    detail: 'No semantic suppression. Every observational re-file reaches the queue.',
    color: '#7a8599',
  },
  {
    value: 'advisory',
    label: 'Advisory (default)',
    detail:
      "Logs 'WOULD suppress' for matches but still files the CR. Soak here first to confirm the gate flags the right things before it bites.",
    color: '#fbbf24',
  },
  {
    value: 'enforcing',
    label: 'Enforcing',
    detail:
      'Suppresses re-files semantically similar to repeatedly-rejected ideas (records them REJECTED instead of queuing). Cooldown, not a ban; novelty preserved by the count floor.',
    color: '#34d399',
  },
];

export function CrQualityGatesCard({ settings }: { settings: RuntimeSettings }) {
  const update = useUpdateRuntimeSettings();
  const [success, setSuccess] = useState('');

  const flash = (msg: string) => {
    setSuccess(msg);
    setTimeout(() => setSuccess(''), 2500);
  };
  const error = update.error instanceof Error ? update.error.message : '';

  // Gate A mode
  const mode = (settings.cr_rejection_suppression_mode as Mode) ?? 'advisory';
  const setMode = async (next: Mode) => {
    if (update.isPending || next === mode) return;
    try {
      await update.mutateAsync({ cr_rejection_suppression_mode: next });
      flash(`Gate A → ${next}`);
    } catch {
      /* surfaced via update.error */
    }
  };

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-[#e2e8f0]">CR-quality gates</h2>
        <p className="text-xs text-[#7a8599] mt-1">
          Keep worthless change-requests out of the review queue: suppress
          paraphrases of rejected ideas (A), only surface library proposals the
          system actually install+import verified (B), and auto-pause producers
          the operator keeps rejecting (C).
        </p>
      </div>

      {/* ── Gate A: semantic rejection suppression ── */}
      <div className="space-y-2">
        <div className="text-sm font-semibold text-[#e2e8f0]">
          Gate A · semantic rejection suppression
        </div>
        <div className="flex flex-col gap-1.5">
          {MODE_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className="flex items-start gap-2 cursor-pointer rounded p-2 border"
              style={{
                borderColor: mode === opt.value ? opt.color + '88' : '#1e2738',
                background: mode === opt.value ? opt.color + '11' : 'transparent',
              }}
            >
              <input
                type="radio"
                name="cr_rejection_suppression_mode"
                checked={mode === opt.value}
                onChange={() => setMode(opt.value)}
                disabled={update.isPending}
                className="mt-0.5 w-4 h-4"
                style={{ accentColor: opt.color }}
              />
              <span>
                <span className="text-sm" style={{ color: opt.color }}>
                  {opt.label}
                </span>
                <span className="block text-xs text-[#7a8599]">{opt.detail}</span>
              </span>
            </label>
          ))}
        </div>
        <div className="flex gap-4">
          <NumberSetting
            label="Similarity ≥"
            value={settings.cr_rejection_suppression_similarity ?? 0.55}
            min={0}
            max={1}
            step={0.05}
            disabled={update.isPending}
            onSave={async (v) => {
              await update.mutateAsync({ cr_rejection_suppression_similarity: v });
              flash('Gate A similarity saved');
            }}
          />
          <NumberSetting
            label="Seen ≥ (count)"
            value={settings.cr_rejection_suppression_min_count ?? 3}
            min={1}
            max={50}
            step={1}
            disabled={update.isPending}
            onSave={async (v) => {
              await update.mutateAsync({ cr_rejection_suppression_min_count: Math.round(v) });
              flash('Gate A min-count saved');
            }}
          />
        </div>
      </div>

      <div className="border-t border-[#1e2738]" />

      {/* ── Gate B: evidence-gated promotion ── */}
      <ToggleSetting
        title="Gate B · evidence-gated library promotion"
        detail="library_radar markdown-doc proposals are only promoted after a real PyPI resolution + green venv smoke-import. The trial-backed adoption CR (requirements.txt) is the operator surface. Off reverts to promote-the-doc-on-a-timer."
        checked={settings.library_radar_evidence_gated_promotion ?? true}
        disabled={update.isPending}
        onToggle={async (next) => {
          await update.mutateAsync({ library_radar_evidence_gated_promotion: next });
          flash(next ? 'Gate B on' : 'Gate B off');
        }}
      />

      <div className="border-t border-[#1e2738]" />

      {/* ── Gate C: per-producer auto-pause ── */}
      <div className="space-y-2">
        <ToggleSetting
          title="Gate C · per-producer approval-rate auto-pause"
          detail="Auto-pause an observational producer whose rolling operator-approval rate falls below the floor (with enough samples). Self-releasing cooldown; humans + bug-fix producers never paused; alerts via the producer_approval_health monitor."
          checked={settings.producer_autopause_enabled ?? true}
          disabled={update.isPending}
          onToggle={async (next) => {
            await update.mutateAsync({ producer_autopause_enabled: next });
            flash(next ? 'Gate C on' : 'Gate C off');
          }}
        />
        <div className="flex gap-4">
          <NumberSetting
            label="Approval floor"
            value={settings.producer_autopause_min_approval_rate ?? 0.15}
            min={0}
            max={1}
            step={0.05}
            disabled={update.isPending}
            onSave={async (v) => {
              await update.mutateAsync({ producer_autopause_min_approval_rate: v });
              flash('Gate C floor saved');
            }}
          />
          <NumberSetting
            label="Min samples"
            value={settings.producer_autopause_min_samples ?? 10}
            min={1}
            max={100}
            step={1}
            disabled={update.isPending}
            onSave={async (v) => {
              await update.mutateAsync({ producer_autopause_min_samples: Math.round(v) });
              flash('Gate C min-samples saved');
            }}
          />
          <NumberSetting
            label="Window (days)"
            value={settings.producer_autopause_window_days ?? 30}
            min={1}
            max={365}
            step={1}
            disabled={update.isPending}
            onSave={async (v) => {
              await update.mutateAsync({ producer_autopause_window_days: Math.round(v) });
              flash('Gate C window saved');
            }}
          />
        </div>
      </div>

      {error && <div className="text-[#f87171] text-sm">{error}</div>}
      {success && <div className="text-[#34d399] text-sm">{success}</div>}
    </div>
  );
}

function ToggleSetting({
  title,
  detail,
  checked,
  disabled,
  onToggle,
}: {
  title: string;
  detail: string;
  checked: boolean;
  disabled: boolean;
  onToggle: (next: boolean) => Promise<void>;
}) {
  const [local, setLocal] = useState(checked);
  useEffect(() => setLocal(checked), [checked]);
  return (
    <label className="flex items-start gap-3 cursor-pointer">
      <input
        type="checkbox"
        checked={local}
        disabled={disabled}
        onChange={async (e) => {
          const next = e.target.checked;
          setLocal(next);
          try {
            await onToggle(next);
          } catch {
            setLocal(!next);
          }
        }}
        className="mt-0.5 w-4 h-4 accent-[#60a5fa]"
      />
      <span>
        <span className="text-sm font-semibold text-[#e2e8f0]">{title}</span>
        <span className="block text-xs text-[#7a8599]">{detail}</span>
      </span>
    </label>
  );
}

function NumberSetting({
  label,
  value,
  min,
  max,
  step,
  disabled,
  onSave,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  onSave: (v: number) => Promise<void>;
}) {
  const [local, setLocal] = useState(String(value));
  useEffect(() => setLocal(String(value)), [value]);
  const commit = async () => {
    const v = Number(local);
    if (Number.isNaN(v) || v < min || v > max || v === value) {
      setLocal(String(value));
      return;
    }
    try {
      await onSave(v);
    } catch {
      setLocal(String(value));
    }
  };
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-[#7a8599]">{label}</span>
      <input
        type="number"
        value={local}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
        className="w-24 bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1 text-sm text-[#e2e8f0]"
      />
    </label>
  );
}

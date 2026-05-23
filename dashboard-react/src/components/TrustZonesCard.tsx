// Trust Zones + Risk Classifier card — Phase 1 piece 2 (2026-05-20).
//
// Wires the dormant AUTO_APPLY infrastructure (PROGRAM §38.3) to the
// operator. The source-pinned baselines at
// `app/change_requests/validator.py` stay empty (defends against silent
// widening); operator adds requestor / path entries here. The validator's
// `_effective_allowed_*()` unions baseline + this overlay.
//
// Safety semantics:
//   - Adding a requestor or path here does NOT auto-apply anything by
//     itself. The change_request validator still enforces TIER_IMMUTABLE,
//     forbidden prefixes, line cap, and additive-only on every request.
//   - Removing entries is instantly safe (back to dormant).
//   - The risk_classifier master switch reserves the toggle slot;
//     the module is a pure library with no production callers yet.
//
// Path validation mirrors the runtime_settings setter:
//   - Absolute paths refused (must be workspace-relative).
//   - Parent-traversal (`..`) refused.
//   - Sanity caps: 32 requestors, 64 paths.

import { useState } from 'react';
import { api } from '../api/client';
import type { RuntimeSettings } from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const WARN = '#f87171';
const WARN_BG = '#7f1d1d22';
const ACCENT_BG = '#1e2738';
const ACCENT_BORDER = '#2a3a52';

const MAX_REQUESTORS = 32;
const MAX_PATHS = 64;

export function TrustZonesCard({
  settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [requestorInput, setRequestorInput] = useState('');
  const [pathInput, setPathInput] = useState('');

  const requestors = settings.auto_apply_allowed_requestors ?? [];
  const paths = settings.auto_apply_allowed_paths ?? [];
  const classifierEnabled = settings.risk_classifier_enabled === true;

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

  const addRequestor = async () => {
    const v = requestorInput.trim();
    if (!v) return;
    if (requestors.includes(v)) {
      setError(`Requestor ${JSON.stringify(v)} already in allowlist`);
      return;
    }
    if (requestors.length >= MAX_REQUESTORS) {
      setError(
        `Sanity cap of ${MAX_REQUESTORS} requestors reached; remove one first`,
      );
      return;
    }
    await update({
      auto_apply_allowed_requestors: [...requestors, v],
    });
    setRequestorInput('');
  };

  const removeRequestor = async (v: string) => {
    await update({
      auto_apply_allowed_requestors: requestors.filter((x) => x !== v),
    });
  };

  const validatePathClientSide = (v: string): string | null => {
    if (!v) return 'path cannot be empty';
    if (v.startsWith('/')) {
      return 'absolute paths refused; use a workspace-relative path';
    }
    if (v.split('/').includes('..')) {
      return 'parent-traversal refused';
    }
    return null;
  };

  const addPath = async () => {
    const v = pathInput.trim();
    const validationErr = validatePathClientSide(v);
    if (validationErr) {
      setError(validationErr);
      return;
    }
    if (paths.includes(v)) {
      setError(`Path ${JSON.stringify(v)} already in allowlist`);
      return;
    }
    if (paths.length >= MAX_PATHS) {
      setError(`Sanity cap of ${MAX_PATHS} paths reached; remove one first`);
      return;
    }
    await update({ auto_apply_allowed_paths: [...paths, v] });
    setPathInput('');
  };

  const removePath = async (v: string) => {
    await update({
      auto_apply_allowed_paths: paths.filter((x) => x !== v),
    });
  };

  return (
    <div
      className="rounded-lg p-4 border space-y-4"
      style={{ background: '#111820', borderColor: '#1e2738' }}
    >
      <div>
        <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Trust zones &amp; AUTO_APPLY allowlists
        </h2>
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Operator-managed overlays on top of the source-pinned baseline
          in <code>app/change_requests/validator.py</code>. The baseline
          stays empty by design (defends against silent widening); every
          entry below comes from this card. Adding a requestor or path
          does NOT auto-apply anything by itself — the validator still
          enforces TIER_IMMUTABLE, forbidden prefixes, line cap, and
          additive-only on every request. Removing entries is instantly
          safe (back to dormant). See{' '}
          <code>crewai-team/docs/AUTO_APPLY.md</code> for the full design.
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
        label="Risk classifier master switch"
        checked={classifierEnabled}
        onChange={(v) => update({ risk_classifier_enabled: v })}
        caveat={
          'Master switch for the app.risk_classifier module. v1 ships ' +
          'the zone enum + deterministic decision tree as a pure ' +
          'library with no production callers yet — this toggle ' +
          'reserves the slot and gates future widening-proposal ' +
          'emission. Default OFF.'
        }
      />

      {/* Allowed requestors */}
      <section
        className="rounded p-3 border"
        style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
      >
        <h3
          className="text-xs font-medium mb-2"
          style={{ color: TEXT_BRIGHT }}
        >
          Allowed requestors ({requestors.length} / {MAX_REQUESTORS})
        </h3>
        <p className="text-[10px] mb-2" style={{ color: TEXT_DIM }}>
          Agent IDs permitted to file AUTO_APPLY change-requests. Empty
          (default) → the lane is dormant; every request goes through
          the standard operator gate.
        </p>

        <div className="flex gap-2 mb-2">
          <input
            type="text"
            value={requestorInput}
            onChange={(e) => setRequestorInput(e.target.value)}
            placeholder="e.g. self_heal_router"
            className="flex-1 px-2 py-1 text-xs rounded"
            style={{
              background: '#0a1018',
              color: TEXT_BRIGHT,
              border: `1px solid ${ACCENT_BORDER}`,
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addRequestor();
            }}
          />
          <button
            onClick={addRequestor}
            className="px-3 py-1 text-xs rounded"
            style={{
              background: '#1e3a52',
              color: TEXT_BRIGHT,
              border: `1px solid ${ACCENT_BORDER}`,
            }}
          >
            Add
          </button>
        </div>

        {requestors.length === 0 ? (
          <p className="text-[10px] italic" style={{ color: TEXT_DIM }}>
            (none — AUTO_APPLY lane is dormant)
          </p>
        ) : (
          <ul className="space-y-1">
            {requestors.map((r) => (
              <li
                key={r}
                className="flex items-center justify-between text-xs"
              >
                <code style={{ color: TEXT_BRIGHT }}>{r}</code>
                <button
                  onClick={() => removeRequestor(r)}
                  className="text-[10px] px-2 py-0.5 rounded"
                  style={{ color: WARN, background: WARN_BG }}
                  aria-label={`Remove ${r}`}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Allowed paths */}
      <section
        className="rounded p-3 border"
        style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
      >
        <h3
          className="text-xs font-medium mb-2"
          style={{ color: TEXT_BRIGHT }}
        >
          Allowed paths ({paths.length} / {MAX_PATHS})
        </h3>
        <p className="text-[10px] mb-2" style={{ color: TEXT_DIM }}>
          Workspace-relative paths permitted as AUTO_APPLY targets. Exact
          match by default; trailing <code>/</code> makes the entry a
          prefix match. Absolute paths and parent-traversal sequences are
          refused at input time.
        </p>

        <div className="flex gap-2 mb-2">
          <input
            type="text"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            placeholder="e.g. workspace/notes/ or workspace/output/file.txt"
            className="flex-1 px-2 py-1 text-xs rounded"
            style={{
              background: '#0a1018',
              color: TEXT_BRIGHT,
              border: `1px solid ${ACCENT_BORDER}`,
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addPath();
            }}
          />
          <button
            onClick={addPath}
            className="px-3 py-1 text-xs rounded"
            style={{
              background: '#1e3a52',
              color: TEXT_BRIGHT,
              border: `1px solid ${ACCENT_BORDER}`,
            }}
          >
            Add
          </button>
        </div>

        {paths.length === 0 ? (
          <p className="text-[10px] italic" style={{ color: TEXT_DIM }}>
            (none — AUTO_APPLY lane is dormant)
          </p>
        ) : (
          <ul className="space-y-1">
            {paths.map((p) => (
              <li
                key={p}
                className="flex items-center justify-between text-xs"
              >
                <code style={{ color: TEXT_BRIGHT }}>{p}</code>
                <button
                  onClick={() => removePath(p)}
                  className="text-[10px] px-2 py-0.5 rounded"
                  style={{ color: WARN, background: WARN_BG }}
                  aria-label={`Remove ${p}`}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
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

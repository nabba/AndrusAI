// Recent subsystems card — Phase E follow-up (2026-05-22).
//
// Operator-visible toggles for the new subsystems that don't already
// have a dedicated settings card. Each row is a single boolean
// runtime_setting flipped via the standard /api/cp/settings endpoint.
//
// Why a shared card instead of one card per setting?
//   The "one card per setting" pattern in /cp/settings is right for
//   subsystems that need OTHER controls (sliders, sub-toggles,
//   typed-phrase confirmation, etc). For plain "yes/no this subsystem
//   ships dormant" switches, a compact list is denser and easier to
//   scan.

import { useState } from 'react';
import {
  useUpdateRuntimeSettings,
  type RuntimeSettings,
} from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT_BG = '#1e2738';
const ACCENT_BORDER = '#2a3a52';
const OK = '#34d399';
const WARN = '#f87171';

type SwitchKey = keyof Pick<
  RuntimeSettings,
  'iterate_loop_enabled' | 'benchmarks_enabled'
>;

interface SwitchRow {
  key: SwitchKey;
  label: string;
  description: string;
  defaultPosture: 'off' | 'on';
  link?: { href: string; label: string };
}

const SWITCHES: SwitchRow[] = [
  {
    key: 'iterate_loop_enabled',
    label: 'Iterate-until-green agent tool',
    description:
      "Agent-callable coding_session_iterate that runs tests + " +
      "pyright in a loop, fixing failures up to a hard cap on " +
      "iterations / cost. Default OFF — the agent has to be " +
      "explicitly opted in.",
    defaultPosture: 'off',
  },
  {
    key: 'benchmarks_enabled',
    label: 'Benchmark suite (scheduled pass)',
    description:
      "Cross-model evaluation harness — runs the YAML catalog " +
      "against tiered targets ~ every 24h subject to a per-pass " +
      "cost cap. Default OFF — the catalog + leaderboard work " +
      "either way; this flag controls the scheduled refresh.",
    defaultPosture: 'off',
    link: { href: '/benchmarks', label: 'Open leaderboard →' },
  },
];

export function RecentSubsystemsCard({
  settings,
  onSettingsChange,
}: {
  settings?: RuntimeSettings | Partial<RuntimeSettings>;
  onSettingsChange: () => void;
}) {
  const update = useUpdateRuntimeSettings();
  const [errKey, setErrKey] = useState<string | null>(null);
  const [errMsg, setErrMsg] = useState<string>('');
  const [pendingKey, setPendingKey] = useState<string | null>(null);

  const handleToggle = async (
    key: SwitchKey,
    current: boolean | undefined,
  ) => {
    setErrKey(null);
    setErrMsg('');
    setPendingKey(key);
    try {
      await update.mutateAsync({ [key]: !current });
      onSettingsChange();
    } catch (e: unknown) {
      setErrKey(key);
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setPendingKey(null);
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
      <div style={{ marginBottom: '12px' }}>
        <h3
          style={{
            margin: 0, fontSize: '15px', fontWeight: 600,
            color: TEXT_BRIGHT,
          }}
        >
          Recent subsystems
        </h3>
        <p
          style={{
            margin: '4px 0 0 0', color: TEXT_DIM, fontSize: '12px',
          }}
        >
          New subsystems that ship dormant — flip on after operator
          review.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {SWITCHES.map(s => {
          const current = settings?.[s.key];
          const enabled = current === true;
          const isPending = pendingKey === s.key;
          return (
            <div
              key={s.key}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '12px',
                padding: '10px',
                background: '#0f1623',
                border: `1px solid ${ACCENT_BORDER}`,
                borderRadius: '4px',
              }}
            >
              <button
                onClick={() => handleToggle(s.key, enabled)}
                disabled={isPending}
                style={{
                  padding: '4px 12px',
                  minWidth: '76px',
                  background: enabled ? OK : ACCENT_BG,
                  color: enabled ? '#0f1623' : TEXT_BRIGHT,
                  border: `1px solid ${enabled ? OK : ACCENT_BORDER}`,
                  borderRadius: '12px',
                  cursor: isPending ? 'not-allowed' : 'pointer',
                  fontSize: '11px',
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                {isPending ? '…' : enabled ? 'ENABLED' : 'OFF'}
              </button>
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    color: TEXT_BRIGHT, fontSize: '13px', fontWeight: 600,
                  }}
                >
                  {s.label}
                </div>
                <div
                  style={{
                    color: TEXT_DIM, fontSize: '12px', marginTop: '2px',
                  }}
                >
                  {s.description}
                </div>
                {s.link && (
                  <a
                    href={s.link.href}
                    style={{
                      display: 'inline-block', marginTop: '4px',
                      color: '#60a5fa', fontSize: '12px',
                      textDecoration: 'none',
                    }}
                  >
                    {s.link.label}
                  </a>
                )}
                {errKey === s.key && (
                  <div
                    style={{
                      marginTop: '4px', color: WARN, fontSize: '11px',
                    }}
                  >
                    {errMsg}
                  </div>
                )}
              </div>
              <div
                style={{
                  color: TEXT_DIM, fontSize: '10px',
                  fontFamily: 'monospace', flexShrink: 0,
                  alignSelf: 'center',
                }}
              >
                default: {s.defaultPosture}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

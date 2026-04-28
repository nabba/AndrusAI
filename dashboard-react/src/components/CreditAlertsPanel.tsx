import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { endpoints } from '../api/endpoints';

// ── Types ─────────────────────────────────────────────────────────────────

interface CreditAlert {
  provider: string;
  error: string;
  url: string;
  ts: string;
  resolved?: boolean;
}

interface CreditAlertsResponse {
  alerts: Record<string, CreditAlert>;
  count: number;
}

// Provider display info — icon, friendly name, fallback URL when the
// backend doesn't include one (defensive only; backend always sets it).
const PROVIDER_META: Record<string, { name: string; icon: string; fallbackUrl: string }> = {
  openrouter: {
    name: 'OpenRouter',
    icon: '🔌',
    fallbackUrl: 'https://openrouter.ai/settings/credits',
  },
  anthropic: {
    name: 'Anthropic',
    icon: '🟣',
    fallbackUrl: 'https://console.anthropic.com/settings/billing',
  },
  openai: {
    name: 'OpenAI',
    icon: '🤖',
    fallbackUrl: 'https://platform.openai.com/settings/organization/billing',
  },
  google: {
    name: 'Google AI',
    icon: '🔷',
    fallbackUrl: 'https://console.cloud.google.com/billing',
  },
};


function metaFor(provider: string) {
  return PROVIDER_META[provider] ?? {
    name: provider,
    icon: '⚡',
    fallbackUrl: '',
  };
}


function formatTs(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const diffMs = Date.now() - d.getTime();
    const diffMin = Math.round(diffMs / 60_000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit' });
  } catch {
    return iso;
  }
}


// ── Hooks ─────────────────────────────────────────────────────────────────

function useCreditAlerts() {
  return useQuery<CreditAlertsResponse>({
    queryKey: ['credit-alerts'],
    queryFn: () => api<CreditAlertsResponse>(endpoints.creditAlerts()),
    // Poll often — credit-exhaustion is the kind of thing the user
    // wants to fix immediately. 15s is cheap (small endpoint).
    refetchInterval: 15_000,
  });
}


function useDismissCreditAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (provider: string) => {
      return fetch(endpoints.creditAlertDismiss(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      }).then((r) => {
        if (!r.ok) throw new Error(`Dismiss failed: ${r.status}`);
        return r.json();
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['credit-alerts'] }),
  });
}


// ── Component ─────────────────────────────────────────────────────────────

/**
 * Top-of-Budgets panel that shows active credit-depletion alerts per
 * provider. Each alert renders a card with the provider name, error
 * preview, "Add credits" button (opens the provider's billing page),
 * and a "Dismiss" button (manual clear, useful after topping up).
 *
 * Renders nothing when no alerts are active so it doesn't take up
 * space on the page during normal operation.
 */
export function CreditAlertsPanel() {
  const { data, error } = useCreditAlerts();
  const dismiss = useDismissCreditAlert();
  const [dismissing, setDismissing] = useState<string | null>(null);

  if (error) {
    // Don't fail the whole Budgets page — log and render nothing.
    return null;
  }
  const alerts = data?.alerts ?? {};
  const providers = Object.keys(alerts);
  if (providers.length === 0) return null;

  return (
    <div
      className="rounded-lg border p-4 space-y-3"
      style={{
        borderColor: '#f59e0b',
        background: 'rgba(245, 158, 11, 0.06)',
      }}
    >
      <div className="flex items-center gap-2">
        <span style={{ fontSize: '1.25rem' }}>⚠️</span>
        <h2 className="text-base font-semibold text-[#fbbf24]">
          Top-up needed: {providers.length === 1 ? '1 provider' : `${providers.length} providers`}
        </h2>
      </div>
      <p className="text-xs text-[#d1d5db]">
        These providers have returned credit-exhaustion errors. Recent calls have automatically
        failed over to local Ollama (the system is still working), but new requests routed to
        these providers will keep falling back until you top up.
      </p>

      <div className="space-y-2">
        {providers.map((provider) => {
          const alert = alerts[provider];
          const meta = metaFor(provider);
          const url = alert.url || meta.fallbackUrl;
          return (
            <div
              key={provider}
              className="rounded-md p-3 flex items-start justify-between gap-3"
              style={{
                background: '#0a0e14',
                border: '1px solid #1e2738',
              }}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span style={{ fontSize: '1rem' }}>{meta.icon}</span>
                  <strong className="text-[#e5e7eb]">{meta.name}</strong>
                  <span className="text-xs text-[#9ca3af]">· {formatTs(alert.ts)}</span>
                </div>
                {alert.error && (
                  <p
                    className="text-xs text-[#9ca3af] mt-1"
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}
                    title={alert.error}
                  >
                    {alert.error}
                  </p>
                )}
              </div>
              <div className="flex flex-col gap-2 flex-shrink-0">
                {url && (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center justify-center px-3 py-1.5 text-xs font-medium rounded transition-colors whitespace-nowrap"
                    style={{
                      background: '#f59e0b',
                      color: '#1f1300',
                    }}
                  >
                    Add credits ↗
                  </a>
                )}
                <button
                  type="button"
                  disabled={dismissing === provider}
                  onClick={async () => {
                    setDismissing(provider);
                    try {
                      await dismiss.mutateAsync(provider);
                    } finally {
                      setDismissing(null);
                    }
                  }}
                  className="inline-flex items-center justify-center px-3 py-1 text-xs rounded transition-colors whitespace-nowrap"
                  style={{
                    background: 'transparent',
                    border: '1px solid #4b5563',
                    color: '#d1d5db',
                  }}
                >
                  {dismissing === provider ? 'Dismissing…' : 'Dismiss'}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

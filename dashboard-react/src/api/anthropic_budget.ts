// Anthropic per-day cap API hooks — Phase D.3 (2026-05-22).
//
// Read + write the vendor-level Anthropic spend cap from /cp/settings.
// Mirrors src/api/connector_budget.ts for shape consistency.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

const A = '/api/cp/anthropic-budget';

// ── Types — mirror app.llm_anthropic_budget.state_snapshot ────────────

export interface AnthropicBudgetState {
  enabled: boolean;
  cap_usd: number | null;
  spent_usd_24h: number;
  headroom_usd: number | null;
  ok: boolean;
  error?: string;
}

export interface PreCheckResponse {
  would_refuse: boolean;
  reason: string;
  estimated_cost_usd: number;
  cap_usd: number | null;
  spent_usd_24h: number;
  headroom_usd: number | null;
  enabled: boolean;
}

export const anthropicBudgetEndpoints = {
  state: () => `${A}/state`,
  setCap: () => `${A}/cap`,
  preCheck: () => `${A}/pre-check`,
};

export const anthropicBudgetKeys = {
  state: () => ['anthropic-budget', 'state'] as const,
};

export function useAnthropicBudgetStateQuery() {
  return useQuery({
    queryKey: anthropicBudgetKeys.state(),
    queryFn: () =>
      api<AnthropicBudgetState>(anthropicBudgetEndpoints.state()),
    refetchInterval: 30_000,
  });
}

export function useSetAnthropicCapMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cap_usd: number | null) =>
      api<AnthropicBudgetState>(anthropicBudgetEndpoints.setCap(), {
        method: 'POST',
        body: JSON.stringify({ cap_usd }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['anthropic-budget'] });
    },
  });
}

export function usePreCheckAnthropicCapMutation() {
  return useMutation({
    mutationFn: (estimated_cost_usd: number) =>
      api<PreCheckResponse>(anthropicBudgetEndpoints.preCheck(), {
        method: 'POST',
        body: JSON.stringify({ estimated_cost_usd }),
      }),
  });
}

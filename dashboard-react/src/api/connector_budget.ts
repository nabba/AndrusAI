// Connector-budget API hooks (2026-05-22).
//
// Read-only view over the per-connector daily-spend ledger. Polls
// every 30s so the operator's view stays fresh during high-volume
// days; the load is trivial (one file scan per poll).

import { useQuery } from '@tanstack/react-query';
import { api } from './client';

const C = '/api/cp/connector-budget';

export interface ConnectorSpend {
  connector: string;
  today_spend_usd: number;
  today_calls: number;
  today_estimated_calls: number;
  // Phase 3 v2 follow-up (2026-05-22) — last-N-day rolling totals.
  // `recent_window_days` carries the N the server used (currently 7).
  recent_spend_usd: number;
  recent_calls: number;
  recent_window_days: number;
}

export interface ConnectorBudgetState {
  enabled: boolean;
  connectors: ConnectorSpend[];
  total_usd: number;
  total_calls: number;
  total_recent_usd: number;
  total_recent_calls: number;
  recent_window_days: number;
}

export const connectorBudgetEndpoints = {
  state: () => `${C}/state`,
};

export const connectorBudgetKeys = {
  state: () => ['connector-budget', 'state'] as const,
};

export function useConnectorBudgetStateQuery() {
  return useQuery({
    queryKey: connectorBudgetKeys.state(),
    queryFn: () =>
      api<ConnectorBudgetState>(connectorBudgetEndpoints.state()),
    refetchInterval: 30_000,
  });
}

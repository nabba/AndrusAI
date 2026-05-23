// Capability-regression API hooks (2026-05-22).
//
// Read-only operator surface over the snapshot history + detected
// regressions written by the hourly idle-scheduler pass. The daemon
// is the only writer; this layer just exposes the JSONL ledger.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

const C = '/api/cp/capability-regression';

// ── Types: mirror app.capability_regression ─────────────────────────

export interface CapabilitySnapshot {
  schema_version: number;
  captured_at: string;
  tools: string[];
  models: string[];
  blocked_models: string[];
}

export interface RegressionReport {
  tools_deleted: string[];
  models_truly_deleted: string[];
  models_newly_blocked: string[];
  prev_captured_at: string;
  curr_captured_at: string;
  has_regression: boolean;
}

export interface RegressionState {
  enabled: boolean;
  current_snapshot: CapabilitySnapshot | null;
  last_regression: RegressionReport | null;
}

export interface HistoryResponse {
  count: number;
  snapshots: CapabilitySnapshot[];
}

export interface RegressionsResponse {
  count: number;
  regressions: RegressionReport[];
}

export const capabilityRegressionEndpoints = {
  state: () => `${C}/state`,
  history: (limit = 24) => `${C}/history?limit=${limit}`,
  regressions: (limit = 50) => `${C}/regressions?limit=${limit}`,
  snapshot: () => `${C}/snapshot`,
};

export interface ForceSnapshotResponse {
  ran: boolean;
  reason?: string;
  snapshot?: CapabilitySnapshot | null;
  regression?: RegressionReport | null;
}

export const capabilityRegressionKeys = {
  state: () => ['capability-regression', 'state'] as const,
  history: (limit: number) =>
    ['capability-regression', 'history', limit] as const,
  regressions: (limit: number) =>
    ['capability-regression', 'regressions', limit] as const,
};

// ── Read hooks ──────────────────────────────────────────────────────

export function useCapabilityRegressionStateQuery() {
  return useQuery({
    queryKey: capabilityRegressionKeys.state(),
    queryFn: () =>
      api<RegressionState>(capabilityRegressionEndpoints.state()),
    refetchInterval: 30_000,
  });
}

export function useCapabilityRegressionHistoryQuery(limit = 24) {
  return useQuery({
    queryKey: capabilityRegressionKeys.history(limit),
    queryFn: () =>
      api<HistoryResponse>(capabilityRegressionEndpoints.history(limit)),
    refetchInterval: 60_000,
  });
}

export function useCapabilityRegressionRegressionsQuery(limit = 50) {
  return useQuery({
    queryKey: capabilityRegressionKeys.regressions(limit),
    queryFn: () =>
      api<RegressionsResponse>(
        capabilityRegressionEndpoints.regressions(limit),
      ),
    refetchInterval: 60_000,
  });
}

// Operator-triggered one-shot snapshot run (2026-05-22).
// Useful for verification — operators don't have to wait up to an
// hour for the next idle pass.
export function useForceCapabilitySnapshotMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<ForceSnapshotResponse>(
        capabilityRegressionEndpoints.snapshot(),
        { method: 'POST' },
      ),
    onSuccess: () => {
      // Refresh every cached query so the new snapshot lands in the UI.
      qc.invalidateQueries({ queryKey: ['capability-regression'] });
    },
  });
}

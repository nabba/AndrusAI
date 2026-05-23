// Benchmark suite API hooks — Phase C.3 (2026-05-22).
//
// Read-only operator visibility into the cross-model leaderboard plus
// one mutation (force a catalog pass). Mirrors src/api/widening.ts for
// shape consistency.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

const B = '/api/cp/benchmarks';

// ── Types — mirror app.benchmarks.{models, aggregator, catalog} ──────

export interface BenchmarkTaskListItem {
  id: string;
  name: string;
  description: string;
  category: string;
  scorer: string;
  model_targets: string[];
  timeout_s: number;
  max_tokens: number | null;
}

export interface CatalogStats {
  task_count: number;
  by_category: Record<string, number>;
  by_scorer: Record<string, number>;
}

export interface CatalogResponse {
  enabled: boolean;
  tasks: BenchmarkTaskListItem[];
  stats: CatalogStats;
  error?: string;
}

export interface BenchmarkRunRow {
  task_id: string;
  model: string;
  ts: string;
  score: number;
  passed: boolean;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  output_preview: string;
  error: string;
}

export interface RunsResponse {
  enabled: boolean;
  n_total: number;
  n_returned: number;
  runs: BenchmarkRunRow[];
  error?: string;
}

export interface BenchmarkSummary {
  n: number;
  n_passed: number;
  n_errored: number;
  mean_score: number;
  pass_rate: number;
  error_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  total_cost_usd: number;
  total_tokens_in: number;
  total_tokens_out: number;
}

export interface LeaderboardResponse {
  enabled: boolean;
  window_days: number;
  n_runs: number;
  by_model: Record<string, BenchmarkSummary>;
  by_task: Record<string, BenchmarkSummary>;
  matrix: Record<string, BenchmarkSummary>;
  error?: string;
}

export interface BenchmarkStatsResponse {
  enabled: boolean;
  rows: number;
  bytes: number;
  last_ts: string;
  error?: string;
}

export interface RefreshResponse {
  ran: boolean;
  skipped_reason: string;
  n_runs: number;
  elapsed_s: number;
  cost_usd: number;
  error: string;
}

export const benchmarksEndpoints = {
  catalog: () => `${B}/catalog`,
  runs: (params: {
    task_id?: string;
    model?: string;
    window_days?: number;
    limit?: number;
  }) => {
    const q = new URLSearchParams();
    if (params.task_id) q.set('task_id', params.task_id);
    if (params.model) q.set('model', params.model);
    if (params.window_days) q.set('window_days', String(params.window_days));
    if (params.limit) q.set('limit', String(params.limit));
    const s = q.toString();
    return s ? `${B}/runs?${s}` : `${B}/runs`;
  },
  leaderboard: (window_days = 7) =>
    `${B}/leaderboard?window_days=${window_days}`,
  stats: () => `${B}/stats`,
  refresh: (force = false) => `${B}/refresh?force=${force ? 'true' : 'false'}`,
};

export const benchmarksKeys = {
  catalog: () => ['benchmarks', 'catalog'] as const,
  runs: (p: object) => ['benchmarks', 'runs', p] as const,
  leaderboard: (window_days: number) =>
    ['benchmarks', 'leaderboard', window_days] as const,
  stats: () => ['benchmarks', 'stats'] as const,
};

// ── Read hooks ───────────────────────────────────────────────────────

export function useBenchmarksCatalogQuery() {
  return useQuery({
    queryKey: benchmarksKeys.catalog(),
    queryFn: () => api<CatalogResponse>(benchmarksEndpoints.catalog()),
    refetchInterval: 60_000,
  });
}

export function useBenchmarksLeaderboardQuery(windowDays = 7) {
  return useQuery({
    queryKey: benchmarksKeys.leaderboard(windowDays),
    queryFn: () =>
      api<LeaderboardResponse>(benchmarksEndpoints.leaderboard(windowDays)),
    refetchInterval: 30_000,
  });
}

export function useBenchmarksRunsQuery(params: {
  task_id?: string;
  model?: string;
  window_days?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: benchmarksKeys.runs(params),
    queryFn: () => api<RunsResponse>(benchmarksEndpoints.runs(params)),
    refetchInterval: 30_000,
  });
}

export function useBenchmarksStatsQuery() {
  return useQuery({
    queryKey: benchmarksKeys.stats(),
    queryFn: () =>
      api<BenchmarkStatsResponse>(benchmarksEndpoints.stats()),
    refetchInterval: 60_000,
  });
}

// ── Mutation — force a catalog pass ──────────────────────────────────

export function useForceBenchmarksRefreshMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean = true) =>
      api<RefreshResponse>(benchmarksEndpoints.refresh(force), {
        method: 'POST',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['benchmarks'] });
    },
  });
}

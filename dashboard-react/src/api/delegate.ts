// Autonomous executor (/delegate) API hooks — Phase 2 piece 2f (2026-05-20).
//
// Operators see active + terminal runs at /cp/delegate, kick off new runs
// with a goal + optional budget, and abort. Mirrors src/api/workflows.ts
// for shape consistency.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

const D = '/api/cp/delegate';

// ── Types: mirror app.autonomous_executor.models.{ExecutorRun, ExecutorStep, Budget} ──

export type ExecutorStatus =
  | 'created'
  | 'planning'
  | 'running'
  | 'blocked'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'budget_exhausted'
  | 'aborted';

export type StepStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped';

export interface ExecutorStep {
  step_id: string;
  description: string;
  crew_hint: string;
  status: StepStatus;
  result_text: string;
  failure_reason: string;
  cost_usd: number;
  tokens_used: number;
  started_at: string;
  ended_at: string;
  // Phase A.2 closure (2026-05-22) — CRs attributed to this step.
  // Populated by the driver post-step from
  // attribute_crs_to_step(). Empty for steps that produced no CRs.
  cr_ids: string[];
}

export interface ExecutorBudget {
  cap_usd: number;
  cap_tokens: number;
  cap_wall_clock_s: number;
  spent_usd: number;
  spent_tokens: number;
  elapsed_s_at_save: number;
}

export interface ExecutorRun {
  run_id: string;
  goal: string;
  requestor: string;
  zone: string;
  status: ExecutorStatus;
  plan: ExecutorStep[];
  notes: string[];
  budget: ExecutorBudget;
  created_at: string;
  started_at: string;
  ended_at: string;
  last_touched_at: string;
  failure_reason: string;
  abort_reason: string;
  blocked_reason: string;
  pause_reason: string;
  is_terminal: boolean;
}

export interface DelegateCreateBody {
  goal: string;
  budget_usd?: number;
  budget_tokens?: number;
  budget_wall_clock_s?: number;
  zone?: string;
  requestor?: string;
}

export type DelegateListFilter = 'active' | 'terminal' | 'all';

export const delegateEndpoints = {
  list: (filter: DelegateListFilter, limit = 50) =>
    `${D}?status=${filter}&limit=${limit}`,
  detail: (runId: string) => `${D}/${encodeURIComponent(runId)}`,
  create: () => D,
  abort: (runId: string) =>
    `${D}/${encodeURIComponent(runId)}/abort`,
};

export const delegateKeys = {
  list: (filter: DelegateListFilter) =>
    ['delegate', 'list', filter] as const,
  detail: (runId: string) => ['delegate', 'detail', runId] as const,
};

// ── Read hooks ───────────────────────────────────────────────────────

export function useDelegateRunsQuery(filter: DelegateListFilter = 'all') {
  return useQuery({
    queryKey: delegateKeys.list(filter),
    queryFn: () =>
      api<{ count: number; runs: ExecutorRun[] }>(
        delegateEndpoints.list(filter),
      ),
    refetchInterval: 5_000,
  });
}

export function useDelegateRunQuery(runId: string | undefined) {
  return useQuery({
    queryKey: delegateKeys.detail(runId ?? ''),
    queryFn: () =>
      api<ExecutorRun>(delegateEndpoints.detail(runId as string)),
    enabled: !!runId,
    refetchInterval: 5_000,
  });
}

// ── Write hooks ──────────────────────────────────────────────────────

export function useCreateDelegateRunMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DelegateCreateBody) =>
      api<ExecutorRun>(delegateEndpoints.create(), {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['delegate'] });
    },
  });
}

export function useAbortDelegateRunMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, reason }: { runId: string; reason?: string }) =>
      api<ExecutorRun>(delegateEndpoints.abort(runId), {
        method: 'POST',
        body: JSON.stringify({ reason: reason ?? 'operator-abort' }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['delegate'] });
    },
  });
}

// ── Helpers ──────────────────────────────────────────────────────────

export function formatStatus(status: ExecutorStatus): {
  bg: string;
  fg: string;
  label: string;
} {
  switch (status) {
    case 'created':
      return { bg: 'bg-[#7a8599]/15', fg: 'text-[#7a8599]', label: 'CREATED' };
    case 'planning':
      return { bg: 'bg-[#a78bfa]/15', fg: 'text-[#a78bfa]', label: 'PLANNING' };
    case 'running':
      return { bg: 'bg-[#22d3ee]/15', fg: 'text-[#22d3ee]', label: 'RUNNING' };
    case 'blocked':
      return { bg: 'bg-[#fbbf24]/15', fg: 'text-[#fbbf24]', label: 'BLOCKED' };
    case 'paused':
      return { bg: 'bg-[#fbbf24]/15', fg: 'text-[#fbbf24]', label: 'PAUSED' };
    case 'completed':
      return { bg: 'bg-[#34d399]/15', fg: 'text-[#34d399]', label: 'COMPLETED' };
    case 'failed':
      return { bg: 'bg-[#f87171]/15', fg: 'text-[#f87171]', label: 'FAILED' };
    case 'budget_exhausted':
      return { bg: 'bg-[#f87171]/15', fg: 'text-[#f87171]', label: 'BUDGET' };
    case 'aborted':
      return { bg: 'bg-[#7a8599]/15', fg: 'text-[#7a8599]', label: 'ABORTED' };
  }
}

export function formatStepStatus(status: StepStatus): string {
  switch (status) {
    case 'pending':
      return '  ';
    case 'running':
      return '▶ ';
    case 'completed':
      return '✅';
    case 'failed':
      return '❌';
    case 'skipped':
      return '⏭ ';
  }
}

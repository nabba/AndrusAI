// Change-request API — kept separate from queries.ts/endpoints.ts so the
// diff stays localized. Mirrors the forge.ts pattern.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  ApproveResponse,
  ChangeListResponse,
  ChangeRequest,
  ChangeStatus,
  RejectResponse,
  RollbackResponse,
} from '../types/changes';

const C = '/api/cp/changes';

export const changesEndpoints = {
  list: (status?: ChangeStatus, limit = 100) =>
    status
      ? `${C}?status=${encodeURIComponent(status)}&limit=${limit}`
      : `${C}?limit=${limit}`,
  detail: (id: string) => `${C}/${encodeURIComponent(id)}`,
  review: (id: string) => `${C}/${encodeURIComponent(id)}/review`,
  typeErrors: (id: string) => `${C}/${encodeURIComponent(id)}/type-errors`,
  checkTypes: (id: string) => `${C}/${encodeURIComponent(id)}/check-types`,
  approve: (id: string) => `${C}/${encodeURIComponent(id)}/approve`,
  reject: (id: string) => `${C}/${encodeURIComponent(id)}/reject`,
  rollback: (id: string) => `${C}/${encodeURIComponent(id)}/rollback`,
  retryApply: (id: string) => `${C}/${encodeURIComponent(id)}/retry-apply`,
};

export const changesKeys = {
  list: (status?: ChangeStatus) =>
    ['changes', 'list', status ?? 'all'] as const,
  detail: (id: string) => ['changes', 'detail', id] as const,
  review: (id: string) => ['changes', 'review', id] as const,
  typeErrors: (id: string) => ['changes', 'type-errors', id] as const,
};

// Phase 3 v2 follow-up (2026-05-22) — payload shape from
// `GET /api/cp/changes/{id}/type-errors`. Each entry inside
// `type_errors` is a dict form of PyrightDiagnostic.
export interface TypeCheckPayload {
  session_id: string;
  path: string;
  submitted_at: string;
  type_errors: Array<{
    file: string;
    line: number;
    column: number;
    severity: string;
    rule: string;
    message: string;
  }>;
}

export function useChangesListQuery(status?: ChangeStatus) {
  return useQuery({
    queryKey: changesKeys.list(status),
    queryFn: () => api<ChangeListResponse>(changesEndpoints.list(status)),
    refetchInterval: 8_000,
  });
}

export function useChangeDetailQuery(id: string | undefined) {
  return useQuery({
    queryKey: changesKeys.detail(id ?? ''),
    queryFn: () => api<ChangeRequest>(changesEndpoints.detail(id as string)),
    enabled: Boolean(id),
    refetchInterval: 5_000,
  });
}

// Phase 4 piece 2c (2026-05-20) — fetch the two-reasoner review for
// a CR. Returns null on 404 (low-stakes zone OR no review recorded)
// rather than throwing, so the UI can silently hide the section
// when no review exists. The ReviewOutcome type comes from
// api/reviews.ts since the shape is identical.
import type { ReviewOutcome } from './reviews';

export function useChangeReviewQuery(id: string | undefined) {
  return useQuery({
    queryKey: changesKeys.review(id ?? ''),
    queryFn: async () => {
      try {
        return await api<ReviewOutcome>(changesEndpoints.review(id as string));
      } catch (e) {
        // 404 is the expected "no review" case — return null so the
        // UI hides the section. Re-raise other errors so we surface
        // unexpected failures.
        const msg = String(e);
        if (msg.includes('404')) {
          return null;
        }
        throw e;
      }
    },
    enabled: Boolean(id),
    refetchInterval: 30_000,
    retry: false, // 404 is final; no exponential backoff
  });
}

// Phase 3 v2 follow-up (2026-05-22) — fetch pyright type-error
// payload for a CR. Returns null on 404 (no coding session opted
// into with_type_check, or CR came from a non-session path) rather
// than throwing, so the UI hides the section gracefully.
export function useChangeTypeErrorsQuery(id: string | undefined) {
  return useQuery({
    queryKey: changesKeys.typeErrors(id ?? ''),
    queryFn: async () => {
      try {
        return await api<TypeCheckPayload>(
          changesEndpoints.typeErrors(id as string),
        );
      } catch (e) {
        const msg = String(e);
        if (msg.includes('404')) {
          return null;
        }
        throw e;
      }
    },
    enabled: Boolean(id),
    refetchInterval: 30_000,
    retry: false,
  });
}

// All four mutations invalidate the list and the detail cache for that id —
// the operator surface should reflect server state immediately even though
// status transitions also fan out via the polling interval.

function invalidateChange(qc: ReturnType<typeof useQueryClient>, id: string) {
  qc.invalidateQueries({ queryKey: ['changes', 'list'] });
  qc.invalidateQueries({ queryKey: changesKeys.detail(id) });
}

export function useApproveChangeMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      operator,
      reason,
    }: {
      id: string;
      operator?: string;
      reason?: string;
    }) =>
      api<ApproveResponse>(changesEndpoints.approve(id), {
        method: 'POST',
        body: JSON.stringify({
          operator: operator ?? 'react-operator',
          reason: reason ?? null,
        }),
      }),
    onSuccess: (_, { id }) => invalidateChange(qc, id),
  });
}

export function useRejectChangeMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      operator,
      reason,
    }: {
      id: string;
      operator?: string;
      reason?: string;
    }) =>
      api<RejectResponse>(changesEndpoints.reject(id), {
        method: 'POST',
        body: JSON.stringify({
          operator: operator ?? 'react-operator',
          reason: reason ?? null,
        }),
      }),
    onSuccess: (_, { id }) => invalidateChange(qc, id),
  });
}

export function useRollbackChangeMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, operator }: { id: string; operator?: string }) =>
      api<RollbackResponse>(changesEndpoints.rollback(id), {
        method: 'POST',
        body: JSON.stringify({ operator: operator ?? 'react-operator' }),
      }),
    onSuccess: (_, { id }) => invalidateChange(qc, id),
  });
}

export function useRetryApplyMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      api<ApproveResponse>(changesEndpoints.retryApply(id), {
        method: 'POST',
      }),
    onSuccess: (_, { id }) => invalidateChange(qc, id),
  });
}

// Phase 3 v2 follow-up (2026-05-22) — operator-triggered fresh pyright
// pass against the CR's proposed new_content. Works for ANY CR, not
// just coding-session-derived ones.
export interface CheckTypesResponse {
  ran: boolean;
  reason?: string;
  path?: string;
  diagnostics?: Array<{
    file: string;
    line: number;
    column: number;
    severity: string;
    rule: string;
    message: string;
  }>;
  error_count?: number;
  warning_count?: number;
  duration_s?: number;
  // Phase 3 v2 follow-up (2026-05-22) — when pyright discovered a
  // pyrightconfig.json / pyproject.toml above the checked path, this
  // carries the project root. Empty string means "pyright ran with
  // defaults" — UI can surface that as a hint.
  config_root?: string;
}

export function useForceTypeCheckMutation() {
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      api<CheckTypesResponse>(changesEndpoints.checkTypes(id), {
        method: 'POST',
      }),
  });
}

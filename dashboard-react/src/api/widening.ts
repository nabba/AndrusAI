// Trust-widening API hooks — Phase 4 piece 1b (2026-05-20).
//
// Operators see proposed widenings of the AUTO_APPLY allowlists at
// /cp/widening, then approve (which applies the widening via the
// standard runtime_settings setters) or reject. Mirrors src/api/delegate.ts
// for shape consistency.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

const W = '/api/cp/widening';

// ── Types: mirror app.risk_classifier.widening + widening_decisions ──

export type WideningListName =
  | 'auto_apply_allowed_requestors'
  | 'auto_apply_allowed_paths';

export type DecisionStatus = 'pending' | 'approved' | 'rejected';

export interface WideningEvidence {
  requestor: string;
  path_prefix: string;
  approvals: number;
  rejections: number;
  rollbacks: number;
  applied: number;
  apply_failed: number;
  first_at: string;
  last_at: string;
  sample_cr_ids: string[];
  rejection_rate: number;
  rollback_rate: number;
  history_days: number;
}

export interface WideningDecision {
  proposal_id: string;
  status: DecisionStatus;
  decided_at: string;
  operator: string;
  reason: string;
}

export interface WideningProposal {
  proposal_id: string;
  proposed_at: string;
  list_name: WideningListName;
  new_entry: string;
  evidence: WideningEvidence;
  rationale: string;
  decision_status: DecisionStatus;
  decision: WideningDecision | null;
}

export interface DecisionBody {
  operator?: string;
  reason?: string;
}

export const wideningEndpoints = {
  pending: (limit = 50) => `${W}?limit=${limit}`,
  all: (limit = 100) => `${W}/all?limit=${limit}`,
  detail: (id: string) => `${W}/${encodeURIComponent(id)}`,
  approve: (id: string) => `${W}/${encodeURIComponent(id)}/approve`,
  reject: (id: string) => `${W}/${encodeURIComponent(id)}/reject`,
};

export const wideningKeys = {
  pending: () => ['widening', 'pending'] as const,
  all: () => ['widening', 'all'] as const,
  detail: (id: string) => ['widening', 'detail', id] as const,
};

// ── Read hooks ───────────────────────────────────────────────────────

export function useWideningPendingQuery() {
  return useQuery({
    queryKey: wideningKeys.pending(),
    queryFn: () =>
      api<{ count: number; proposals: WideningProposal[] }>(
        wideningEndpoints.pending(),
      ),
    refetchInterval: 10_000,
  });
}

export function useWideningAllQuery() {
  return useQuery({
    queryKey: wideningKeys.all(),
    queryFn: () =>
      api<{ count: number; proposals: WideningProposal[] }>(
        wideningEndpoints.all(),
      ),
    refetchInterval: 10_000,
  });
}

// ── Write hooks ──────────────────────────────────────────────────────

export function useApproveWideningMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      proposalId,
      body,
    }: {
      proposalId: string;
      body?: DecisionBody;
    }) =>
      api<WideningProposal>(wideningEndpoints.approve(proposalId), {
        method: 'POST',
        body: JSON.stringify(body || {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['widening'] });
    },
  });
}

export function useRejectWideningMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      proposalId,
      body,
    }: {
      proposalId: string;
      body?: DecisionBody;
    }) =>
      api<WideningProposal>(wideningEndpoints.reject(proposalId), {
        method: 'POST',
        body: JSON.stringify(body || {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['widening'] });
    },
  });
}

// ── Helpers ──────────────────────────────────────────────────────────

export function formatDecisionStatus(status: DecisionStatus): {
  bg: string;
  fg: string;
  label: string;
} {
  switch (status) {
    case 'pending':
      return {
        bg: 'bg-[#fbbf24]/15',
        fg: 'text-[#fbbf24]',
        label: 'PENDING',
      };
    case 'approved':
      return {
        bg: 'bg-[#34d399]/15',
        fg: 'text-[#34d399]',
        label: 'APPROVED',
      };
    case 'rejected':
      return {
        bg: 'bg-[#7a8599]/15',
        fg: 'text-[#7a8599]',
        label: 'REJECTED',
      };
  }
}

export function formatListName(list: WideningListName): string {
  switch (list) {
    case 'auto_apply_allowed_requestors':
      return 'requestor allowlist';
    case 'auto_apply_allowed_paths':
      return 'path allowlist';
  }
}

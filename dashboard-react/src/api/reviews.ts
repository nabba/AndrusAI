// Two-reasoner reviews API hooks — Phase 4 piece 2b (2026-05-20).
//
// Operator visibility into the audit trail of independent LLM safety
// verdicts. Read-only — every review is produced by upstream callers
// invoking review_text(). The REST surface exposes the JSONL audit
// so operators don't need shell access to inspect.

import { useQuery } from '@tanstack/react-query';
import { api } from './client';

const R = '/api/cp/reviews';

// ── Types: mirror app.risk_classifier.two_reasoner ─────────────────

export type Verdict =
  | 'safe'
  | 'unsafe'
  | 'uncertain'
  | 'disagree'
  | 'disabled';

export interface ReasonerVerdict {
  reasoner_id: string;
  verdict: Verdict;
  confidence: number;
  reasoning: string;
  error: string;
}

export interface ReviewOutcome {
  review_id: string;
  reviewed_at: string;
  verdict: Verdict;
  confidence: number;
  per_reasoner: ReasonerVerdict[];
  diagnostic: string;
  zone: string;
}

export interface ReviewsListResponse {
  count: number;
  total_scanned: number;
  filter_verdict: Verdict | null;
  reviews: ReviewOutcome[];
}

export interface ReviewsSummary {
  total: number;
  by_verdict: Record<Verdict, number>;
}

export const reviewsEndpoints = {
  list: (verdict: Verdict | null = null, limit = 100) => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (verdict) params.set('verdict', verdict);
    return `${R}?${params.toString()}`;
  },
  detail: (reviewId: string) => `${R}/${encodeURIComponent(reviewId)}`,
  summary: () => `${R}/stats/summary`,
};

export const reviewsKeys = {
  list: (verdict: Verdict | null) =>
    ['reviews', 'list', verdict ?? 'all'] as const,
  detail: (id: string) => ['reviews', 'detail', id] as const,
  summary: () => ['reviews', 'summary'] as const,
};

// ── Read hooks ───────────────────────────────────────────────────────

export function useReviewsListQuery(verdict: Verdict | null = null) {
  return useQuery({
    queryKey: reviewsKeys.list(verdict),
    queryFn: () => api<ReviewsListResponse>(reviewsEndpoints.list(verdict)),
    refetchInterval: 10_000,
  });
}

export function useReviewQuery(reviewId: string | undefined) {
  return useQuery({
    queryKey: reviewsKeys.detail(reviewId ?? ''),
    queryFn: () => api<ReviewOutcome>(reviewsEndpoints.detail(reviewId as string)),
    enabled: !!reviewId,
  });
}

export function useReviewsSummaryQuery() {
  return useQuery({
    queryKey: reviewsKeys.summary(),
    queryFn: () => api<ReviewsSummary>(reviewsEndpoints.summary()),
    refetchInterval: 30_000,
  });
}

// ── Helpers ──────────────────────────────────────────────────────────

export function formatVerdict(verdict: Verdict): {
  bg: string;
  fg: string;
  label: string;
  icon: string;
} {
  switch (verdict) {
    case 'safe':
      return {
        bg: 'bg-[#34d399]/15',
        fg: 'text-[#34d399]',
        label: 'SAFE',
        icon: '✓',
      };
    case 'unsafe':
      return {
        bg: 'bg-[#f87171]/15',
        fg: 'text-[#f87171]',
        label: 'UNSAFE',
        icon: '⚠',
      };
    case 'uncertain':
      return {
        bg: 'bg-[#fbbf24]/15',
        fg: 'text-[#fbbf24]',
        label: 'UNCERTAIN',
        icon: '?',
      };
    case 'disagree':
      return {
        bg: 'bg-[#a78bfa]/15',
        fg: 'text-[#a78bfa]',
        label: 'DISAGREE',
        icon: '⚖',
      };
    case 'disabled':
      return {
        bg: 'bg-[#7a8599]/15',
        fg: 'text-[#7a8599]',
        label: 'DISABLED',
        icon: '−',
      };
  }
}

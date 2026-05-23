// Two-reasoner review log — /cp/reviews.
//
// Phase 4 piece 2b (2026-05-20). Operator visibility into the audit
// trail of independent LLM safety verdicts produced by review_text().
// Three sections:
//
//   1. Summary chips: at-a-glance counts by verdict (safe / unsafe /
//      uncertain / disagree / disabled).
//   2. Verdict filter chips: click to narrow the list.
//   3. Review list: per-review row with verdict + confidence +
//      reviewed_at; expandable drawer shows per-reasoner verdicts +
//      reasoning + diagnostic.
//
// When the master switch (two_reasoner_review_enabled) is OFF, the
// page still works for reviewing past reviews — new entries just
// won't appear.

import { useState } from 'react';
import {
  formatVerdict,
  useReviewsListQuery,
  useReviewsSummaryQuery,
  type ReviewOutcome,
  type Verdict,
} from '../api/reviews';
import { useRuntimeSettingsQuery } from '../api/queries';
import { Skeleton } from './ui/Skeleton';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT_BG = '#111820';
const ACCENT_BORDER = '#1e2738';

const VERDICT_ORDER: Verdict[] = [
  'safe',
  'unsafe',
  'uncertain',
  'disagree',
  'disabled',
];

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const delta = (Date.now() - d.getTime()) / 1000;
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  if (delta < 604800) return `${Math.floor(delta / 86400)}d ago`;
  return d.toLocaleDateString();
}

function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const v = formatVerdict(verdict);
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${v.bg} ${v.fg}`}
    >
      <span>{v.icon}</span>
      <span>{v.label}</span>
    </span>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  // High confidence = green, mid = yellow, low = red
  const color =
    pct >= 70 ? '#34d399' : pct >= 40 ? '#fbbf24' : '#f87171';
  return (
    <div
      className="h-1 w-12 rounded overflow-hidden"
      style={{ background: '#0a1018' }}
    >
      <div
        className="h-full"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

// ── Per-review row with expandable drawer ───────────────────────────

function ReviewRow({
  review,
  expanded,
  onToggle,
}: {
  review: ReviewOutcome;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className="rounded-lg border"
      style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
    >
      <button
        onClick={onToggle}
        className="w-full px-3 py-2 text-left flex items-center gap-3 hover:bg-[#1a2230]/40 transition-colors"
      >
        <VerdictBadge verdict={review.verdict} />
        <code
          className="text-[10px] font-mono"
          style={{ color: TEXT_DIM, minWidth: '5rem' }}
        >
          {review.review_id.slice(0, 8)}
        </code>
        {review.zone && (
          <code
            className="text-[10px] px-1.5 py-0.5 rounded"
            style={{
              background: '#1e2738',
              color: TEXT_BRIGHT,
              minWidth: '5rem',
              textAlign: 'center',
            }}
          >
            {review.zone}
          </code>
        )}
        <span
          className="text-xs flex-1 truncate"
          style={{ color: TEXT_DIM }}
        >
          {review.diagnostic || '(no diagnostic)'}
        </span>
        <ConfidenceBar value={review.confidence} />
        <span
          className="text-[10px] font-mono"
          style={{ color: TEXT_DIM, minWidth: '3rem', textAlign: 'right' }}
        >
          {(review.confidence * 100).toFixed(0)}%
        </span>
        <span
          className="text-[10px]"
          style={{ color: TEXT_DIM, minWidth: '4.5rem', textAlign: 'right' }}
        >
          {formatRelative(review.reviewed_at)}
        </span>
        <span className="text-[10px]" style={{ color: TEXT_DIM }}>
          {expanded ? '▼' : '▶'}
        </span>
      </button>

      {expanded && (
        <div
          className="px-3 py-3 border-t space-y-3 text-xs"
          style={{ borderColor: ACCENT_BORDER, color: TEXT_DIM }}
        >
          {/* Outcome metadata */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Review ID
              </div>
              <code className="text-xs font-mono" style={{ color: TEXT_BRIGHT }}>
                {review.review_id}
              </code>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Reviewed at
              </div>
              <div style={{ color: TEXT_BRIGHT }}>
                {review.reviewed_at.slice(0, 19)}
              </div>
            </div>
          </div>

          {/* Diagnostic */}
          <div>
            <div className="text-[10px] uppercase tracking-wider mb-1">
              Aggregated diagnostic
            </div>
            <div style={{ color: TEXT_BRIGHT }}>
              {review.diagnostic || '(none)'}
            </div>
          </div>

          {/* Per-reasoner breakdown */}
          {review.per_reasoner.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-2">
                Per-reasoner verdicts ({review.per_reasoner.length})
              </div>
              <div className="space-y-2">
                {review.per_reasoner.map((r, idx) => (
                  <div
                    key={`${r.reasoner_id}-${idx}`}
                    className="px-3 py-2 rounded border"
                    style={{
                      background: '#0a1018',
                      borderColor: ACCENT_BORDER,
                    }}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <VerdictBadge verdict={r.verdict} />
                      <code
                        className="text-[10px] font-mono"
                        style={{ color: TEXT_DIM }}
                      >
                        {r.reasoner_id}
                      </code>
                      <ConfidenceBar value={r.confidence} />
                      <span
                        className="text-[10px] font-mono"
                        style={{ color: TEXT_DIM }}
                      >
                        {(r.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    {r.reasoning && (
                      <p
                        className="text-[11px] mt-1"
                        style={{ color: TEXT_BRIGHT }}
                      >
                        {r.reasoning}
                      </p>
                    )}
                    {r.error && (
                      <p className="text-[10px] mt-1 text-[#f87171]">
                        ⚠ error: {r.error}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Summary chips ─────────────────────────────────────────────────

function SummaryRow() {
  const q = useReviewsSummaryQuery();
  if (q.isLoading) return <Skeleton className="h-8 w-full" />;
  if (q.error || !q.data) return null;

  const total = q.data.total;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span style={{ color: TEXT_DIM }}>{total} total reviews</span>
      <span style={{ color: TEXT_DIM }}>·</span>
      {VERDICT_ORDER.map((v) => {
        const count = q.data.by_verdict[v] || 0;
        if (count === 0) return null;
        const f = formatVerdict(v);
        return (
          <span
            key={v}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${f.bg} ${f.fg}`}
          >
            {f.icon} {f.label} {count}
          </span>
        );
      })}
    </div>
  );
}

// ── Master-switch warning banner ─────────────────────────────────

function MasterSwitchBanner() {
  const settingsQ = useRuntimeSettingsQuery();
  const enabled =
    settingsQ.data?.two_reasoner_review_enabled === true;
  if (settingsQ.isLoading || enabled) return null;
  return (
    <div
      className="rounded-lg p-3 border text-xs"
      style={{
        background: '#7f1d1d22',
        borderColor: '#f87171',
        color: '#f87171',
      }}
    >
      ⚠ <strong>Two-reasoner review is OFF.</strong> New reviews aren't
      being produced — callers receive <code>verdict=disabled</code>.
      Past reviews (if any) remain inspectable below.
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────

export function ReviewsPage() {
  const [filter, setFilter] = useState<Verdict | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const listQ = useReviewsListQuery(filter);

  const toggle = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const reviews = listQ.data?.reviews ?? [];

  return (
    <div className="space-y-4 max-w-5xl">
      <div>
        <h1
          className="text-xl font-semibold"
          style={{ color: TEXT_BRIGHT }}
        >
          Two-reasoner review log
        </h1>
        <p className="text-xs mt-1" style={{ color: TEXT_DIM }}>
          Audit trail of independent LLM safety verdicts on proposed
          actions. Two reasoners review each proposal; their
          aggregated verdict is one of safe / unsafe / uncertain /
          disagree. Click a row to see each reasoner's reasoning.
        </p>
      </div>

      <MasterSwitchBanner />
      <SummaryRow />

      {/* Filter chips */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setFilter(null)}
          className="px-3 py-1 text-xs rounded-full border"
          style={{
            background: filter === null ? '#1e3a52' : ACCENT_BG,
            color: filter === null ? TEXT_BRIGHT : TEXT_DIM,
            borderColor: ACCENT_BORDER,
          }}
        >
          All
        </button>
        {VERDICT_ORDER.map((v) => {
          const f = formatVerdict(v);
          const active = filter === v;
          return (
            <button
              key={v}
              onClick={() => setFilter(v)}
              className="inline-flex items-center gap-1 px-3 py-1 text-xs rounded-full border"
              style={{
                background: active ? '#1e3a52' : ACCENT_BG,
                color: active ? TEXT_BRIGHT : TEXT_DIM,
                borderColor: ACCENT_BORDER,
              }}
            >
              <span>{f.icon}</span>
              <span>{f.label.toLowerCase()}</span>
            </button>
          );
        })}
      </div>

      {/* Review list */}
      <section>
        {listQ.isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : listQ.error ? (
          <div className="text-xs text-[#f87171]">
            Failed to load reviews: {String(listQ.error)}
          </div>
        ) : reviews.length === 0 ? (
          <div
            className="text-xs italic px-3 py-2 rounded border"
            style={{
              color: TEXT_DIM,
              background: ACCENT_BG,
              borderColor: ACCENT_BORDER,
            }}
          >
            {filter
              ? `(no reviews with verdict=${filter})`
              : '(no reviews yet — callers invoke two_reasoner.review_text to produce entries)'}
          </div>
        ) : (
          <div className="space-y-2">
            {reviews.map((r) => (
              <ReviewRow
                key={r.review_id}
                review={r}
                expanded={!!expanded[r.review_id]}
                onToggle={() => toggle(r.review_id)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

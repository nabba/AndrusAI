// Phase 5.3b — operator React surface for the change-request system.
// Lists agent-proposed code changes with status filter, opens a drawer
// for the diff and per-status action buttons (approve / reject /
// rollback / retry-apply).
//
// Backend lives at /api/cp/changes — see app/control_plane/changes_api.py
// and docs/CHANGE_REQUESTS.md.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Skeleton } from './ui/Skeleton';
import {
  useApproveChangeMutation,
  useChangeDetailQuery,
  useChangeReviewQuery,
  useChangeTypeErrorsQuery,
  useChangesListQuery,
  useForceTypeCheckMutation,
  useRejectChangeMutation,
  useRetryApplyMutation,
  useRollbackChangeMutation,
} from '../api/changes';
import type { ChangeRequest, ChangeStatus } from '../types/changes';
import { formatVerdict, type ReviewOutcome } from '../api/reviews';

const STATUS_FILTERS: (ChangeStatus | 'all')[] = [
  'all',
  'pending',
  'approved',
  'applied',
  'apply_failed',
  'rejected',
  'rolled_back',
  'tier_immutable_refused',
  'timeout',
];

const STATUS_BADGE: Record<
  ChangeStatus,
  { bg: string; fg: string; border: string; label: string }
> = {
  pending: {
    bg: 'bg-[#fbbf24]/15',
    fg: 'text-[#fbbf24]',
    border: 'border-[#fbbf24]/30',
    label: 'PENDING',
  },
  approved: {
    bg: 'bg-[#60a5fa]/15',
    fg: 'text-[#60a5fa]',
    border: 'border-[#60a5fa]/30',
    label: 'APPROVED',
  },
  applied: {
    bg: 'bg-[#34d399]/15',
    fg: 'text-[#34d399]',
    border: 'border-[#34d399]/30',
    label: 'APPLIED',
  },
  apply_failed: {
    bg: 'bg-[#f87171]/15',
    fg: 'text-[#f87171]',
    border: 'border-[#f87171]/30',
    label: 'APPLY FAILED',
  },
  rejected: {
    bg: 'bg-[#7a8599]/15',
    fg: 'text-[#7a8599]',
    border: 'border-[#7a8599]/30',
    label: 'REJECTED',
  },
  rolled_back: {
    bg: 'bg-[#a78bfa]/15',
    fg: 'text-[#a78bfa]',
    border: 'border-[#a78bfa]/30',
    label: 'ROLLED BACK',
  },
  tier_immutable_refused: {
    bg: 'bg-[#f87171]/15',
    fg: 'text-[#f87171]',
    border: 'border-[#f87171]/40',
    label: 'TIER_IMMUTABLE',
  },
  timeout: {
    bg: 'bg-[#7a8599]/15',
    fg: 'text-[#7a8599]',
    border: 'border-[#7a8599]/30',
    label: 'TIMEOUT',
  },
};

function StatusBadge({ status }: { status: ChangeStatus }) {
  const s = STATUS_BADGE[status];
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border ${s.bg} ${s.fg} ${s.border}`}
    >
      {s.label}
    </span>
  );
}

function ProtectedBadge({ isProtected }: { isProtected: boolean }) {
  if (!isProtected) return null;
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-[#f87171]/15 text-[#f87171] border border-[#f87171]/40"
      title="Path is in TIER_IMMUTABLE — agent path can never modify it, even with operator approval."
    >
      🛑 PROTECTED
    </span>
  );
}

// Phase 3 v2 follow-up (2026-05-22) — type-error count badge on
// CR list rows. Shown only when count > 0 so the list stays calm
// when most CRs are type-clean. Click target is the row itself,
// not the badge — drawer surfaces the per-error detail.
function TypeErrorsBadge({ count }: { count?: number }) {
  if (!count || count <= 0) return null;
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-[#f87171]/15 text-[#f87171] border border-[#f87171]/40"
      title={`Pyright recorded ${count} type error${count === 1 ? '' : 's'} for this CR at submit time. Open the drawer to see them. CR is NOT blocked on these — informational.`}
    >
      ❌ {count} TYPE{count === 1 ? '' : 'S'}
    </span>
  );
}

// Render a unified diff with line-level coloring. Lines starting with
// `+` are green; `-` are red; `@@` are blue (hunk headers); everything
// else is muted. Long diffs are truncated to 800 lines with a footer.
function DiffView({ diff }: { diff: string }) {
  if (!diff) {
    return (
      <div className="text-xs text-[#7a8599] italic">
        (no diff — likely a new-file creation with empty old_content)
      </div>
    );
  }
  const allLines = diff.split('\n');
  const MAX = 800;
  const truncated = allLines.length > MAX;
  const lines = truncated ? allLines.slice(0, MAX) : allLines;

  return (
    <div className="rounded-md border border-[#1e2738] bg-[#0a0e14] overflow-x-auto font-mono text-[11px] leading-snug">
      <pre className="px-3 py-2 whitespace-pre">
        {lines.map((line, i) => {
          let cls = 'text-[#cbd5e1]';
          if (line.startsWith('+++') || line.startsWith('---'))
            cls = 'text-[#7a8599]';
          else if (line.startsWith('@@')) cls = 'text-[#60a5fa]';
          else if (line.startsWith('+')) cls = 'text-[#34d399]';
          else if (line.startsWith('-')) cls = 'text-[#f87171]';
          else cls = 'text-[#94a3b8]';
          return (
            <div key={i} className={cls}>
              {line || ' '}
            </div>
          );
        })}
        {truncated && (
          <div className="text-[#fbbf24] mt-2">
            … diff truncated at {MAX} lines (full diff is {allLines.length}{' '}
            lines)
          </div>
        )}
      </pre>
    </div>
  );
}

function ChangeRow({
  change,
  onClick,
  isActive,
}: {
  change: ChangeRequest;
  onClick: () => void;
  isActive: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-4 rounded-lg border transition-colors ${
        isActive
          ? 'border-[#60a5fa]/40 bg-[#60a5fa]/5'
          : 'border-[#1e2738] bg-[#111820] hover:border-[#60a5fa]/30 hover:bg-[#1e2738]/50'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <code className="text-sm font-semibold text-[#e2e8f0] truncate">
              {change.path}
            </code>
            <StatusBadge status={change.status} />
            <ProtectedBadge isProtected={change.is_protected} />
            <TypeErrorsBadge count={change.type_error_count} />
            <span className="text-[10px] font-mono text-[#7a8599]">
              by {change.requestor}
            </span>
          </div>
          {change.reason && (
            <div className="text-xs text-[#cbd5e1] mt-1 line-clamp-2">
              {change.reason}
            </div>
          )}
          {change.apply_error && (
            <div className="text-xs text-[#f87171] mt-1 truncate">
              error: {change.apply_error}
            </div>
          )}
        </div>
        <div className="text-[10px] text-[#7a8599] font-mono flex-shrink-0 text-right">
          <div>{new Date(change.created_at).toLocaleString()}</div>
          <div className="opacity-60 mt-0.5">{change.id.slice(0, 8)}…</div>
        </div>
      </div>
    </button>
  );
}

function ActionButtons({
  change,
  onClose,
}: {
  change: ChangeRequest;
  onClose: () => void;
}) {
  const [confirmRollback, setConfirmRollback] = useState(false);
  const approve = useApproveChangeMutation();
  const reject = useRejectChangeMutation();
  const rollback = useRollbackChangeMutation();
  const retry = useRetryApplyMutation();

  // TIER_IMMUTABLE_REFUSED has no action — the rule is absolute.
  if (change.status === 'tier_immutable_refused') {
    return (
      <div className="text-xs text-[#f87171] bg-[#f87171]/10 border border-[#f87171]/30 rounded-md p-3">
        <strong className="block mb-1">🛑 TIER_IMMUTABLE</strong>
        This path is in the absolute-no-modify list. No human-override
        path can authorize an agent write. Operator must edit directly
        via PR (gateway redeploy required).
      </div>
    );
  }

  const pending = (
    approve.isPending ||
    reject.isPending ||
    rollback.isPending ||
    retry.isPending
  );

  return (
    <div className="space-y-3">
      {change.status === 'pending' && (
        <div className="flex flex-wrap gap-2">
          <button
            disabled={pending}
            onClick={() => approve.mutate({ id: change.id })}
            className="px-4 py-2 rounded-md text-sm font-medium bg-[#34d399]/15 text-[#34d399] hover:bg-[#34d399]/25 border border-[#34d399]/30 disabled:opacity-50 transition-colors"
          >
            ✓ Approve + apply
          </button>
          <button
            disabled={pending}
            onClick={() =>
              reject.mutate({ id: change.id, reason: 'rejected via React' })
            }
            className="px-4 py-2 rounded-md text-sm font-medium bg-[#f87171]/15 text-[#f87171] hover:bg-[#f87171]/25 border border-[#f87171]/30 disabled:opacity-50 transition-colors"
          >
            ✗ Reject
          </button>
        </div>
      )}

      {change.status === 'apply_failed' && (
        <div className="space-y-2">
          <div className="text-xs text-[#f87171]">
            Apply failed:{' '}
            <code className="font-mono">{change.apply_error ?? 'unknown error'}</code>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              disabled={pending}
              onClick={() => retry.mutate({ id: change.id })}
              className="px-4 py-2 rounded-md text-sm font-medium bg-[#fbbf24]/15 text-[#fbbf24] hover:bg-[#fbbf24]/25 border border-[#fbbf24]/30 disabled:opacity-50 transition-colors"
            >
              ↻ Retry apply
            </button>
          </div>
        </div>
      )}

      {change.is_rollbackable && (
        <div className="space-y-2">
          {!confirmRollback ? (
            <button
              disabled={pending}
              onClick={() => setConfirmRollback(true)}
              className="px-4 py-2 rounded-md text-sm font-medium bg-[#a78bfa]/15 text-[#a78bfa] hover:bg-[#a78bfa]/25 border border-[#a78bfa]/30 disabled:opacity-50 transition-colors"
            >
              ⤺ Roll back…
            </button>
          ) : (
            <div className="flex flex-wrap items-center gap-2 p-2 border border-[#a78bfa]/30 rounded-md bg-[#a78bfa]/5">
              <span className="text-xs text-[#cbd5e1]">
                Revert {change.git_commit_sha?.slice(0, 8) ?? '?'} on{' '}
                <code className="font-mono">{change.path}</code>?
              </span>
              <button
                disabled={pending}
                onClick={() => {
                  rollback.mutate(
                    { id: change.id },
                    { onSettled: () => setConfirmRollback(false) },
                  );
                }}
                className="px-3 py-1 rounded text-xs font-medium bg-[#a78bfa]/25 text-[#a78bfa] hover:bg-[#a78bfa]/40 border border-[#a78bfa]/40 disabled:opacity-50"
              >
                Confirm rollback
              </button>
              <button
                disabled={pending}
                onClick={() => setConfirmRollback(false)}
                className="px-3 py-1 rounded text-xs text-[#7a8599] hover:text-[#cbd5e1]"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {(approve.error || reject.error || rollback.error || retry.error) && (
        <div className="text-xs text-[#f87171] bg-[#f87171]/10 border border-[#f87171]/30 rounded-md p-2">
          {(approve.error || reject.error || rollback.error || retry.error)?.message}
        </div>
      )}

      {change.is_terminal && (
        <button
          onClick={onClose}
          className="px-3 py-1 rounded text-xs text-[#7a8599] hover:text-[#cbd5e1]"
        >
          Close
        </button>
      )}
    </div>
  );
}

// On-demand pyright runner inside the drawer (Phase 3 v2 follow-up,
// 2026-05-22). Available for ANY .py CR regardless of origin — unlike
// the read-only `ChangeTypeErrorsSection` which only surfaces data
// from a coding-session submit, this section runs pyright FRESH
// against the CR's proposed new_content when the operator clicks.
function OnDemandTypeCheck({ change }: { change: ChangeRequest }) {
  const mut = useForceTypeCheckMutation();
  if (!change.path.endsWith('.py')) return null;
  const result = mut.data;
  const diagnostics = result?.diagnostics ?? [];
  const errors = diagnostics.filter((d) => d.severity === 'error');

  return (
    <section className="rounded-md border border-[#1e2738] bg-[#111820] p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider">
          Type check (on-demand)
        </h3>
        <button
          disabled={mut.isPending}
          onClick={() => mut.mutate({ id: change.id })}
          className="px-2 py-1 rounded text-[11px] font-medium border disabled:opacity-50"
          style={{
            background: '#1e2738',
            color: '#7a8599',
            borderColor: '#2a3a52',
          }}
          title="Runs pyright against this CR's proposed new_content. Works for any CR — including those that came from request_restricted_write (not a coding session)."
        >
          {mut.isPending ? '⟳ Running…' : '⟳ Check types now'}
        </button>
      </div>

      {!result && !mut.isPending && (
        <p className="text-[11px] italic text-[#7a8599]">
          Click to run pyright against this CR's proposed content.
          The diff is NOT applied — the check runs against the
          new_content as if it were on disk.
        </p>
      )}

      {result && !result.ran && (
        <div
          className="text-xs rounded p-2 border"
          style={{
            background: '#7f1d1d22',
            color: '#f87171',
            borderColor: '#f87171' + '55',
          }}
        >
          Check did not run: {result.reason ?? '(no reason)'}
        </div>
      )}

      {result && result.ran && (
        <div className="space-y-1">
          <div className="text-xs text-[#cbd5e1]">
            {errors.length === 0 ? (
              <>
                <strong className="text-[#34d399]">✓ Type-clean.</strong>{' '}
                {result.warning_count
                  ? `(${result.warning_count} warning${result.warning_count === 1 ? '' : 's'})`
                  : 'No errors or warnings.'}
              </>
            ) : (
              <>
                <strong className="text-[#f87171]">
                  ❌ {errors.length} error{errors.length === 1 ? '' : 's'}
                </strong>
                {result.warning_count
                  ? ` · ${result.warning_count} warning${result.warning_count === 1 ? '' : 's'}`
                  : ''}
                {result.duration_s !== undefined && (
                  <span className="text-[10px] text-[#7a8599] ml-2">
                    ({result.duration_s.toFixed(2)}s)
                  </span>
                )}
              </>
            )}
          </div>

          {/* Config-root debugging hint */}
          <div className="text-[10px] text-[#7a8599] font-mono">
            {result.config_root ? (
              <>
                via config at <code>{result.config_root}</code>
              </>
            ) : (
              <em>
                ran with pyright defaults (add{' '}
                <code>pyrightconfig.json</code> at the project root for
                project-specific rules)
              </em>
            )}
          </div>
          {errors.map((e, i) => (
            <div
              key={`${e.file}-${e.line}-${i}`}
              className="rounded border border-[#1e2738] bg-[#0a0e14] p-2 text-[11px]"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[#f87171]">❌</span>
                <code className="font-mono text-[#cbd5e1]">
                  {e.file}:{e.line}:{e.column}
                </code>
                {e.rule && (
                  <span className="text-[10px] font-mono text-[#fbbf24]">
                    [{e.rule}]
                  </span>
                )}
              </div>
              <div className="text-[#cbd5e1] whitespace-pre-wrap pl-5">
                {e.message}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}


// Pyright type-error surface inside the drawer (Phase 3 v2
// follow-up, 2026-05-22). Hidden when no type-check data is
// recorded for the CR (low-stakes session, opt-out, or non-session
// origin). When ≥1 error is recorded, the section lights up with
// per-error rows including the rule + message + file:line:col.
function ChangeTypeErrorsSection({ changeId }: { changeId: string }) {
  const q = useChangeTypeErrorsQuery(changeId);
  if (q.isLoading || q.data === undefined) return null;
  if (q.data === null) return null;
  const payload = q.data;
  const errors = payload.type_errors ?? [];

  // Render the "via session <id>" suffix as a Link so operators
  // can drill from a CR's type-error context back to the originating
  // coding session in one click. The deep-link is handled by
  // CodingSessionsPage's useSearchParams hook.
  const sessionLink = (
    <Link
      to={`/coding-sessions?session=${encodeURIComponent(payload.session_id)}`}
      className="text-[#60a5fa] hover:underline font-mono"
      title="Open the originating coding session"
    >
      session {payload.session_id.slice(0, 8)}…
    </Link>
  );

  // Clean type-check is informational only — show a compact "no
  // errors" badge so the operator knows the check ran but stays
  // out of the way.
  if (errors.length === 0) {
    return (
      <section className="rounded-md border border-[#1e2738] bg-[#111820] p-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider">
            Type check
          </h3>
          <span className="text-[10px] font-mono text-[#34d399]">
            ✓ clean
          </span>
        </div>
        <div className="text-[10px] font-mono text-[#7a8599] mt-1">
          via {sessionLink}
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-md border border-[#f87171]/40 bg-[#f87171]/5 p-4">
      <div className="flex items-center justify-between gap-2 mb-3">
        <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider">
          Type check
        </h3>
        <span className="text-[10px] font-mono text-[#7a8599]">
          via {sessionLink}
        </span>
      </div>
      <div className="text-xs text-[#cbd5e1] mb-3">
        <strong className="text-[#f87171]">
          {errors.length} type error{errors.length === 1 ? '' : 's'}
        </strong>{' '}
        recorded by pyright at submit time. The CR is{' '}
        <em>not blocked</em> on these — they're operator decision
        support. Approving applies the diff as-is.
      </div>
      <div className="space-y-1">
        {errors.map((e, i) => (
          <div
            key={`${e.file}-${e.line}-${i}`}
            className="rounded border border-[#1e2738] bg-[#0a0e14] p-2 text-[11px]"
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[#f87171]">❌</span>
              <code className="font-mono text-[#cbd5e1]">
                {e.file}:{e.line}:{e.column}
              </code>
              {e.rule && (
                <span className="text-[10px] font-mono text-[#fbbf24]">
                  [{e.rule}]
                </span>
              )}
            </div>
            <div className="text-[#cbd5e1] whitespace-pre-wrap pl-5">
              {e.message}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}


// Two-reasoner review surface inside the drawer (Phase 4 piece 2c).
// Hidden when no review exists (low-stakes zone OR review not yet
// recorded). DISAGREE is rendered with extra emphasis since it means
// the two reasoners did not converge and operator judgement matters
// more than usual.
function ChangeReviewSection({ changeId }: { changeId: string }) {
  const q = useChangeReviewQuery(changeId);
  const review: ReviewOutcome | null | undefined = q.data;

  if (q.isLoading || review === undefined) return null;
  if (review === null) return null;

  const v = formatVerdict(review.verdict);
  const confidencePct = Math.round(review.confidence * 100);
  const isDisagree = review.verdict === 'disagree';
  const isUnsafe = review.verdict === 'unsafe';
  const emphasize = isDisagree || isUnsafe;

  return (
    <section
      className={`rounded-md border p-4 ${
        emphasize
          ? 'border-[#a78bfa]/40 bg-[#a78bfa]/5'
          : 'border-[#1e2738] bg-[#111820]'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-3">
        <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider">
          Two-reasoner review
        </h3>
        <span className="text-[10px] font-mono text-[#7a8599]">
          {new Date(review.reviewed_at).toLocaleString()} · zone{' '}
          <code>{review.zone}</code>
        </span>
      </div>

      <div className="flex items-center gap-3 flex-wrap mb-3">
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${v.bg} ${v.fg} border-current/30`}
        >
          <span>{v.icon}</span>
          <span>{v.label}</span>
        </span>
        <div className="flex items-center gap-2 text-xs text-[#cbd5e1]">
          <span className="text-[#7a8599]">confidence</span>
          <div className="relative w-32 h-1.5 rounded-full bg-[#1e2738] overflow-hidden">
            <div
              className={`absolute inset-y-0 left-0 ${v.bg.replace(
                '/15',
                '/60',
              )}`}
              style={{ width: `${confidencePct}%` }}
            />
          </div>
          <span className="font-mono">{confidencePct}%</span>
        </div>
      </div>

      {emphasize && (
        <div className="text-xs text-[#cbd5e1] mb-3">
          {isDisagree ? (
            <>
              <strong className="text-[#a78bfa]">Reasoners disagreed.</strong>{' '}
              No consensus on safety — read both verdicts below before deciding.
            </>
          ) : (
            <>
              <strong className="text-[#f87171]">Marked unsafe.</strong>{' '}
              At least one reasoner flagged this change.
            </>
          )}
        </div>
      )}

      {review.diagnostic && (
        <div className="text-xs text-[#cbd5e1] italic mb-3">
          {review.diagnostic}
        </div>
      )}

      <div className="space-y-2">
        {review.per_reasoner.map((r) => {
          const rv = formatVerdict(r.verdict);
          return (
            <div
              key={r.reasoner_id}
              className="rounded border border-[#1e2738] bg-[#0a0e14] p-2"
            >
              <div className="flex items-center gap-2 mb-1">
                <code className="text-[10px] font-mono text-[#7a8599]">
                  {r.reasoner_id}
                </code>
                <span
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${rv.bg} ${rv.fg}`}
                >
                  {rv.icon} {rv.label}
                </span>
                <span className="text-[10px] font-mono text-[#7a8599]">
                  {Math.round(r.confidence * 100)}%
                </span>
              </div>
              {r.error ? (
                <div className="text-[11px] text-[#f87171] font-mono">
                  error: {r.error}
                </div>
              ) : (
                <div className="text-[11px] text-[#cbd5e1] whitespace-pre-wrap">
                  {r.reasoning || '(no reasoning supplied)'}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ChangeDetailDrawer({
  changeId,
  onClose,
}: {
  changeId: string | null;
  onClose: () => void;
}) {
  // Hooks must run on every render — early return goes after.
  const q = useChangeDetailQuery(changeId ?? undefined);
  const change = q.data;

  if (!changeId) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl bg-[#0a0e14] border-l border-[#1e2738] flex flex-col h-full"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-5 py-4 border-b border-[#1e2738] flex-shrink-0">
          <div className="min-w-0">
            <div className="text-xs text-[#7a8599] font-mono truncate">
              {changeId}
            </div>
            <div className="text-sm font-semibold text-[#e2e8f0] truncate">
              {change?.path ?? '…'}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded text-[#7a8599] hover:text-[#e2e8f0] hover:bg-[#1e2738]"
            aria-label="Close drawer"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {q.isLoading && !change ? (
            <div className="space-y-3">
              <Skeleton className="h-6 w-1/3" />
              <Skeleton className="h-32" />
            </div>
          ) : !change ? (
            <div className="text-sm text-[#f87171]">
              Failed to load change request.
            </div>
          ) : (
            <>
              {/* Status row */}
              <div className="flex items-center gap-2 flex-wrap">
                <StatusBadge status={change.status} />
                <ProtectedBadge isProtected={change.is_protected} />
                <span className="text-xs text-[#7a8599] font-mono">
                  by {change.requestor}
                </span>
                <span className="text-xs text-[#7a8599] font-mono">
                  · {new Date(change.created_at).toLocaleString()}
                </span>
                {change.decided_by && (
                  <span className="text-xs text-[#7a8599] font-mono">
                    · decided by {change.decided_by}
                  </span>
                )}
              </div>

              {/* Reason */}
              <section>
                <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider mb-2">
                  Reason
                </h3>
                <div className="text-sm text-[#cbd5e1] whitespace-pre-wrap">
                  {change.reason || '(no reason)'}
                </div>
              </section>

              {/* Pyright type-error surface (Phase 3 v2, 2026-05-22) */}
              <ChangeTypeErrorsSection changeId={changeId} />

              {/* On-demand pyright runner (works for any .py CR) */}
              <OnDemandTypeCheck change={change} />

              {/* Two-reasoner review (Phase 4 piece 2c, 2026-05-20) */}
              <ChangeReviewSection changeId={changeId} />


              {/* Decision metadata, if any */}
              {(change.decision_reason || change.decided_at) && (
                <section>
                  <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider mb-2">
                    Decision
                  </h3>
                  <div className="text-xs text-[#cbd5e1] space-y-1">
                    {change.decided_at && (
                      <div>
                        <span className="text-[#7a8599]">at:</span>{' '}
                        <span className="font-mono">
                          {new Date(change.decided_at).toLocaleString()}
                        </span>
                      </div>
                    )}
                    {change.decision_reason && (
                      <div>
                        <span className="text-[#7a8599]">note:</span>{' '}
                        {change.decision_reason}
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* Application metadata, if any */}
              {(change.git_branch ||
                change.git_commit_sha ||
                change.pr_url ||
                change.applied_at) && (
                <section>
                  <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider mb-2">
                    Apply
                  </h3>
                  <div className="text-xs space-y-1">
                    {change.applied_at && (
                      <div>
                        <span className="text-[#7a8599]">applied at:</span>{' '}
                        <span className="font-mono text-[#cbd5e1]">
                          {new Date(change.applied_at).toLocaleString()}
                        </span>
                      </div>
                    )}
                    {change.git_branch && (
                      <div>
                        <span className="text-[#7a8599]">branch:</span>{' '}
                        <code className="font-mono text-[#cbd5e1]">
                          {change.git_branch}
                        </code>
                      </div>
                    )}
                    {change.git_commit_sha && (
                      <div>
                        <span className="text-[#7a8599]">commit:</span>{' '}
                        <code className="font-mono text-[#cbd5e1]">
                          {change.git_commit_sha.slice(0, 12)}
                        </code>
                      </div>
                    )}
                    {change.pr_url && (
                      <div>
                        <span className="text-[#7a8599]">PR:</span>{' '}
                        <a
                          href={change.pr_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[#60a5fa] hover:underline font-mono break-all"
                        >
                          {change.pr_url}
                        </a>
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* Rollback metadata, if any */}
              {change.rolled_back_at && (
                <section>
                  <h3 className="text-xs font-semibold text-[#a78bfa] uppercase tracking-wider mb-2">
                    Rolled back
                  </h3>
                  <div className="text-xs space-y-1">
                    <div>
                      <span className="text-[#7a8599]">at:</span>{' '}
                      <span className="font-mono text-[#cbd5e1]">
                        {new Date(change.rolled_back_at).toLocaleString()}
                      </span>
                    </div>
                    {change.rolled_back_by && (
                      <div>
                        <span className="text-[#7a8599]">by:</span>{' '}
                        <span className="font-mono text-[#cbd5e1]">
                          {change.rolled_back_by}
                        </span>
                      </div>
                    )}
                    {change.rollback_pr_url && (
                      <div>
                        <span className="text-[#7a8599]">revert PR:</span>{' '}
                        <a
                          href={change.rollback_pr_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[#a78bfa] hover:underline font-mono break-all"
                        >
                          {change.rollback_pr_url}
                        </a>
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* Diff */}
              <section>
                <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider mb-2">
                  Diff
                </h3>
                <DiffView diff={change.diff} />
              </section>

              {/* Actions */}
              <section className="pt-2 border-t border-[#1e2738]">
                <ActionButtons change={change} onClose={onClose} />
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function ChangesPage() {
  const [statusFilter, setStatusFilter] = useState<ChangeStatus | 'all'>('all');
  const [activeId, setActiveId] = useState<string | null>(null);
  const listQ = useChangesListQuery(
    statusFilter === 'all' ? undefined : statusFilter,
  );

  const changes = listQ.data?.changes ?? [];

  // Counts per status for the row of stat cards. Server already filters
  // when `statusFilter !== 'all'`, so for accurate counts we'd want a
  // separate aggregated endpoint — for v1 just show counts of the current
  // (possibly filtered) view.
  const counts: Partial<Record<ChangeStatus, number>> = {};
  for (const c of changes) {
    counts[c.status] = (counts[c.status] ?? 0) + 1;
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Change Requests</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Agent-proposed code modifications to restricted paths. Approving
          here hot-applies the change and opens an auto-PR against{' '}
          <code className="font-mono">main</code>. Operator merge is gate 2.
          TIER_IMMUTABLE files are refused at request time and cannot be
          overridden.
        </p>
      </div>

      {/* Filter tabs */}
      <div className="flex flex-wrap gap-1.5">
        {STATUS_FILTERS.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              statusFilter === s
                ? 'bg-[#60a5fa]/15 text-[#60a5fa] border border-[#60a5fa]/30'
                : 'bg-[#111820] text-[#7a8599] border border-[#1e2738] hover:bg-[#1e2738]'
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* List */}
      {listQ.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
      ) : changes.length === 0 ? (
        <div className="p-8 text-center text-sm text-[#7a8599] border border-[#1e2738] rounded-lg bg-[#111820]">
          {statusFilter === 'all' ? (
            <>
              No change requests yet. Agents that have the{' '}
              <code className="font-mono text-[#fbbf24]">
                request_restricted_write
              </code>{' '}
              tool can propose code changes — they'll appear here when
              submitted.
            </>
          ) : (
            <>
              No change requests with status{' '}
              <code className="font-mono text-[#fbbf24]">{statusFilter}</code>.
            </>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {changes.map((c) => (
            <ChangeRow
              key={c.id}
              change={c}
              isActive={activeId === c.id}
              onClick={() => setActiveId(c.id)}
            />
          ))}
        </div>
      )}

      <ChangeDetailDrawer
        changeId={activeId}
        onClose={() => setActiveId(null)}
      />
    </div>
  );
}

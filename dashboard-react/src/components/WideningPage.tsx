// Trust-widening operator surface — /cp/widening.
//
// Phase 4 piece 1b (2026-05-20). The widening proposer (Phase 4
// piece 1) periodically scans the change-request log and proposes
// adding requestor / path combinations to the AUTO_APPLY allowlists
// when they have a clean approval track record (≥10 approvals,
// 0 rollbacks, ≤10% rejections, ≥30 days history by default).
//
// This page is where the operator confirms or rejects each proposal:
//   - List of pending proposals at the top
//   - Per-proposal expandable drawer with evidence breakdown
//   - Approve button → calls POST /approve which applies the widening
//     via the standard runtime_settings setters
//   - Reject button + optional reason input → records the rejection
//   - 10s auto-refresh
//   - Master-switch-off warning banner
//
// When the master switch (widening_proposer_enabled) is OFF, the
// page still works for reviewing past proposals — you just won't see
// new ones land.

import { useState } from 'react';
import {
  formatDecisionStatus,
  formatListName,
  useApproveWideningMutation,
  useRejectWideningMutation,
  useWideningAllQuery,
  useWideningPendingQuery,
  type WideningProposal,
} from '../api/widening';
import { useRuntimeSettingsQuery } from '../api/queries';
import { Skeleton } from './ui/Skeleton';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT_BG = '#111820';
const ACCENT_BORDER = '#1e2738';

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

function StatusBadge({ proposal }: { proposal: WideningProposal }) {
  const s = formatDecisionStatus(proposal.decision_status);
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${s.bg} ${s.fg}`}
    >
      {s.label}
    </span>
  );
}

// ── Proposal row + expandable drawer ───────────────────────────────

function ProposalRow({
  proposal,
  expanded,
  onToggle,
}: {
  proposal: WideningProposal;
  expanded: boolean;
  onToggle: () => void;
}) {
  const approveMut = useApproveWideningMutation();
  const rejectMut = useRejectWideningMutation();
  const [rejectReason, setRejectReason] = useState('');
  const [showRejectInput, setShowRejectInput] = useState(false);
  const isPending = proposal.decision_status === 'pending';
  const ev = proposal.evidence;

  return (
    <div
      className="rounded-lg border"
      style={{ background: ACCENT_BG, borderColor: ACCENT_BORDER }}
    >
      <button
        onClick={onToggle}
        className="w-full px-3 py-2 text-left flex items-center gap-3 hover:bg-[#1a2230]/40 transition-colors"
      >
        <StatusBadge proposal={proposal} />
        <code
          className="text-[10px] font-mono"
          style={{ color: TEXT_DIM, minWidth: '5rem' }}
        >
          {proposal.proposal_id.slice(0, 8)}
        </code>
        <span
          className="text-xs flex-1 truncate"
          style={{ color: TEXT_BRIGHT }}
        >
          {formatListName(proposal.list_name)} +=
          {' '}
          <code style={{ color: '#60a5fa' }}>{proposal.new_entry}</code>
        </span>
        <span className="text-[10px]" style={{ color: TEXT_DIM }}>
          {ev.approvals} approvals · {ev.rollbacks} rollbacks ·{' '}
          {ev.history_days.toFixed(0)}d
        </span>
        <span
          className="text-[10px]"
          style={{ color: TEXT_DIM, minWidth: '4rem', textAlign: 'right' }}
        >
          {formatRelative(proposal.proposed_at)}
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
          {/* Rationale */}
          <div>
            <div className="text-[10px] uppercase tracking-wider mb-1">
              Rationale
            </div>
            <div style={{ color: TEXT_BRIGHT }}>
              {proposal.rationale || '(no rationale recorded)'}
            </div>
          </div>

          {/* Evidence grid */}
          <div>
            <div className="text-[10px] uppercase tracking-wider mb-2">
              Evidence
            </div>
            <div className="grid grid-cols-2 gap-3">
              <EvidenceCell label="Requestor" value={ev.requestor} />
              <EvidenceCell label="Path prefix" value={ev.path_prefix} />
              <EvidenceCell label="Approvals" value={String(ev.approvals)} />
              <EvidenceCell
                label="Applied / rolled-back"
                value={`${ev.applied} / ${ev.rollbacks}`}
              />
              <EvidenceCell
                label="Rejections"
                value={`${ev.rejections} (${(ev.rejection_rate * 100).toFixed(1)}%)`}
              />
              <EvidenceCell
                label="Rollback rate"
                value={`${(ev.rollback_rate * 100).toFixed(1)}%`}
              />
              <EvidenceCell
                label="History"
                value={`${ev.history_days.toFixed(1)} days`}
              />
              <EvidenceCell
                label="First seen"
                value={ev.first_at?.slice(0, 16) || '—'}
              />
            </div>
          </div>

          {/* Sample CR IDs */}
          {ev.sample_cr_ids.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Sample CRs ({ev.sample_cr_ids.length})
              </div>
              <div className="flex flex-wrap gap-1">
                {ev.sample_cr_ids.map((id) => (
                  <code
                    key={id}
                    className="text-[10px] px-1.5 py-0.5 rounded"
                    style={{
                      background: '#0a1018',
                      color: TEXT_BRIGHT,
                    }}
                  >
                    {id.slice(0, 12)}
                  </code>
                ))}
              </div>
            </div>
          )}

          {/* Decision history */}
          {proposal.decision && (
            <div>
              <div className="text-[10px] uppercase tracking-wider mb-1">
                Decision
              </div>
              <div style={{ color: TEXT_BRIGHT }}>
                <strong
                  style={{
                    color:
                      proposal.decision.status === 'approved'
                        ? '#34d399'
                        : '#f87171',
                  }}
                >
                  {proposal.decision.status.toUpperCase()}
                </strong>{' '}
                by{' '}
                <code style={{ color: TEXT_DIM }}>
                  {proposal.decision.operator}
                </code>{' '}
                at {proposal.decision.decided_at.slice(0, 19)}
                {proposal.decision.reason && (
                  <div className="mt-1" style={{ color: TEXT_DIM }}>
                    "{proposal.decision.reason}"
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Approve / reject actions (only when pending) */}
          {isPending && (
            <div className="pt-1 space-y-2">
              {!showRejectInput ? (
                <div className="flex gap-2">
                  <button
                    onClick={() =>
                      approveMut.mutate({
                        proposalId: proposal.proposal_id,
                      })
                    }
                    disabled={approveMut.isPending}
                    className="text-xs px-3 py-1 rounded border border-[#34d399]/30 bg-[#34d399]/15 text-[#34d399] hover:bg-[#34d399]/25 disabled:opacity-50"
                  >
                    {approveMut.isPending ? 'Approving…' : '✓ Approve + widen'}
                  </button>
                  <button
                    onClick={() => setShowRejectInput(true)}
                    className="text-xs px-3 py-1 rounded border border-[#f87171]/30 bg-[#f87171]/15 text-[#f87171] hover:bg-[#f87171]/25"
                  >
                    ✕ Reject
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  <input
                    type="text"
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    placeholder="Reason for rejection (optional)"
                    className="w-full px-2 py-1 text-xs rounded"
                    style={{
                      background: '#0a1018',
                      color: TEXT_BRIGHT,
                      border: `1px solid ${ACCENT_BORDER}`,
                    }}
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() =>
                        rejectMut.mutate({
                          proposalId: proposal.proposal_id,
                          body: { reason: rejectReason },
                        })
                      }
                      disabled={rejectMut.isPending}
                      className="text-xs px-3 py-1 rounded border border-[#f87171]/30 bg-[#f87171]/15 text-[#f87171] hover:bg-[#f87171]/25 disabled:opacity-50"
                    >
                      {rejectMut.isPending ? 'Rejecting…' : 'Confirm reject'}
                    </button>
                    <button
                      onClick={() => {
                        setShowRejectInput(false);
                        setRejectReason('');
                      }}
                      className="text-xs px-3 py-1 rounded"
                      style={{ color: TEXT_DIM }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
              {(approveMut.error || rejectMut.error) && (
                <div className="text-[10px] text-[#f87171]">
                  Error:{' '}
                  {String(approveMut.error || rejectMut.error)}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EvidenceCell({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider">{label}</div>
      <code className="text-xs" style={{ color: TEXT_BRIGHT }}>
        {value}
      </code>
    </div>
  );
}

// ── Master-switch warning banner ───────────────────────────────────

function MasterSwitchBanner() {
  const settingsQ = useRuntimeSettingsQuery();
  const enabled =
    settingsQ.data?.widening_proposer_enabled === true;
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
      ⚠{' '}
      <strong>Widening proposer is OFF.</strong> New proposals won't be
      generated until you flip{' '}
      <code>widening_proposer_enabled</code> in /cp/settings. Past
      proposals (if any) remain reviewable below.
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────

export function WideningPage() {
  const pendingQ = useWideningPendingQuery();
  const allQ = useWideningAllQuery();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showAll, setShowAll] = useState(false);

  const toggle = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const pending = pendingQ.data?.proposals ?? [];
  const all = allQ.data?.proposals ?? [];
  const decided = all.filter((p) => p.decision_status !== 'pending');

  return (
    <div className="space-y-4 max-w-5xl">
      <div>
        <h1
          className="text-xl font-semibold"
          style={{ color: TEXT_BRIGHT }}
        >
          Trust widening proposals
        </h1>
        <p className="text-xs mt-1" style={{ color: TEXT_DIM }}>
          When (requestor, path) combinations show a strong approval
          track record over time, the system proposes adding them to
          the AUTO_APPLY allowlists. You confirm; the change applies
          via the same setter the React /cp/settings card uses.
        </p>
      </div>

      <MasterSwitchBanner />

      {/* Pending */}
      <section>
        <h2
          className="text-sm font-medium mb-2"
          style={{ color: TEXT_BRIGHT }}
        >
          Pending review{' '}
          <span style={{ color: TEXT_DIM, fontWeight: 'normal' }}>
            ({pending.length})
          </span>
        </h2>
        {pendingQ.isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : pendingQ.error ? (
          <div className="text-xs text-[#f87171]">
            Failed to load pending proposals: {String(pendingQ.error)}
          </div>
        ) : pending.length === 0 ? (
          <div
            className="text-xs italic px-3 py-2 rounded border"
            style={{
              color: TEXT_DIM,
              background: ACCENT_BG,
              borderColor: ACCENT_BORDER,
            }}
          >
            (no pending proposals — the system hasn't observed any
            qualifying patterns yet)
          </div>
        ) : (
          <div className="space-y-2">
            {pending.map((p) => (
              <ProposalRow
                key={p.proposal_id}
                proposal={p}
                expanded={!!expanded[p.proposal_id]}
                onToggle={() => toggle(p.proposal_id)}
              />
            ))}
          </div>
        )}
      </section>

      {/* Decided (collapsed by default) */}
      <section>
        <button
          onClick={() => setShowAll(!showAll)}
          className="text-sm font-medium flex items-center gap-1 hover:opacity-80"
          style={{ color: TEXT_BRIGHT }}
        >
          {showAll ? '▼' : '▶'} Decided{' '}
          <span style={{ color: TEXT_DIM, fontWeight: 'normal' }}>
            ({decided.length})
          </span>
        </button>
        {showAll && (
          <div className="mt-2">
            {decided.length === 0 ? (
              <div
                className="text-xs italic px-3 py-2 rounded border"
                style={{
                  color: TEXT_DIM,
                  background: ACCENT_BG,
                  borderColor: ACCENT_BORDER,
                }}
              >
                (no decided proposals yet)
              </div>
            ) : (
              <div className="space-y-2">
                {decided.map((p) => (
                  <ProposalRow
                    key={p.proposal_id}
                    proposal={p}
                    expanded={!!expanded[p.proposal_id]}
                    onToggle={() => toggle(p.proposal_id)}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

import { useState } from 'react';
import { useApi } from '../hooks/useApi';
import type { GovernanceRequest } from '../types/index.ts';
import { api } from '../api/client';

function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse bg-[#1e2738] rounded ${className}`} />;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const TYPE_COLORS: Record<string, string> = {
  budget_override: 'text-[#fbbf24] bg-[#fbbf24]/10 border-[#fbbf24]/20',
  agent_spawn: 'text-[#a78bfa] bg-[#a78bfa]/10 border-[#a78bfa]/20',
  policy_change: 'text-[#fb923c] bg-[#fb923c]/10 border-[#fb923c]/20',
  escalation: 'text-[#f87171] bg-[#f87171]/10 border-[#f87171]/20',
  default: 'text-[#22d3ee] bg-[#22d3ee]/10 border-[#22d3ee]/20',
};

export function GovernanceQueue() {
  const { data: pending, loading, error, refetch } = useApi<GovernanceRequest[]>(
    '/governance/pending',
    15000
  );
  const [actionState, setActionState] = useState<Record<string, 'approving' | 'rejecting' | 'approved' | 'rejected'>>({});
  const [rejectNote, setRejectNote] = useState<Record<string, string>>({});

  const handleApprove = async (id: string) => {
    setActionState((s) => ({ ...s, [id]: 'approving' }));
    try {
      await api(`/governance/${id}/approve`, { method: 'POST', body: JSON.stringify({}) });
      setActionState((s) => ({ ...s, [id]: 'approved' }));
      setTimeout(() => refetch(), 1000);
    } catch {
      setActionState((s) => {
        const next = { ...s };
        delete next[id];
        return next;
      });
    }
  };

  const handleReject = async (id: string) => {
    setActionState((s) => ({ ...s, [id]: 'rejecting' }));
    try {
      await api(`/governance/${id}/reject`, {
        method: 'POST',
        body: JSON.stringify({ reason: rejectNote[id] || undefined }),
      });
      setActionState((s) => ({ ...s, [id]: 'rejected' }));
      setTimeout(() => refetch(), 1000);
    } catch {
      setActionState((s) => {
        const next = { ...s };
        delete next[id];
        return next;
      });
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Governance Queue</h1>
        <p className="text-sm text-[#7a8599] mt-1">Review and approve or reject pending requests</p>
      </div>

      {loading ? (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : error ? (
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-8 text-center text-[#f87171] text-sm">
          {error}
        </div>
      ) : !pending || pending.length === 0 ? (
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-12 text-center">
          <div className="text-4xl mb-3">✅</div>
          <p className="text-[#e2e8f0] font-medium">All clear</p>
          <p className="text-sm text-[#7a8599] mt-1">No pending governance requests.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-[#fbbf24]/20 text-[#fbbf24]">
              {pending.length} pending
            </span>
          </div>

          {pending.map((req) => {
            const state = actionState[req.id];
            const typeColor =
              TYPE_COLORS[req.type] ?? TYPE_COLORS.default;

            const isResolved = state === 'approved' || state === 'rejected';

            return (
              <div
                key={req.id}
                className={`bg-[#111820] border rounded-lg p-4 transition-all ${
                  isResolved
                    ? state === 'approved'
                      ? 'border-[#34d399]/30 opacity-70'
                      : 'border-[#f87171]/30 opacity-70'
                    : 'border-[#1e2738]'
                }`}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-2">
                      <span className={`text-xs px-2 py-0.5 rounded border ${typeColor}`}>
                        {req.type.replace(/_/g, ' ')}
                      </span>
                      <span className="text-xs text-[#7a8599]">{timeAgo(req.created_at)}</span>
                    </div>
                    <h3 className="text-sm font-medium text-[#e2e8f0] mb-1">{req.title}</h3>
                    {req.description && (
                      <p className="text-xs text-[#7a8599] mb-2">{req.description}</p>
                    )}
                    <p className="text-xs text-[#7a8599]">
                      Requested by{' '}
                      <span className="text-[#60a5fa]">{req.requested_by}</span>
                    </p>
                  </div>

                  {isResolved ? (
                    <div
                      className={`flex-shrink-0 text-sm font-medium px-3 py-1 rounded-lg ${
                        state === 'approved'
                          ? 'text-[#34d399] bg-[#34d399]/10'
                          : 'text-[#f87171] bg-[#f87171]/10'
                      }`}
                    >
                      {state === 'approved' ? '✓ Approved' : '✗ Rejected'}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2 flex-shrink-0">
                      <button
                        onClick={() => handleApprove(req.id)}
                        disabled={state === 'approving' || state === 'rejecting'}
                        className="px-4 py-1.5 bg-[#34d399]/20 border border-[#34d399]/30 text-[#34d399] text-sm rounded-lg hover:bg-[#34d399]/30 disabled:opacity-50 transition-colors"
                      >
                        {state === 'approving' ? 'Approving...' : 'Approve'}
                      </button>
                      <button
                        onClick={() => handleReject(req.id)}
                        disabled={state === 'approving' || state === 'rejecting'}
                        className="px-4 py-1.5 bg-[#f87171]/20 border border-[#f87171]/30 text-[#f87171] text-sm rounded-lg hover:bg-[#f87171]/30 disabled:opacity-50 transition-colors"
                      >
                        {state === 'rejecting' ? 'Rejecting...' : 'Reject'}
                      </button>
                    </div>
                  )}
                </div>

                {!isResolved && (
                  <div className="mt-3 pt-3 border-t border-[#1e2738]">
                    <input
                      type="text"
                      placeholder="Rejection reason (optional)..."
                      value={rejectNote[req.id] ?? ''}
                      onChange={(e) =>
                        setRejectNote((n) => ({ ...n, [req.id]: e.target.value }))
                      }
                      className="w-full bg-[#0a0e14] border border-[#1e2738] rounded-lg px-3 py-1.5 text-xs text-[#e2e8f0] placeholder-[#7a8599] focus:outline-none focus:border-[#60a5fa]"
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

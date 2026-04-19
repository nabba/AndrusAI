import { useState } from 'react';
import { useProject } from '../context/useProject';
import { priorityLabel, difficultyLabel } from '../types';
import type { Ticket, TicketStatus, KanbanColumnKey } from '../types';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useTicketBoardQuery,
  useUpdateTicketStatus,
  useAddTicketComment,
} from '../api/queries';

const COLUMNS: { key: KanbanColumnKey; label: string; color: string }[] = [
  { key: 'todo', label: 'To Do', color: 'text-[#e2e8f0]' },
  { key: 'in_progress', label: 'In Progress', color: 'text-[#60a5fa]' },
  { key: 'review', label: 'Review', color: 'text-[#a78bfa]' },
  { key: 'blocked', label: 'Blocked', color: 'text-[#fb923c]' },
  { key: 'done', label: 'Done', color: 'text-[#34d399]' },
  { key: 'failed', label: 'Failed', color: 'text-[#f87171]' },
];

const PRIORITY_COLORS: Record<string, string> = {
  low: 'text-[#7a8599] border-[#7a8599]/30 bg-[#7a8599]/10',
  medium: 'text-[#fbbf24] border-[#fbbf24]/30 bg-[#fbbf24]/10',
  high: 'text-[#fb923c] border-[#fb923c]/30 bg-[#fb923c]/10',
  critical: 'text-[#f87171] border-[#f87171]/30 bg-[#f87171]/10',
};

// Difficulty is the Commander-routed task complexity 1-10 — far more useful
// on the card than priority, which is always 5 (the schema default that
// nothing in the routing pipeline ever overrides).
const DIFFICULTY_COLORS: Record<string, string> = {
  trivial: 'text-[#7a8599] border-[#7a8599]/30 bg-[#7a8599]/10',   // 1-2
  easy:    'text-[#34d399] border-[#34d399]/30 bg-[#34d399]/10',   // 3-4
  moderate:'text-[#fbbf24] border-[#fbbf24]/30 bg-[#fbbf24]/10',   // 5-6
  hard:    'text-[#fb923c] border-[#fb923c]/30 bg-[#fb923c]/10',   // 7-8
  extreme: 'text-[#f87171] border-[#f87171]/30 bg-[#f87171]/10',   // 9-10
};

function TicketCard({ ticket, onClick }: { ticket: Ticket; onClick: () => void }) {
  const dLabel = difficultyLabel(ticket.difficulty);
  return (
    <div
      onClick={onClick}
      className="bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3 cursor-pointer hover:border-[#60a5fa]/40 transition-colors group"
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <h3 className="text-sm text-[#e2e8f0] group-hover:text-[#60a5fa] transition-colors leading-snug">
          {ticket.title}
        </h3>
        {ticket.difficulty != null && (
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded border flex-shrink-0 font-medium ${DIFFICULTY_COLORS[dLabel]}`}
            title={`difficulty ${ticket.difficulty}/10 (${dLabel})`}
          >
            d{ticket.difficulty}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-[#7a8599] truncate max-w-[120px]">
          {ticket.assigned_agent ?? 'Unassigned'}
        </span>
        {ticket.cost_usd != null && (
          <span className="text-[#34d399]">${ticket.cost_usd.toFixed(4)}</span>
        )}
      </div>
    </div>
  );
}

function TicketModal({
  ticket,
  onClose,
}: {
  ticket: Ticket;
  onClose: () => void;
}) {
  const [comment, setComment] = useState('');
  const [pending, setPending] = useState<TicketStatus | null>(null);
  const updateStatus = useUpdateTicketStatus();
  const addComment = useAddTicketComment();

  const handleStatusChange = async (status: TicketStatus) => {
    setPending(status);
    try {
      await updateStatus.mutateAsync({ id: ticket.id, status });
      onClose();
    } catch {
      setPending(null);
    }
  };

  const handleComment = async () => {
    if (!comment.trim()) return;
    try {
      await addComment.mutateAsync({ id: ticket.id, body: comment });
      setComment('');
    } catch {
      // surfaced via addComment.error
    }
  };

  const pLabel = priorityLabel(ticket.priority);
  const dLabel = difficultyLabel(ticket.difficulty);
  const currentStatus = pending ?? ticket.status;

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[#111820] border border-[#1e2738] rounded-xl w-full max-w-lg max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between p-4 border-b border-[#1e2738]">
          <div>
            <h2 className="text-base font-semibold text-[#e2e8f0]">{ticket.title}</h2>
            <p className="text-xs text-[#7a8599] mt-1">#{ticket.id}</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-[#7a8599] hover:text-[#e2e8f0] p-1 rounded"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-4 space-y-4">
          {ticket.description && (
            <p className="text-sm text-[#7a8599]">{ticket.description}</p>
          )}

          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-[#7a8599]">Assigned to</span>
              <p className="text-[#e2e8f0] mt-0.5">{ticket.assigned_agent ?? 'Unassigned'}</p>
            </div>
            <div>
              <span className="text-[#7a8599]">Difficulty</span>
              <p className={`mt-0.5 ${ticket.difficulty != null ? DIFFICULTY_COLORS[dLabel].split(' ')[0] : 'text-[#7a8599]'}`}>
                {ticket.difficulty != null
                  ? `${dLabel} (${ticket.difficulty}/10)`
                  : '—'}
              </p>
            </div>
            <div>
              <span className="text-[#7a8599]">Priority</span>
              <p className={`mt-0.5 ${PRIORITY_COLORS[pLabel].split(' ')[0]}`}>
                {pLabel} (p{ticket.priority})
              </p>
            </div>
            <div>
              <span className="text-[#7a8599]">Cost</span>
              <p className="text-[#34d399] mt-0.5">
                {ticket.cost_usd != null ? `$${ticket.cost_usd.toFixed(4)}` : '—'}
              </p>
            </div>
            <div>
              <span className="text-[#7a8599]">Created</span>
              <p className="text-[#e2e8f0] mt-0.5">
                {new Date(ticket.created_at).toLocaleDateString()}
              </p>
            </div>
          </div>

          <div>
            <span className="text-xs text-[#7a8599]">Status</span>
            <div className="flex flex-wrap gap-2 mt-2">
              {COLUMNS.map((col) => (
                <button
                  key={col.key}
                  onClick={() => handleStatusChange(col.key)}
                  disabled={updateStatus.isPending}
                  className={`text-xs px-2.5 py-1 rounded-lg border transition-colors disabled:opacity-50 ${
                    currentStatus === col.key
                      ? 'border-[#60a5fa] bg-[#60a5fa]/10 text-[#60a5fa]'
                      : 'border-[#1e2738] text-[#7a8599] hover:border-[#7a8599]'
                  }`}
                >
                  {col.label}
                </button>
              ))}
            </div>
            {updateStatus.error && (
              <p className="text-xs text-[#f87171] mt-2">
                {(updateStatus.error as Error).message}
              </p>
            )}
          </div>

          {ticket.comments && ticket.comments.length > 0 && (
            <div>
              <span className="text-xs text-[#7a8599] font-medium">Comments</span>
              <div className="mt-2 space-y-2">
                {ticket.comments.map((c) => (
                  <div key={c.id} className="bg-[#1e2738] rounded-lg p-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-medium text-[#60a5fa]">{c.author}</span>
                      <span className="text-xs text-[#7a8599]">
                        {new Date(c.created_at).toLocaleString()}
                      </span>
                    </div>
                    <p className="text-sm text-[#e2e8f0]">{c.content}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Add a comment..."
              rows={3}
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded-lg p-2.5 text-sm text-[#e2e8f0] placeholder-[#7a8599] focus:outline-none focus:border-[#60a5fa] resize-none"
            />
            <button
              onClick={handleComment}
              disabled={addComment.isPending || !comment.trim()}
              className="mt-2 px-4 py-1.5 bg-[#60a5fa]/20 border border-[#60a5fa]/30 text-[#60a5fa] text-sm rounded-lg hover:bg-[#60a5fa]/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {addComment.isPending ? 'Posting...' : 'Comment'}
            </button>
            {addComment.error && (
              <p className="text-xs text-[#f87171] mt-2">
                {(addComment.error as Error).message}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function KanbanBoard() {
  const { activeProject } = useProject();
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);
  const { data: board, isLoading, error, refetch } = useTicketBoardQuery(activeProject?.id);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#e2e8f0]">Tickets</h1>
          <p className="text-sm text-[#7a8599] mt-1">
            {activeProject ? activeProject.name : 'All projects'}
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          {COLUMNS.map((col) => (
            <div key={col.key} className="space-y-3">
              <Skeleton className="h-6 w-24" />
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-20" />
              ))}
            </div>
          ))}
        </div>
      ) : error ? (
        <ErrorPanel error={error} onRetry={refetch} />
      ) : !board ? (
        <div className="text-center text-[#7a8599] py-12">Failed to load board.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-4 overflow-x-auto">
          {COLUMNS.map((col) => {
            const tickets = board.board[col.key] ?? [];
            return (
              <div key={col.key} className="min-w-[200px]">
                <div className="flex items-center gap-2 mb-3">
                  <h2 className={`text-sm font-medium ${col.color}`}>{col.label}</h2>
                  <span className="text-xs text-[#7a8599] bg-[#1e2738] rounded-full px-2 py-0.5">
                    {tickets.length}
                  </span>
                </div>
                <div className="space-y-2">
                  {tickets.length === 0 ? (
                    <div className="bg-[#111820] border border-dashed border-[#1e2738] rounded-lg p-4 text-center text-xs text-[#7a8599]">
                      Empty
                    </div>
                  ) : (
                    tickets.map((ticket) => (
                      <TicketCard
                        key={ticket.id}
                        ticket={ticket}
                        onClick={() => setSelectedTicket(ticket)}
                      />
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selectedTicket && (
        <TicketModal
          ticket={selectedTicket}
          onClose={() => setSelectedTicket(null)}
        />
      )}
    </div>
  );
}

import { useMemo, useState } from 'react';
import { useProject } from '../context/useProject';
import { priorityLabel, difficultyLabel } from '../types';
import type { Ticket, TicketStatus, KanbanColumnKey, KanbanBoard as KanbanBoardType } from '../types';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useTicketBoardQuery,
  useUpdateTicketStatus,
  useAddTicketComment,
  keys,
} from '../api/queries';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { useQueryClient } from '@tanstack/react-query';

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

const DIFFICULTY_COLORS: Record<string, string> = {
  trivial: 'text-[#7a8599] border-[#7a8599]/30 bg-[#7a8599]/10',
  easy:    'text-[#34d399] border-[#34d399]/30 bg-[#34d399]/10',
  moderate:'text-[#fbbf24] border-[#fbbf24]/30 bg-[#fbbf24]/10',
  hard:    'text-[#fb923c] border-[#fb923c]/30 bg-[#fb923c]/10',
  extreme: 'text-[#f87171] border-[#f87171]/30 bg-[#f87171]/10',
};

// ── Ticket card (presentational — reused by both draggable and overlay) ────

function TicketCardContent({ ticket }: { ticket: Ticket }) {
  const dLabel = difficultyLabel(ticket.difficulty);
  return (
    <>
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
    </>
  );
}

function DraggableTicket({ ticket, onClick }: { ticket: Ticket; onClick: () => void }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: ticket.id,
    data: { ticket },
  });

  // When dragging: hide the source card (the DragOverlay renders it at the cursor).
  // Clicks are suppressed by dnd-kit's 6 px activation distance; below that,
  // pointerdown bubbles up and onClick fires as expected.
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      onClick={onClick}
      className={`bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3 cursor-grab active:cursor-grabbing hover:border-[#60a5fa]/40 transition-colors group touch-none ${
        isDragging ? 'opacity-30' : ''
      }`}
    >
      <TicketCardContent ticket={ticket} />
    </div>
  );
}

function DroppableColumn({
  col,
  children,
  isOver,
}: {
  col: { key: KanbanColumnKey; label: string; color: string };
  children: React.ReactNode;
  isOver: boolean;
}) {
  const { setNodeRef } = useDroppable({ id: col.key });
  return (
    <div
      ref={setNodeRef}
      className={`min-w-[200px] rounded-lg transition-colors ${isOver ? 'bg-[#60a5fa]/5 ring-1 ring-[#60a5fa]/30 ring-inset' : ''}`}
    >
      {children}
    </div>
  );
}

// ── Modal (unchanged behavior, still opens on click) ──────────────────────

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
                {ticket.difficulty != null ? `${dLabel} (${ticket.difficulty}/10)` : '—'}
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

// ── Board ─────────────────────────────────────────────────────────────────

export function KanbanBoard() {
  const { activeProject } = useProject();
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [overColumn, setOverColumn] = useState<KanbanColumnKey | null>(null);
  const { data: board, isLoading, error, refetch } = useTicketBoardQuery(activeProject?.id);
  const updateStatus = useUpdateTicketStatus();
  const queryClient = useQueryClient();

  // Pointer sensor with an activation distance so click-to-open still works
  // and tap-scroll on mobile isn't hijacked by the drag handler.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  // Build a quick lookup of id → ticket so DragOverlay can render the card
  // that's currently under the cursor.
  const ticketsById = useMemo(() => {
    const map = new Map<string, Ticket>();
    if (board) {
      for (const col of COLUMNS) for (const t of board.board[col.key] ?? []) map.set(t.id, t);
    }
    return map;
  }, [board]);

  const activeTicket = activeDragId ? ticketsById.get(activeDragId) : null;

  const handleDragStart = (event: DragStartEvent) => {
    setActiveDragId(String(event.active.id));
  };

  const handleDragOver = (event: { over: { id: string | number } | null }) => {
    const id = event.over?.id ? String(event.over.id) : null;
    if (id && COLUMNS.some((c) => c.key === id)) setOverColumn(id as KanbanColumnKey);
    else setOverColumn(null);
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    setActiveDragId(null);
    setOverColumn(null);
    const { active, over } = event;
    if (!over || !board) return;

    const ticket = ticketsById.get(String(active.id));
    if (!ticket) return;
    const target = String(over.id) as TicketStatus;
    if (ticket.status === target) return;
    if (!COLUMNS.some((c) => c.key === target)) return;

    // Optimistic update: move the ticket in the cached board, then mutate.
    const queryKey = keys.ticketsBoard(activeProject?.id);
    const previous = queryClient.getQueryData<KanbanBoardType>(queryKey);
    if (previous) {
      const next: KanbanBoardType = {
        ...previous,
        board: { ...previous.board },
        counts: { ...previous.counts },
      };
      next.board[ticket.status] = (next.board[ticket.status] ?? []).filter((t) => t.id !== ticket.id);
      next.board[target] = [{ ...ticket, status: target }, ...(next.board[target] ?? [])];
      next.counts[ticket.status] = next.board[ticket.status].length;
      next.counts[target] = next.board[target].length;
      queryClient.setQueryData<KanbanBoardType>(queryKey, next);
    }

    try {
      await updateStatus.mutateAsync({ id: ticket.id, status: target });
    } catch {
      // Roll back on failure. Mutation's onSuccess already invalidates on
      // success, which will re-fetch the authoritative state.
      if (previous) queryClient.setQueryData(queryKey, previous);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#e2e8f0]">Tickets</h1>
          <p className="text-sm text-[#7a8599] mt-1">
            {activeProject ? activeProject.name : 'All projects'}
            <span className="opacity-60"> · drag cards between columns to change status</span>
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
        <DndContext
          sensors={sensors}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDragEnd={handleDragEnd}
          onDragCancel={() => { setActiveDragId(null); setOverColumn(null); }}
        >
          <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-6 gap-4 overflow-x-auto">
            {COLUMNS.map((col) => {
              const tickets = board.board[col.key] ?? [];
              return (
                <DroppableColumn key={col.key} col={col} isOver={overColumn === col.key}>
                  <div className="flex items-center gap-2 mb-3 px-1">
                    <h2 className={`text-sm font-medium ${col.color}`}>{col.label}</h2>
                    <span className="text-xs text-[#7a8599] bg-[#1e2738] rounded-full px-2 py-0.5">
                      {tickets.length}
                    </span>
                  </div>
                  <div className="space-y-2 p-1">
                    {tickets.length === 0 ? (
                      <div className="bg-[#111820] border border-dashed border-[#1e2738] rounded-lg p-4 text-center text-xs text-[#7a8599]">
                        {overColumn === col.key ? 'Drop here' : 'Empty'}
                      </div>
                    ) : (
                      tickets.map((ticket) => (
                        <DraggableTicket
                          key={ticket.id}
                          ticket={ticket}
                          onClick={() => setSelectedTicket(ticket)}
                        />
                      ))
                    )}
                  </div>
                </DroppableColumn>
              );
            })}
          </div>

          <DragOverlay dropAnimation={null}>
            {activeTicket ? (
              <div className="bg-[#0a0e14] border border-[#60a5fa]/60 rounded-lg p-3 shadow-2xl rotate-2 cursor-grabbing w-[220px]">
                <TicketCardContent ticket={activeTicket} />
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
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

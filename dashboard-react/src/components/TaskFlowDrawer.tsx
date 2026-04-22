import { useMemo, useState } from 'react';
import {
  useTaskTimelineQuery,
  type TaskSpan,
  type TaskSpanType,
} from '../api/queries';

type ViewMode = 'tree' | 'timeline';

const SPAN_COLORS: Record<TaskSpanType, { border: string; bg: string; text: string; icon: string }> = {
  agent:    { border: 'border-[#a78bfa]/40', bg: 'bg-[#a78bfa]/15', text: 'text-[#a78bfa]', icon: '🧑' },
  tool:     { border: 'border-[#fbbf24]/40', bg: 'bg-[#fbbf24]/15', text: 'text-[#fbbf24]', icon: '🔧' },
  llm_call: { border: 'border-[#60a5fa]/40', bg: 'bg-[#60a5fa]/15', text: 'text-[#60a5fa]', icon: '🧠' },
};

const STATE_COLORS: Record<string, string> = {
  running:   'text-[#22d3ee]',
  completed: 'text-[#34d399]',
  failed:    'text-[#f87171]',
};

function formatDuration(startedAt: string, completedAt: string | null | undefined): string {
  const start = Date.parse(startedAt);
  const end = completedAt ? Date.parse(completedAt) : Date.now();
  const ms = Math.max(0, end - start);
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function flattenTree(spans: TaskSpan[], depth = 0): Array<TaskSpan & { depth: number }> {
  const out: Array<TaskSpan & { depth: number }> = [];
  for (const s of spans) {
    out.push({ ...s, depth });
    if (s.children?.length) out.push(...flattenTree(s.children, depth + 1));
  }
  return out;
}

export function TaskFlowDrawer({
  taskId,
  onClose,
}: {
  taskId: string | null;
  onClose: () => void;
}) {
  // IMPORTANT: every hook below MUST run on every render, regardless of
  // ``taskId``. Previously an early ``if (!taskId) return null`` lived
  // between the first two hooks and the useMemo calls — that caused a
  // different number of hooks on the initial null-taskId render vs.
  // after a row was clicked, tripping React's "rules of hooks" check
  // (minified error #310). The early return now happens AFTER all hooks.
  const [view, setView] = useState<ViewMode>('tree');
  const q = useTaskTimelineQuery(taskId);

  const task = q.data?.task;
  const spans = q.data?.spans ?? [];
  const flatSpans = useMemo(() => flattenTree(spans), [spans]);

  // Timeline range: task start → max(now if running, else max span end).
  const { timelineStartMs, timelineEndMs } = useMemo(() => {
    const startStr = task?.started_at;
    const start = startStr ? Date.parse(startStr) : Date.now();
    let end = task?.completed_at ? Date.parse(task.completed_at) : Date.now();
    for (const s of flatSpans) {
      const spanEnd = s.completed_at ? Date.parse(s.completed_at) : Date.now();
      if (spanEnd > end) end = spanEnd;
    }
    return { timelineStartMs: start, timelineEndMs: Math.max(end, start + 1000) };
  }, [task?.started_at, task?.completed_at, flatSpans]);

  // Safe to early-return now — hook order is locked.
  if (!taskId) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/60"
      onClick={onClose}
    >
      <div
        className="relative bg-[#0a0e14] border-l border-[#1e2738] w-full max-w-3xl h-full overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 bg-[#0a0e14] border-b border-[#1e2738] px-4 py-3 z-10">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-sm font-medium text-[#e2e8f0] truncate">
                Task Flow · {task?.crew ?? '…'}
                {task?.state && (
                  <span className={`ml-2 text-[10px] uppercase tracking-wider ${STATE_COLORS[task.state] ?? 'text-[#7a8599]'}`}>
                    {task.state}
                  </span>
                )}
              </h3>
              <div className="text-[10px] text-[#7a8599] font-mono truncate">
                {taskId}
              </div>
            </div>
            <button
              onClick={onClose}
              className="text-[#7a8599] hover:text-[#e2e8f0] px-2 py-1"
              aria-label="Close drawer"
            >
              ✕
            </button>
          </div>
          {/* View toggle */}
          <div className="flex gap-1 mt-2">
            <button
              onClick={() => setView('tree')}
              className={`text-xs px-3 py-1 rounded border transition-colors ${
                view === 'tree'
                  ? 'border-[#60a5fa] bg-[#60a5fa]/10 text-[#e2e8f0]'
                  : 'border-[#1e2738] bg-[#0a0e14] text-[#7a8599] hover:border-[#60a5fa]/40 hover:text-[#e2e8f0]'
              }`}
            >
              🌳 Tree
            </button>
            <button
              onClick={() => setView('timeline')}
              className={`text-xs px-3 py-1 rounded border transition-colors ${
                view === 'timeline'
                  ? 'border-[#60a5fa] bg-[#60a5fa]/10 text-[#e2e8f0]'
                  : 'border-[#1e2738] bg-[#0a0e14] text-[#7a8599] hover:border-[#60a5fa]/40 hover:text-[#e2e8f0]'
              }`}
            >
              ⏱️ Timeline
            </button>
            <span className="ml-auto text-[10px] text-[#7a8599] self-center">
              {q.data?.span_count ?? 0} span{(q.data?.span_count ?? 0) === 1 ? '' : 's'}
              {task?.state === 'running' && <span className="ml-2 text-[#22d3ee]">· live (2s poll)</span>}
            </span>
          </div>
        </div>

        {/* Body */}
        <div className="p-4">
          {q.isLoading && <p className="text-xs text-[#7a8599] italic">Loading…</p>}
          {q.error && (
            <p className="text-xs text-[#f87171]">
              Failed to load timeline: {String((q.error as Error).message)}
            </p>
          )}

          {/* Task summary */}
          {task && (
            <div className="mb-4 bg-[#111820] border border-[#1e2738] rounded-lg p-3 space-y-1">
              {task.summary && (
                <div className="text-sm text-[#e2e8f0]">{task.summary}</div>
              )}
              <div className="text-[11px] text-[#7a8599] flex flex-wrap gap-x-4 gap-y-1">
                {task.model && <span>model: <span className="text-[#a78bfa]">{task.model}</span></span>}
                {task.tokens_used != null && task.tokens_used > 0 && <span>tokens: {task.tokens_used.toLocaleString()}</span>}
                {task.cost_usd != null && task.cost_usd > 0 && <span>cost: ${task.cost_usd.toFixed(4)}</span>}
                {task.started_at && (
                  <span>duration: {formatDuration(task.started_at, task.completed_at)}</span>
                )}
              </div>
              {task.error && (
                <div className="text-[11px] text-[#f87171] mt-1">Error: {task.error}</div>
              )}
            </div>
          )}

          {/* Empty state */}
          {!q.isLoading && flatSpans.length === 0 && (
            <div className="text-sm text-[#7a8599] italic bg-[#111820] border border-[#1e2738] rounded-lg p-4 text-center">
              No execution spans recorded for this task yet.
              {task?.state === 'running' && ' Waiting for agent/tool events…'}
            </div>
          )}

          {/* Views */}
          {view === 'tree' && flatSpans.length > 0 && (
            <TreeView spans={flatSpans} />
          )}
          {view === 'timeline' && flatSpans.length > 0 && (
            <TimelineView
              spans={flatSpans}
              startMs={timelineStartMs}
              endMs={timelineEndMs}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Tree view ────────────────────────────────────────────────────────────────

function TreeView({ spans }: { spans: Array<TaskSpan & { depth: number }> }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  return (
    <ul className="space-y-1">
      {spans.map((s) => {
        const colors = SPAN_COLORS[s.span_type];
        const stateColor = STATE_COLORS[s.state] ?? 'text-[#7a8599]';
        const isExpanded = expanded.has(s.id);
        const hasDetail = s.detail && Object.keys(s.detail).length > 0;

        return (
          <li
            key={s.id}
            style={{ marginLeft: `${s.depth * 20}px` }}
            className={`border-l-2 ${colors.border} pl-3`}
          >
            <button
              onClick={() => {
                if (!hasDetail) return;
                setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(s.id)) next.delete(s.id);
                  else next.add(s.id);
                  return next;
                });
              }}
              className="w-full text-left hover:bg-[#111820] rounded px-2 py-1 transition-colors"
              disabled={!hasDetail}
            >
              <div className="flex items-baseline gap-2 text-sm">
                <span>{colors.icon}</span>
                <span className={`${colors.text} font-medium truncate`}>{s.name}</span>
                <span className={`text-[10px] uppercase ${stateColor}`}>{s.state}</span>
                <span className="text-[10px] text-[#7a8599] ml-auto flex-shrink-0">
                  {formatDuration(s.started_at, s.completed_at)}
                </span>
              </div>
              {s.error && (
                <div className="text-[10px] text-[#f87171] mt-0.5 truncate">
                  {s.error}
                </div>
              )}
            </button>
            {isExpanded && hasDetail && (
              <pre className="text-[10px] text-[#7a8599] bg-[#111820] border border-[#1e2738] rounded p-2 mt-1 ml-5 overflow-x-auto">
                {JSON.stringify(s.detail, null, 2)}
              </pre>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// ── Timeline view (Gantt-style) ──────────────────────────────────────────────

function TimelineView({
  spans,
  startMs,
  endMs,
}: {
  spans: Array<TaskSpan & { depth: number }>;
  startMs: number;
  endMs: number;
}) {
  const totalMs = Math.max(1, endMs - startMs);

  return (
    <div className="space-y-1">
      {/* Time axis */}
      <div className="flex justify-between text-[9px] text-[#7a8599] pb-1 border-b border-[#1e2738] mb-2">
        <span>T+0</span>
        <span>T+{((totalMs / 2) / 1000).toFixed(1)}s</span>
        <span>T+{(totalMs / 1000).toFixed(1)}s</span>
      </div>
      {spans.map((s) => {
        const colors = SPAN_COLORS[s.span_type];
        const spanStart = Date.parse(s.started_at);
        const spanEnd = s.completed_at ? Date.parse(s.completed_at) : Date.now();
        const leftPct = Math.max(0, ((spanStart - startMs) / totalMs) * 100);
        const widthPct = Math.max(0.5, ((spanEnd - spanStart) / totalMs) * 100);

        return (
          <div key={s.id} className="flex items-center gap-2 text-xs">
            <span
              className="truncate flex-shrink-0 text-[10px]"
              style={{ minWidth: '140px', paddingLeft: `${s.depth * 8}px` }}
              title={s.name}
            >
              {colors.icon} <span className={colors.text}>{s.name}</span>
            </span>
            <div className="relative flex-1 h-4 bg-[#111820] rounded">
              <div
                className={`absolute top-0 bottom-0 rounded border ${colors.border} ${colors.bg} ${
                  s.state === 'running' ? 'animate-pulse' : ''
                }`}
                style={{
                  left: `${leftPct}%`,
                  width: `${widthPct}%`,
                }}
                title={`${s.name} · ${formatDuration(s.started_at, s.completed_at)} · ${s.state}`}
              />
            </div>
            <span className="text-[9px] text-[#7a8599] flex-shrink-0 w-12 text-right">
              {formatDuration(s.started_at, s.completed_at)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

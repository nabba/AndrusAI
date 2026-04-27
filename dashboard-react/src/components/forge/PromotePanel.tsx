import { usePromoteMutation, useDemoteMutation } from '../../api/forge';
import type { ToolStatus } from '../../types/forge';

const FORWARD: Record<ToolStatus, 'SHADOW' | 'CANARY' | 'ACTIVE' | null> = {
  DRAFT: null,
  // QUARANTINED → SHADOW is a manual human override (e.g. judge was down).
  QUARANTINED: 'SHADOW',
  SHADOW: 'CANARY',
  CANARY: 'ACTIVE',
  ACTIVE: null,
  DEPRECATED: null,
  KILLED: null,
};

const BACKWARD: Record<ToolStatus, 'SHADOW' | 'CANARY' | 'DEPRECATED' | null> = {
  DRAFT: null,
  QUARANTINED: null,
  SHADOW: null,
  CANARY: 'SHADOW',
  ACTIVE: 'DEPRECATED',
  DEPRECATED: null,
  KILLED: null,
};

interface Props {
  toolId: string;
  status: ToolStatus;
}

export function PromotePanel({ toolId, status }: Props) {
  const promoteMut = usePromoteMutation();
  const demoteMut = useDemoteMutation();
  const fwd = FORWARD[status];
  const back = BACKWARD[status];

  if (!fwd && !back) {
    return null;
  }

  return (
    <div className="p-4 rounded-lg border border-[#1e2738] bg-[#111820] space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[#e2e8f0]">Promotion</h3>
        <span className="text-[10px] text-[#7a8599] uppercase tracking-wider">
          current: {status}
        </span>
      </div>
      <p className="text-xs text-[#7a8599]">
        Forward transitions are gated by audits. The state machine forbids
        skipping stages — use the next-step button only when you've reviewed
        the audit findings and recent invocations.
      </p>
      <div className="flex gap-2">
        {fwd && (
          <button
            onClick={() => promoteMut.mutate({ id: toolId, target: fwd })}
            disabled={promoteMut.isPending}
            className="flex-1 px-3 py-1.5 rounded bg-[#34d399]/15 text-[#34d399] hover:bg-[#34d399]/25 border border-[#34d399]/30 text-xs font-medium transition-colors disabled:opacity-50"
          >
            {promoteMut.isPending ? 'promoting…' : `Promote → ${fwd}`}
          </button>
        )}
        {back && (
          <button
            onClick={() => demoteMut.mutate({ id: toolId, target: back })}
            disabled={demoteMut.isPending}
            className="flex-1 px-3 py-1.5 rounded bg-[#fbbf24]/15 text-[#fbbf24] hover:bg-[#fbbf24]/25 border border-[#fbbf24]/30 text-xs font-medium transition-colors disabled:opacity-50"
          >
            {demoteMut.isPending ? 'demoting…' : `Demote → ${back}`}
          </button>
        )}
      </div>
      {(promoteMut.isError || demoteMut.isError) && (
        <div className="text-xs text-[#f87171]">
          {String(promoteMut.error || demoteMut.error)}
        </div>
      )}
    </div>
  );
}

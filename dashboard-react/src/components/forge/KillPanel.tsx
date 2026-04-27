import { useState } from 'react';
import { useKillToolMutation, useRerunAuditMutation } from '../../api/forge';
import type { ToolStatus } from '../../types/forge';

interface Props {
  toolId: string;
  status: ToolStatus;
}

export function KillPanel({ toolId, status }: Props) {
  const [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState(false);
  const killMut = useKillToolMutation();
  const rerunMut = useRerunAuditMutation();

  const isKilled = status === 'KILLED';

  return (
    <div className="p-4 rounded-lg border border-[#f87171]/30 bg-[#f87171]/5 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[#f87171]">Danger zone</h3>
        {isKilled && (
          <span className="text-xs text-[#f87171] font-medium">
            already killed (sticky)
          </span>
        )}
      </div>
      <p className="text-xs text-[#7a8599]">
        Kill is irreversible — to recover, regenerate the tool through the
        full audit pipeline. This is by design so a UI compromise cannot
        un-kill a forged tool.
      </p>

      {!isKilled && (
        <>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason (optional, recorded in audit log)"
            className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-3 py-2 text-sm text-[#e2e8f0] placeholder-[#7a8599] focus:outline-none focus:border-[#f87171]"
          />
          {!confirming ? (
            <button
              onClick={() => setConfirming(true)}
              className="w-full px-4 py-2 rounded bg-[#f87171]/15 text-[#f87171] hover:bg-[#f87171]/25 border border-[#f87171]/30 text-sm font-medium transition-colors"
            >
              Kill this tool
            </button>
          ) : (
            <div className="flex gap-2">
              <button
                onClick={() => {
                  killMut.mutate({ id: toolId, reason });
                  setConfirming(false);
                }}
                disabled={killMut.isPending}
                className="flex-1 px-4 py-2 rounded bg-[#f87171] text-[#0a0e14] hover:bg-[#fca5a5] text-sm font-semibold transition-colors disabled:opacity-50"
              >
                {killMut.isPending ? 'killing...' : 'Confirm kill'}
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="px-4 py-2 rounded bg-[#1e2738] text-[#cbd5e1] hover:bg-[#2a3550] text-sm transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </>
      )}

      <div className="pt-3 border-t border-[#f87171]/20">
        <button
          onClick={() => rerunMut.mutate({ id: toolId })}
          disabled={rerunMut.isPending || isKilled}
          className="w-full px-4 py-2 rounded bg-[#1e2738] text-[#cbd5e1] hover:bg-[#2a3550] text-sm transition-colors disabled:opacity-50"
        >
          {rerunMut.isPending ? 're-running audit...' : 'Re-run static + semantic audits'}
        </button>
      </div>

      {killMut.isError && (
        <div className="text-xs text-[#f87171]">
          Kill failed: {String(killMut.error)}
        </div>
      )}
      {rerunMut.isSuccess && (
        <div className="text-xs text-[#34d399]">
          Audit re-run complete — new status: {rerunMut.data?.status}
        </div>
      )}
    </div>
  );
}

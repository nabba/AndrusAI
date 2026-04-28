import { useState } from 'react';
import { useOverrideReset } from '../../api/affect';

export function OverrideResetButton() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState('');
  const reset = useOverrideReset();

  function close() {
    setOpen(false);
    setToken('');
    reset.reset();
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-[11px] px-3 py-1.5 rounded font-mono text-[#f87171] bg-[#f871711a] border border-[#f87171]/30 hover:bg-[#f87171]/20 transition-colors"
        title="Factory-reset the soft envelope (setpoints + calibration). Hard envelope is unchanged."
      >
        ⚠ override reset
      </button>

      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="rounded-lg bg-[#111820] border border-[#f87171]/40 max-w-md w-full p-5 space-y-4">
            <div>
              <div className="text-base font-semibold text-[#f87171]">Override reset (panic button)</div>
              <p className="text-sm text-[#e2e8f0] mt-2">
                This factory-restores the <strong>soft envelope</strong>: deletes
                <code className="px-1 mx-1 bg-[#1e2738] rounded text-[#7a8599]">setpoints.json</code>
                and
                <code className="px-1 mx-1 bg-[#1e2738] rounded text-[#7a8599]">calibration.json</code>,
                resetting set-points and ratchet state to defaults.
              </p>
              <p className="text-sm text-[#7a8599] mt-2">
                The <strong>hard envelope</strong> (welfare bounds, attachment caps) is unchanged —
                it lives in code and is unaffected by this action.
              </p>
              <p className="text-sm text-[#7a8599] mt-2">
                Recorded in <code className="px-1 bg-[#1e2738] rounded">welfare_audit.jsonl</code> as
                <code className="px-1 mx-1 bg-[#1e2738] rounded">override_invoked</code>.
              </p>
            </div>

            <div>
              <label className="block text-[11px] text-[#7a8599] uppercase tracking-wider mb-1">
                Gateway override token
              </label>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="X-Override-Token"
                className="w-full px-2.5 py-1.5 rounded bg-[#0a0e14] border border-[#1e2738] text-sm text-[#e2e8f0] font-mono focus:outline-none focus:border-[#60a5fa]"
                autoFocus
              />
            </div>

            {reset.isError ? (
              <div className="text-[12px] text-[#f87171] font-mono break-all">
                {(reset.error as Error)?.message ?? 'Reset failed.'}
              </div>
            ) : null}

            {reset.data ? (
              <div className="text-[12px] text-[#34d399] font-mono">
                Reset complete. Deleted: {reset.data.deleted.join(', ') || '(nothing)'}.
              </div>
            ) : null}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={close}
                className="text-sm px-3 py-1.5 rounded text-[#7a8599] hover:text-[#e2e8f0] hover:bg-[#1e2738] transition-colors"
              >
                {reset.data ? 'Close' : 'Cancel'}
              </button>
              {!reset.data ? (
                <button
                  type="button"
                  disabled={!token || reset.isPending}
                  onClick={() => reset.mutate({ overrideToken: token })}
                  className="text-sm px-3 py-1.5 rounded font-mono text-[#f87171] bg-[#f871711a] border border-[#f87171]/30 hover:bg-[#f87171]/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {reset.isPending ? 'Resetting…' : 'Confirm reset'}
                </button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

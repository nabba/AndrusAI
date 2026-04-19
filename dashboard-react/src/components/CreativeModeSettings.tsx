import { useState } from 'react';
import { ErrorPanel } from './ui/ErrorPanel';
import { useCreativeModeQuery, useUpdateCreativeMode, type CreativeSettings } from '../api/queries';

// Note: POST to /config/creative_mode requires a gateway bearer secret. The
// dashboard server (server.mjs) injects `Authorization: Bearer $GATEWAY_SECRET`
// on outbound requests when the env var is set; the Vite dev server does the
// same. Without that, the save button will return 401.

export default function CreativeModeSettings() {
  const settingsQ = useCreativeModeQuery();

  if (settingsQ.isLoading) {
    return (
      <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
        <div className="text-[#7a8599] text-sm">Loading creative-mode settings…</div>
      </div>
    );
  }
  if (settingsQ.error) return <ErrorPanel error={settingsQ.error} onRetry={settingsQ.refetch} />;
  if (!settingsQ.data) return null;

  // Remount the form when server data changes (key on object identity).
  return <CreativeModeForm key={settingsQ.data.mem0_weight + '|' + settingsQ.data.creative_run_budget_usd} initial={settingsQ.data} />;
}

function CreativeModeForm({ initial }: { initial: CreativeSettings }) {
  const update = useUpdateCreativeMode();
  const [budgetInput, setBudgetInput] = useState(() => initial.creative_run_budget_usd.toFixed(2));
  const [weightInput, setWeightInput] = useState(() => initial.originality_wiki_weight.toFixed(2));
  const [localError, setLocalError] = useState('');
  const [success, setSuccess] = useState('');

  const save = async () => {
    setLocalError('');
    setSuccess('');
    const budget = parseFloat(budgetInput);
    const weight = parseFloat(weightInput);
    if (isNaN(budget) || budget < 0) {
      setLocalError('Budget must be ≥ 0');
      return;
    }
    if (isNaN(weight) || weight < 0 || weight > 1) {
      setLocalError('Weight must be in [0, 1]');
      return;
    }
    try {
      await update.mutateAsync({
        creative_run_budget_usd: budget,
        originality_wiki_weight: weight,
      });
      setSuccess('Saved.');
      setTimeout(() => setSuccess(''), 2500);
    } catch {
      // surfaced via update.error
    }
  };

  const error = localError || (update.error instanceof Error ? update.error.message : '');
  const wiki = parseFloat(weightInput || '0');
  const mem0 = 1 - wiki;

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-[#e2e8f0]">Creative Mode</h2>
        <p className="text-xs text-[#7a8599] mt-1">
          Controls the multi-agent divergent-discussion-convergence pipeline.
        </p>
      </div>

      <label className="block">
        <span className="text-sm text-[#e2e8f0]">Per-run budget cap (USD)</span>
        <input
          type="number"
          step="0.01"
          min="0"
          value={budgetInput}
          onChange={(e) => setBudgetInput(e.target.value)}
          className="mt-1 w-full bg-[#0a0f18] border border-[#1e2738] rounded px-3 py-2 text-[#e2e8f0] text-sm"
        />
        <span className="text-xs text-[#7a8599] mt-1 block">
          Hard cap. Runs abort mid-phase when exceeded, returning best output so far.
        </span>
      </label>

      <label className="block">
        <span className="text-sm text-[#e2e8f0]">
          Originality: wiki weight ({(wiki * 100).toFixed(0)}% wiki / {(mem0 * 100).toFixed(0)}% Mem0)
        </span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={weightInput}
          onChange={(e) => setWeightInput(e.target.value)}
          className="mt-2 w-full"
        />
      </label>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={update.isPending}
          className="px-4 py-2 bg-[#2563eb] hover:bg-[#1d4ed8] disabled:opacity-50 rounded text-white text-sm"
        >
          {update.isPending ? 'Saving…' : 'Save'}
        </button>
        {error && <span className="text-[#f87171] text-sm">{error}</span>}
        {success && <span className="text-[#34d399] text-sm">{success}</span>}
      </div>
    </div>
  );
}

import { useEffect, useState } from 'react';
import { api } from '../api/client';

type CreativeSettings = {
  creative_run_budget_usd: number;
  originality_wiki_weight: number;
  mem0_weight: number;
};

export default function CreativeModeSettings() {
  const [settings, setSettings] = useState<CreativeSettings | null>(null);
  const [budgetInput, setBudgetInput] = useState('');
  const [weightInput, setWeightInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const load = async () => {
    try {
      const s = await api<CreativeSettings>('/creative_mode');
      setSettings(s);
      setBudgetInput(s.creative_run_budget_usd.toFixed(2));
      setWeightInput(s.originality_wiki_weight.toFixed(2));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const save = async () => {
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      const budget = parseFloat(budgetInput);
      const weight = parseFloat(weightInput);
      if (isNaN(budget) || budget < 0) throw new Error('Budget must be ≥ 0');
      if (isNaN(weight) || weight < 0 || weight > 1) throw new Error('Weight must be in [0, 1]');
      const updated = await api<CreativeSettings & { status: string }>('/creative_mode', {
        method: 'POST',
        body: JSON.stringify({
          creative_run_budget_usd: budget,
          originality_wiki_weight: weight,
        }),
      });
      setSettings(updated);
      setSuccess('Saved.');
      setTimeout(() => setSuccess(''), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  if (!settings) {
    return (
      <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
        <div className="text-[#7a8599] text-sm">Loading creative-mode settings…</div>
      </div>
    );
  }

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
          Originality: wiki weight ({(parseFloat(weightInput || '0') * 100).toFixed(0)}% wiki /{' '}
          {((1 - parseFloat(weightInput || '0')) * 100).toFixed(0)}% Mem0)
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
          disabled={saving}
          className="px-4 py-2 bg-[#2563eb] hover:bg-[#1d4ed8] disabled:opacity-50 rounded text-white text-sm"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {error && <span className="text-red-400 text-sm">{error}</span>}
        {success && <span className="text-green-400 text-sm">{success}</span>}
      </div>
    </div>
  );
}

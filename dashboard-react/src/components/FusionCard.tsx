import { useState } from 'react';
import {
  useFusionStateQuery,
  useUpdateRuntimeSettings,
  type RuntimeSettings,
} from '../api/queries';

// OpenRouter Fusion control. Master toggle + per-role scope + the resolved
// panel (what the LLM chooser picked per vendor class) + judge/caps. Default
// OFF; even ON, nothing fuses until the operator selects roles.
export function FusionCard({
  settings,
  onSettingsChange,
}: {
  settings: RuntimeSettings;
  onSettingsChange: () => void;
}) {
  const fusionQ = useFusionStateQuery();
  const update = useUpdateRuntimeSettings();

  const enabled = settings.fusion_enabled ?? false;
  const scope = settings.fusion_scope_roles ?? [];

  const [classes, setClasses] = useState(
    (settings.fusion_panel_classes ?? []).join(', '),
  );
  const [judge, setJudge] = useState(settings.fusion_judge_id ?? '');
  const [maxPanel, setMaxPanel] = useState<number>(settings.fusion_max_panel ?? 4);
  const [cap, setCap] = useState<number>(settings.fusion_daily_cap_usd ?? 10);
  const [msg, setMsg] = useState('');

  const state = fusionQ.data;
  const availableRoles = state?.available_roles ?? [];
  const error = update.error instanceof Error ? update.error.message : '';

  async function save(patch: Partial<RuntimeSettings>, note = 'Saved.') {
    setMsg('');
    try {
      await update.mutateAsync(patch);
      onSettingsChange();
      fusionQ.refetch();
      setMsg(note);
      setTimeout(() => setMsg(''), 2500);
    } catch {
      /* surfaced via update.error */
    }
  }

  function toggleRole(role: string) {
    const next = scope.includes(role)
      ? scope.filter((r) => r !== role)
      : [...scope, role];
    save({ fusion_scope_roles: next });
  }

  function savePanelConfig() {
    const classList = classes
      .split(',')
      .map((c) => c.trim().toLowerCase())
      .filter(Boolean);
    save({
      fusion_panel_classes: classList,
      fusion_judge_id: judge.trim(),
      fusion_max_panel: Math.max(1, Math.min(8, Number(maxPanel) || 4)),
      fusion_daily_cap_usd: Math.max(0, Number(cap) || 0),
    });
  }

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[#e2e8f0]">
            🔀 Fusion (multi-model deliberation)
          </h2>
          <p className="text-xs text-[#7a8599] mt-1">
            Routes scoped completions through OpenRouter Fusion — a panel of
            diverse models deliberates and a judge synthesises the answer.
            ~{state?.cost_multiplier ?? 5}× single-call cost. Off by default;
            nothing fuses until you select roles below.
          </p>
        </div>
        <label className="flex items-center gap-2 cursor-pointer shrink-0">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => save({ fusion_enabled: e.target.checked })}
            className="w-4 h-4 accent-[#60a5fa]"
          />
          <span className="text-sm text-[#e2e8f0]">{enabled ? 'On' : 'Off'}</span>
        </label>
      </div>

      {enabled && (
        <>
          {/* Agent-path opt-in (advanced) */}
          <label className="flex items-center gap-2 cursor-pointer text-xs text-[#7a8599]">
            <input
              type="checkbox"
              checked={settings.fusion_agent_path_enabled ?? false}
              onChange={(e) => save({ fusion_agent_path_enabled: e.target.checked })}
              className="w-4 h-4 accent-[#60a5fa]"
            />
            Also fuse CrewAI agent calls — offered-not-forced, unmetered (advanced)
          </label>

          {/* Scope roles */}
          <div>
            <div className="text-xs font-medium text-[#7a8599] uppercase tracking-wide mb-2">
              Fused roles {scope.length === 0 && '— none selected (idle)'}
            </div>
            <div className="flex flex-wrap gap-2">
              {availableRoles.map((role) => (
                <label
                  key={role}
                  className={`px-2 py-1 rounded text-xs cursor-pointer border transition-colors ${
                    scope.includes(role)
                      ? 'bg-[#60a5fa]/15 border-[#60a5fa]/40 text-[#60a5fa]'
                      : 'bg-[#1e2738] border-[#1e2738] text-[#7a8599] hover:text-[#e2e8f0]'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={scope.includes(role)}
                    onChange={() => toggleRole(role)}
                    className="hidden"
                  />
                  {role}
                </label>
              ))}
              {availableRoles.length === 0 && (
                <span className="text-xs text-[#7a8599]">
                  Role list loads from the live catalog…
                </span>
              )}
            </div>
          </div>

          {/* Resolved panel preview — the "LLM chooser" output */}
          <div className="bg-[#0c1219] border border-[#1e2738] rounded-lg p-3">
            <div className="text-xs font-medium text-[#7a8599] uppercase tracking-wide mb-2">
              Resolved panel — current OpenRouter champion per class
            </div>
            <div className="space-y-1">
              {(state?.panel ?? []).map((slot) => (
                <div
                  key={slot.class}
                  className="flex items-center justify-between gap-3 text-xs"
                >
                  <span className="text-[#e2e8f0]">{slot.class}</span>
                  <span
                    className={`text-right ${
                      slot.model_id ? 'text-[#34d399]' : 'text-[#f87171]'
                    }`}
                  >
                    {slot.model_id ?? '(no model in catalog yet)'}
                    {slot.pinned && ' 📌'}
                  </span>
                </div>
              ))}
              {(state?.panel ?? []).length === 0 && (
                <span className="text-xs text-[#7a8599]">
                  No classes configured.
                </span>
              )}
            </div>
            <div className="mt-2 pt-2 border-t border-[#1e2738] text-xs text-[#7a8599] space-y-0.5">
              <div>
                Judge: <span className="text-[#e2e8f0]">{state?.judge}</span>
              </div>
              <div>
                Cost ~{state?.cost_multiplier ?? '?'}× · today $
                {(state?.spent_today_usd ?? 0).toFixed(3)} / $
                {(state?.daily_cap_usd ?? 0).toFixed(2)} cap
                {state?.brake_engaged && (
                  <span className="text-[#fbbf24]"> · budget brake engaged</span>
                )}
              </div>
            </div>
          </div>

          {/* Panel configuration */}
          <div className="space-y-2">
            <label className="block text-xs text-[#7a8599]">
              Panel classes (vendor families, comma-separated)
              <input
                value={classes}
                onChange={(e) => setClasses(e.target.value)}
                placeholder="google, qwen, moonshotai, deepseek"
                className="mt-1 w-full bg-[#0c1219] border border-[#1e2738] rounded px-2 py-1 text-sm text-[#e2e8f0]"
              />
            </label>
            <label className="block text-xs text-[#7a8599]">
              Judge model id (blank = OpenRouter default)
              <input
                value={judge}
                onChange={(e) => setJudge(e.target.value)}
                placeholder="(OpenRouter default — Claude Opus class)"
                className="mt-1 w-full bg-[#0c1219] border border-[#1e2738] rounded px-2 py-1 text-sm text-[#e2e8f0]"
              />
            </label>
            <div className="flex gap-3">
              <label className="block text-xs text-[#7a8599] flex-1">
                Max panel (1–8)
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={maxPanel}
                  onChange={(e) => setMaxPanel(Number(e.target.value))}
                  className="mt-1 w-full bg-[#0c1219] border border-[#1e2738] rounded px-2 py-1 text-sm text-[#e2e8f0]"
                />
              </label>
              <label className="block text-xs text-[#7a8599] flex-1">
                Daily cap (USD)
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={cap}
                  onChange={(e) => setCap(Number(e.target.value))}
                  className="mt-1 w-full bg-[#0c1219] border border-[#1e2738] rounded px-2 py-1 text-sm text-[#e2e8f0]"
                />
              </label>
            </div>
            <button
              onClick={savePanelConfig}
              disabled={update.isPending}
              className="px-4 py-2 bg-[#2563eb] hover:bg-[#1d4ed8] disabled:opacity-50 rounded text-white text-sm"
            >
              {update.isPending ? 'Saving…' : 'Save panel config'}
            </button>
          </div>
        </>
      )}

      {error && <div className="text-[#f87171] text-sm">{error}</div>}
      {msg && <div className="text-[#34d399] text-sm">{msg}</div>}
    </div>
  );
}

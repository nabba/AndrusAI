import { useMemo, useState } from 'react';
import {
  useLlmJudgesQuery,
  useLlmJudgeEvaluationsQuery,
  useLlmCatalogQuery,
  usePinJudge,
  useUnpinJudge,
  type JudgeRotationEntry,
  type LlmModel,
} from '../api/queries';

// ── Cross-eval judges panel ───────────────────────────────────────────────────
//
// Surfaces three things the discovery / re-benchmark subsystem uses
// behind the scenes:
//
//   1. The current 3-judge rotation (one model per provider family).
//   2. Manual pins that override the dynamic top-intelligence pick.
//   3. Recent multi-judge scoring panels with inter-rater std-dev,
//      so the user can see when judges disagree + when the OpenRouter
//      fallback fired (direct API was out of credits).
//
// All read-only except the per-family pin/unpin controls.

const FAMILY_COLORS: Record<string, string> = {
  anthropic: 'bg-[#fb923c]/10 text-[#fb923c] border-[#fb923c]/30',
  openai:    'bg-[#22d3ee]/10 text-[#22d3ee] border-[#22d3ee]/30',
  google:    'bg-[#60a5fa]/10 text-[#60a5fa] border-[#60a5fa]/30',
  deepseek:  'bg-[#a78bfa]/10 text-[#a78bfa] border-[#a78bfa]/30',
  meta:      'bg-[#34d399]/10 text-[#34d399] border-[#34d399]/30',
  alibaba:   'bg-[#fbbf24]/10 text-[#fbbf24] border-[#fbbf24]/30',
  mistral:   'bg-[#f472b6]/10 text-[#f472b6] border-[#f472b6]/30',
  unknown:   'bg-[#7a8599]/10 text-[#7a8599] border-[#7a8599]/30',
};

function familyColor(family: string): string {
  return FAMILY_COLORS[family] ?? FAMILY_COLORS.unknown;
}

function formatScore(s: number | null | undefined): string {
  if (s == null) return '—';
  return s.toFixed(3);
}

function formatStdDev(s: number | null | undefined): string {
  if (s == null) return '—';
  return s.toFixed(3);
}

function formatRelTime(iso: string): string {
  const t = Date.parse(iso);
  if (isNaN(t)) return iso;
  const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function JudgesPanel() {
  const judgesQ = useLlmJudgesQuery();
  const evalsQ = useLlmJudgeEvaluationsQuery(20);
  const catalogQ = useLlmCatalogQuery();
  const pin = usePinJudge();
  const unpin = useUnpinJudge();

  // Pin dialog: open with a specific family preselected; user picks a
  // model from the catalog of tool-supporting entries.
  const [pinDialog, setPinDialog] = useState<
    | null
    | { provider_family: string; model: string; reason: string }
  >(null);

  const rotation = judgesQ.data?.rotation ?? [];
  const agreement = judgesQ.data?.agreement;
  const evals = evalsQ.data?.evaluations ?? [];

  // Eligible-for-pin models: any tool-supporting entry, ordered by
  // reasoning strength descending so the dialog surfaces strongest
  // first. Falls back to any catalog entry when supports_tools is
  // missing.
  const eligibleModels = useMemo<LlmModel[]>(() => {
    const all = catalogQ.data?.models ?? [];
    return all
      .filter((m) => (m as Record<string, unknown>).supports_tools !== false)
      .sort((a, b) => {
        const av = (a.strengths?.reasoning as number | undefined) ?? 0;
        const bv = (b.strengths?.reasoning as number | undefined) ?? 0;
        return bv - av;
      });
  }, [catalogQ.data?.models]);

  return (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-xs font-medium text-[#7a8599] uppercase tracking-wider">
            Cross-Eval Judges
          </h3>
          <p className="text-[11px] text-[#7a8599] mt-0.5">
            One judge per provider family. Used by discovery + re-benchmarking
            to score new candidates without same-lab bias.
          </p>
        </div>
        {agreement && (
          <div className="flex gap-3 text-[10px]">
            <span className="text-[#7a8599]">
              24h: <span className="text-[#e2e8f0]">{agreement.evaluations}</span> eval{agreement.evaluations === 1 ? '' : 's'}
            </span>
            <span className="text-[#7a8599]">
              σ̄: <span className="text-[#e2e8f0]">{formatStdDev(agreement.mean_std_dev)}</span>
            </span>
            <span className={agreement.high_disagreement > 0 ? 'text-[#fbbf24]' : 'text-[#7a8599]'}>
              high-disagreement: {agreement.high_disagreement}
            </span>
            <span className={agreement.fallback_fired > 0 ? 'text-[#22d3ee]' : 'text-[#7a8599]'}>
              OR fallback: {agreement.fallback_fired}
            </span>
          </div>
        )}
      </div>

      {judgesQ.isLoading && (
        <p className="text-xs text-[#7a8599] italic">Loading judges…</p>
      )}
      {judgesQ.error && (
        <p className="text-xs text-[#f87171]">
          Failed to load judges: {String((judgesQ.error as Error).message)}
        </p>
      )}

      {/* Rotation cards */}
      {rotation.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {rotation.map((j: JudgeRotationEntry) => (
            <div
              key={j.provider_family}
              className={`bg-[#0a0e14] border rounded-lg p-3 ${
                j.pinned ? 'border-[#fbbf24]/50 ring-1 ring-[#fbbf24]/20' : 'border-[#1e2738]'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wider font-medium ${familyColor(j.provider_family)}`}
                >
                  {j.provider_family}
                </span>
                {j.pinned && <span title="Hand-pinned">📌</span>}
              </div>
              <div className="text-sm text-[#e2e8f0] truncate" title={j.catalog_key}>
                {j.catalog_key}
              </div>
              <div className="text-[10px] text-[#7a8599] mt-1 flex justify-between">
                <span>tier: {j.tier ?? '—'}</span>
                <span>reasoning: {formatScore(j.reasoning_score)}</span>
              </div>
              <div className="flex gap-2 mt-2 pt-2 border-t border-[#1e2738]">
                {j.pinned ? (
                  <button
                    onClick={() => unpin.mutate({ provider_family: j.provider_family })}
                    disabled={unpin.isPending}
                    className="text-[10px] px-2 py-1 rounded bg-[#0a0e14] border border-[#1e2738] text-[#f87171] hover:border-[#f87171]/40 disabled:opacity-50"
                  >
                    {unpin.isPending ? 'unpinning…' : '✕ unpin'}
                  </button>
                ) : (
                  <button
                    onClick={() =>
                      setPinDialog({
                        provider_family: j.provider_family,
                        model: j.catalog_key,
                        reason: '',
                      })
                    }
                    className="text-[10px] px-2 py-1 rounded bg-[#0a0e14] border border-[#1e2738] text-[#7a8599] hover:border-[#fbbf24]/40 hover:text-[#fbbf24]"
                  >
                    📌 pin different model
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {rotation.length === 0 && !judgesQ.isLoading && (
        <p className="text-sm text-[#7a8599] italic bg-[#111820] border border-[#1e2738] rounded-lg p-4 text-center">
          No judges available. Run a catalog refresh so the rotation can pick from the live pool.
        </p>
      )}

      {/* Recent evaluations */}
      <div>
        <h4 className="text-[10px] font-medium text-[#7a8599] uppercase tracking-wider mb-2">
          Recent Evaluations ({evals.length})
        </h4>
        {evalsQ.isLoading && (
          <p className="text-xs text-[#7a8599] italic">Loading…</p>
        )}
        {evals.length === 0 && !evalsQ.isLoading ? (
          <p className="text-[11px] text-[#7a8599] italic">
            No multi-judge scoring panels recorded yet. They appear after the next discovery / re-benchmark run.
          </p>
        ) : (
          <div className="bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#1e2738]">
                  <th className="text-left px-3 py-2 text-[10px] font-medium text-[#7a8599] uppercase">When</th>
                  <th className="text-left px-3 py-2 text-[10px] font-medium text-[#7a8599] uppercase">Candidate</th>
                  <th className="text-left px-3 py-2 text-[10px] font-medium text-[#7a8599] uppercase">Judges</th>
                  <th className="text-right px-3 py-2 text-[10px] font-medium text-[#7a8599] uppercase">Mean</th>
                  <th className="text-right px-3 py-2 text-[10px] font-medium text-[#7a8599] uppercase">σ</th>
                  <th className="text-left px-3 py-2 text-[10px] font-medium text-[#7a8599] uppercase">Fallback</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e2738]">
                {evals.map((e) => {
                  const fallbackHits = (e.used_fallback || []).filter(Boolean).length;
                  const sigmaTone =
                    (e.std_dev ?? 0) > 0.2 ? 'text-[#fbbf24]'
                    : (e.std_dev ?? 0) > 0.1 ? 'text-[#22d3ee]'
                    : 'text-[#7a8599]';
                  return (
                    <tr key={e.id} className="hover:bg-[#1e2738]/40">
                      <td className="px-3 py-1.5 text-[#7a8599] whitespace-nowrap">
                        {formatRelTime(e.created_at)}
                      </td>
                      <td className="px-3 py-1.5 text-[#e2e8f0] truncate max-w-[16rem]" title={e.candidate_model}>
                        {e.candidate_model}
                      </td>
                      <td className="px-3 py-1.5 text-[10px] text-[#a78bfa]">
                        {e.judges.map((j, i) => (
                          <span key={i} className="mr-2">
                            {j}
                            <span className="text-[#7a8599]">: {formatScore(e.scores[i])}</span>
                          </span>
                        ))}
                      </td>
                      <td className="px-3 py-1.5 text-right text-[#34d399] font-mono">
                        {formatScore(e.mean_score)}
                      </td>
                      <td className={`px-3 py-1.5 text-right font-mono ${sigmaTone}`}>
                        {formatStdDev(e.std_dev)}
                      </td>
                      <td className="px-3 py-1.5 text-[10px]">
                        {fallbackHits > 0 ? (
                          <span className="text-[#22d3ee]" title="Direct API failed; OpenRouter served the call">
                            ⤳ OR×{fallbackHits}
                          </span>
                        ) : (
                          <span className="text-[#7a8599]">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pin dialog */}
      {pinDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setPinDialog(null)}
        >
          <div
            className="bg-[#0a0e14] border border-[#1e2738] rounded-lg p-4 w-full max-w-md space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <h4 className="text-sm font-medium text-[#e2e8f0]">
              Pin judge for{' '}
              <span className={`text-xs px-1.5 py-0.5 rounded border uppercase font-medium ${familyColor(pinDialog.provider_family)}`}>
                {pinDialog.provider_family}
              </span>
            </h4>
            <p className="text-[11px] text-[#7a8599]">
              Override the dynamic top-intelligence pick for this provider family.
              Discovery + re-benchmarking will use the pinned model from now on.
            </p>

            <label className="block text-[10px] uppercase tracking-wider text-[#7a8599]">Model</label>
            <select
              value={pinDialog.model}
              onChange={(e) => setPinDialog({ ...pinDialog, model: e.target.value })}
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1.5 text-sm text-[#e2e8f0]"
            >
              {eligibleModels.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name} (reasoning: {formatScore(m.strengths?.reasoning as number | undefined)})
                </option>
              ))}
            </select>

            <label className="block text-[10px] uppercase tracking-wider text-[#7a8599]">Reason (optional)</label>
            <input
              value={pinDialog.reason}
              onChange={(e) => setPinDialog({ ...pinDialog, reason: e.target.value })}
              placeholder="why this pin?"
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1.5 text-sm text-[#e2e8f0]"
            />

            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setPinDialog(null)}
                className="px-3 py-1.5 text-sm text-[#7a8599] hover:text-[#e2e8f0]"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  pin.mutate({
                    provider_family: pinDialog.provider_family,
                    model: pinDialog.model,
                    reason: pinDialog.reason || undefined,
                  });
                  setPinDialog(null);
                }}
                disabled={pin.isPending}
                className="px-3 py-1.5 text-sm bg-[#fbbf24]/20 border border-[#fbbf24]/40 text-[#fbbf24] rounded hover:bg-[#fbbf24]/30 disabled:opacity-50"
              >
                {pin.isPending ? 'Pinning…' : '📌 Pin'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

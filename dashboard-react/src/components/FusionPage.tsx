import { useFusionDeliberationsQuery, useFusionStateQuery } from '../api/queries';
import { ErrorPanel } from './ui/ErrorPanel';

// /cp/fusion — live state summary + the judge's recorded deliberations.
export function FusionPage() {
  const stateQ = useFusionStateQuery();
  const delibQ = useFusionDeliberationsQuery(50);
  const state = stateQ.data;
  const rows = delibQ.data?.deliberations ?? [];

  const statusLabel = !state?.enabled
    ? 'Off'
    : state.active
      ? 'Active'
      : 'On (idle — no roles or unresolved panel)';

  return (
    <div className="space-y-4 max-w-4xl">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">🔀 Fusion</h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Multi-model deliberations. Status: <strong>{statusLabel}</strong>. Configure
          in <a href="/cp/settings" className="text-[#60a5fa]">Settings</a>.
        </p>
      </div>

      {state && (
        <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4 text-sm space-y-2">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[#7a8599]">
            <span>Enabled: <span className="text-[#e2e8f0]">{String(state.enabled)}</span></span>
            <span>Roles: <span className="text-[#e2e8f0]">{state.scope_roles.join(', ') || '—'}</span></span>
            <span>Judge: <span className="text-[#e2e8f0]">{state.judge}</span></span>
            <span>
              ~{state.cost_multiplier}× · ${(state.spent_today_usd ?? 0).toFixed(3)} / $
              {(state.daily_cap_usd ?? 0).toFixed(2)} today
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {state.panel.map((p) => (
              <span
                key={p.class}
                className={`px-2 py-1 rounded text-xs border ${
                  p.model_id
                    ? 'bg-[#34d399]/10 border-[#34d399]/30 text-[#34d399]'
                    : 'bg-[#f87171]/10 border-[#f87171]/30 text-[#f87171]'
                }`}
              >
                {p.class}: {p.model_id ?? 'unresolved'}
                {p.pinned ? ' 📌' : ''}
              </span>
            ))}
          </div>
        </div>
      )}

      <div>
        <h2 className="text-sm font-medium text-[#7a8599] uppercase tracking-wider mb-2">
          Recent deliberations
        </h2>
        {delibQ.isLoading ? (
          <div className="text-[#7a8599] text-sm">Loading…</div>
        ) : delibQ.error ? (
          <ErrorPanel error={delibQ.error} onRetry={delibQ.refetch} />
        ) : rows.length === 0 ? (
          <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4 text-sm text-[#7a8599]">
            No deliberations recorded yet — they appear here once a fused completion runs.
          </div>
        ) : (
          <div className="space-y-3">
            {rows.map((d, i) => (
              <div key={i} className="bg-[#111820] border border-[#1e2738] rounded-xl p-4 space-y-2">
                <div className="flex items-center justify-between text-xs text-[#7a8599]">
                  <span>
                    {d.role} · panel of {d.panel.length} → {d.judge}
                  </span>
                  <span>{d.ts}</span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {d.panel.map((m) => (
                    <span key={m} className="px-1.5 py-0.5 rounded text-[10px] bg-[#1e2738] text-[#7a8599]">
                      {m}
                    </span>
                  ))}
                </div>
                {d.answer_preview && (
                  <p className="text-sm text-[#e2e8f0] whitespace-pre-wrap">{d.answer_preview}</p>
                )}
                {(d.deliberation || d.router || d.message_annotations || d.annotations) ? (
                  <details className="text-xs">
                    <summary className="text-[#60a5fa] cursor-pointer">Judge analysis (raw)</summary>
                    <pre className="mt-1 p-2 bg-[#0c1219] border border-[#1e2738] rounded overflow-x-auto text-[#7a8599]">
                      {JSON.stringify(
                        d.deliberation ?? d.router ?? d.message_annotations ?? d.annotations,
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

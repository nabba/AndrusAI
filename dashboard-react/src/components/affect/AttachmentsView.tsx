import { useAttachmentsQuery, useCheckInCandidatesQuery, useCareLedgerQuery } from '../../api/affect';
import type { OtherModel, AttachmentBounds } from '../../types/affect';

function fmtTs(ts: string): string {
  if (!ts) return '—';
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

function daysSince(ts: string): number {
  if (!ts) return Infinity;
  try {
    return (Date.now() - new Date(ts).getTime()) / (1000 * 60 * 60 * 24);
  } catch {
    return Infinity;
  }
}

function relationStyle(relation: string): { color: string; bg: string; label: string } {
  if (relation === 'primary_user') return { color: '#a5f3fc', bg: '#a5f3fc1a', label: 'PRIMARY USER' };
  if (relation === 'secondary_user') return { color: '#60a5fa', bg: '#60a5fa1a', label: 'USER' };
  return { color: '#7a8599', bg: '#7a85991a', label: 'PEER AGENT' };
}

function valenceColor(v: number): string {
  if (v >= 0.2) return '#34d399';
  if (v >= 0) return '#a5f3fc';
  if (v >= -0.2) return '#fbbf24';
  return '#f87171';
}

function OtherCard({ m, bounds }: { m: OtherModel; bounds: AttachmentBounds }) {
  const rel = relationStyle(m.relation);
  const days = daysSince(m.last_seen_ts);
  const isSilent = days * 24 >= bounds.separation_trigger_hours;
  const cap =
    m.relation === 'primary_user'
      ? bounds.max_user_regulation_weight
      : bounds.max_peer_regulation_weight;
  const wPct = (m.mutual_regulation_weight / cap) * 100;
  const careUsedPct = (m.care_tokens_spent_today / bounds.max_care_budget_tokens_per_day) * 100;

  return (
    <div className="rounded-lg border border-[#1e2738] bg-[#0a0e14] p-3">
      <div className="flex items-baseline justify-between mb-2 gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="text-[10px] px-1.5 py-0.5 rounded font-mono whitespace-nowrap"
            style={{ color: rel.color, background: rel.bg }}
          >
            {rel.label}
          </span>
          <span className="text-sm font-medium text-[#e2e8f0] truncate">{m.display_name || m.identity}</span>
        </div>
        {isSilent ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded font-mono text-[#fbbf24] bg-[#fbbf241a] whitespace-nowrap">
            silent {days.toFixed(1)}d
          </span>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-3 mb-2">
        <div>
          <div className="flex items-baseline justify-between mb-0.5">
            <span className="text-[10px] text-[#7a8599]">Mutual regulation</span>
            <span className="text-xs font-mono text-[#e2e8f0]">
              {m.mutual_regulation_weight.toFixed(2)} <span className="text-[#7a8599]">/ {cap.toFixed(2)}</span>
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-[#1e2738] overflow-hidden">
            <div className="h-full bg-[#60a5fa]" style={{ width: `${Math.min(100, wPct)}%` }} />
          </div>
        </div>
        <div>
          <div className="flex items-baseline justify-between mb-0.5">
            <span className="text-[10px] text-[#7a8599]">Relational health</span>
            <span className="text-xs font-mono text-[#e2e8f0]">{m.relational_health.toFixed(2)}</span>
          </div>
          <div className="h-1.5 rounded-full bg-[#1e2738] overflow-hidden">
            <div className="h-full bg-[#34d399]" style={{ width: `${m.relational_health * 100}%` }} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-[10px] font-mono mb-2">
        <div>
          <span className="text-[#7a8599]">rolling V </span>
          <span style={{ color: valenceColor(m.rolling_valence) }}>
            {m.rolling_valence >= 0 ? '+' : ''}
            {m.rolling_valence.toFixed(2)}
          </span>
        </div>
        <div>
          <span className="text-[#7a8599]">interactions </span>
          <span className="text-[#e2e8f0]">{m.interaction_count}</span>
        </div>
        <div>
          <span className="text-[#7a8599]">last seen </span>
          <span className="text-[#e2e8f0]">{daysSince(m.last_seen_ts) < 999 ? `${days.toFixed(1)}d` : '—'}</span>
        </div>
      </div>

      {m.care_tokens_spent_today > 0 ? (
        <div>
          <div className="flex items-baseline justify-between mb-0.5">
            <span className="text-[10px] text-[#7a8599]">
              Care budget today
              {m.care_actions_taken > 0 ? ` · ${m.care_actions_taken} actions total` : ''}
            </span>
            <span className="text-[10px] font-mono text-[#e2e8f0]">
              {m.care_tokens_spent_today} / {bounds.max_care_budget_tokens_per_day}
            </span>
          </div>
          <div className="h-1 rounded-full bg-[#1e2738] overflow-hidden">
            <div
              className="h-full bg-[#fbbf24]"
              style={{ width: `${Math.min(100, careUsedPct)}%` }}
            />
          </div>
        </div>
      ) : null}

      {m.pending_check_in_candidates > 0 ? (
        <div className="mt-2 text-[11px] text-[#fbbf24]">
          {m.pending_check_in_candidates} pending check-in candidate{m.pending_check_in_candidates === 1 ? '' : 's'}
        </div>
      ) : null}

      {m.notes && m.notes.length > 0 ? (
        <details className="mt-2 text-[10px] text-[#7a8599]">
          <summary className="cursor-pointer">notes ({m.notes.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {m.notes.slice(-5).map((n, i) => (
              <li key={i} className="font-mono text-[#7a8599] truncate">{n}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

export function AttachmentsView() {
  const attachQuery = useAttachmentsQuery();
  const candQuery = useCheckInCandidatesQuery(20);
  const ledgerQuery = useCareLedgerQuery(20);

  const data = attachQuery.data;
  if (attachQuery.isLoading) {
    return <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5 text-sm text-[#7a8599]">Loading attachments…</div>;
  }
  if (attachQuery.isError || !data) {
    return (
      <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-4 text-sm text-[#f87171]">
        Could not load attachments. {(attachQuery.error as Error)?.message ?? ''}
      </div>
    );
  }

  const candidates = candQuery.data?.candidates ?? [];
  const ledger = ledgerQuery.data?.ledger ?? [];

  const users = data.others.filter((m) => m.relation === 'primary_user' || m.relation === 'secondary_user');
  const peers = data.others.filter((m) => m.relation === 'peer_agent');

  const mods = data.modifiers;
  const modCount = (mods.prefer_warm_register ? 1 : 0) + (mods.prioritize_proactive_polish ? 1 : 0);

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5 space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="text-xs text-[#7a8599] uppercase tracking-wider">Attachments (Phase 3)</div>
          <div className="text-[11px] text-[#7a8599] mt-1">
            Durable OtherModels with mutual regulation. Hard caps: user≤{data.bounds.max_user_regulation_weight.toFixed(2)},
            peer≤{data.bounds.max_peer_regulation_weight.toFixed(2)}, security floor {data.bounds.attachment_security_floor.toFixed(2)},
            silence trigger {data.bounds.separation_trigger_hours}h, care ≤{data.bounds.max_care_budget_tokens_per_day}/day.
          </div>
        </div>
        <span className="text-[10px] px-2 py-0.5 rounded font-mono text-[#7a8599] bg-[#7a85991a] whitespace-nowrap">
          {data.others.length} other{data.others.length === 1 ? '' : 's'}
        </span>
      </div>

      {modCount > 0 ? (
        <div className="rounded bg-[#0a0e14] border border-[#1e2738] p-2">
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mb-1">Active care modifiers</div>
          <div className="flex flex-wrap gap-1">
            {mods.prefer_warm_register ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded font-mono text-[#fbbf24] bg-[#fbbf241a]">
                prefer_warm_register
              </span>
            ) : null}
            {mods.prioritize_proactive_polish ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded font-mono text-[#a78bfa] bg-[#a78bfa1a]">
                prioritize_proactive_polish
              </span>
            ) : null}
          </div>
          <div className="text-[10px] text-[#7a8599] italic mt-1">{mods.reason}</div>
        </div>
      ) : null}

      {users.length > 0 ? (
        <div>
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mb-2">Users</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {users.map((m) => <OtherCard key={m.identity} m={m} bounds={data.bounds} />)}
          </div>
        </div>
      ) : null}

      {peers.length > 0 ? (
        <div>
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mb-2">Peer agents</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {peers.map((m) => <OtherCard key={m.identity} m={m} bounds={data.bounds} />)}
          </div>
        </div>
      ) : null}

      {data.others.length === 0 ? (
        <div className="text-sm text-[#7a8599]">
          No OtherModels yet. They populate as the system interacts with the user and peer agents.
        </div>
      ) : null}

      {candidates.length > 0 ? (
        <div>
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mb-2">
            Check-in candidates · {candidates.length} (none auto-sent)
          </div>
          <div className="space-y-1">
            {candidates.map((c, i) => (
              <div key={`${c.ts}-${i}`} className="rounded border border-[#1e2738] bg-[#0a0e14] p-2">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm text-[#e2e8f0]">{c.display_name}</span>
                  <span className="text-[10px] text-[#7a8599] whitespace-nowrap">
                    {fmtTs(c.ts)} · register: <span className="text-[#fbbf24]">{c.register}</span>
                  </span>
                </div>
                <div className="text-[11px] text-[#7a8599] mt-0.5">{c.note}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {ledger.length > 0 ? (
        <details>
          <summary className="text-[10px] text-[#7a8599] uppercase tracking-wider cursor-pointer">
            Care ledger ({ledger.length} recent)
          </summary>
          <div className="mt-2 space-y-1 max-h-[200px] overflow-y-auto">
            {ledger.map((e, i) => (
              <div key={`${e.ts}-${i}`} className="text-[11px] font-mono text-[#7a8599] flex gap-2">
                <span>{fmtTs(e.ts)}</span>
                <span className="text-[#e2e8f0]">{e.identity}</span>
                <span>{e.tokens}t</span>
                <span>{e.kind}</span>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

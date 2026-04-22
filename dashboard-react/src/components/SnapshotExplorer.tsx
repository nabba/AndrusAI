/**
 * SnapshotExplorer
 * ----------------
 * Admin/debug view for the Postgres-backed observability snapshot
 * store (see app/observability/snapshots.py backend-side).  Exercises
 * all three snapshot-aware TanStack Query hooks:
 *
 *   useSnapshotKinds()   — catalogue of recorded ``kind`` strings.
 *   useSnapshot(kind)    — latest payload for one kind.
 *   useSnapshotHistory() — most-recent N snapshots for one kind.
 *
 * The component is deliberately generic: it renders whatever JSON the
 * payload contains, without any per-kind shape knowledge.  Concrete
 * consumer pages (e.g. a future SubIA-state panel) can copy the same
 * hook calls + render their own typed UI over the payload.
 */
import { useState } from 'react';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useSnapshotKinds,
  useSnapshot,
  useSnapshotHistory,
} from '../api/queries';

function relTime(iso?: string | null): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return iso;
  const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function JsonBlock({ data }: { data: unknown }) {
  return (
    <pre className="text-[11px] leading-relaxed text-[#cbd5e1] bg-[#0a0f17] border border-[#1e2738] rounded p-3 overflow-x-auto max-h-[320px]">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

export function SnapshotExplorer() {
  const kindsQ = useSnapshotKinds();
  const [selected, setSelected] = useState<string | null>(null);
  const [histLimit, setHistLimit] = useState(20);

  // Default to the first kind once the catalogue lands.
  const activeKind =
    selected ?? kindsQ.data?.kinds[0]?.kind ?? null;

  const latestQ = useSnapshot(activeKind ?? '', {
    // enabled when we have a kind; refetch every 30s for a live feel
    // without hammering the gateway.
    refetchMs: 30_000,
  });
  const historyQ = useSnapshotHistory(activeKind ?? '', histLimit, {
    refetchMs: 60_000,
  });

  if (kindsQ.isLoading) return <Skeleton className="h-48" />;
  if (kindsQ.error) return <ErrorPanel error={kindsQ.error} onRetry={kindsQ.refetch} />;

  const kinds = kindsQ.data?.kinds ?? [];

  return (
    <div className="space-y-4">
      <div className="text-xs text-[#7a8599]">
        Postgres-backed observability store (<code className="text-[#60a5fa]">observability_snapshots</code>).
        Each <em>kind</em> is a publisher that emits typed snapshots on a schedule; history accumulates
        so you can inspect recent values or trace state over time. Replaces per-concern Firestore
        collections — same data, no external dependency.
      </div>

      {kinds.length === 0 ? (
        <div className="text-sm text-[#7a8599] italic py-6 text-center">
          No snapshots recorded yet.  Wait up to 5 minutes for the first publisher tick
          (heartbeat fires within 60s).
        </div>
      ) : (
        <>
          {/* Kind selector row */}
          <div className="flex flex-wrap gap-2">
            {kinds.map((k) => {
              const isActive = k.kind === activeKind;
              return (
                <button
                  key={k.kind}
                  onClick={() => setSelected(k.kind)}
                  className={`px-3 py-1.5 rounded-md text-xs transition-colors flex items-center gap-2 border ${
                    isActive
                      ? 'bg-[#60a5fa]/15 text-[#60a5fa] border-[#60a5fa]/40 font-medium'
                      : 'text-[#7a8599] hover:text-[#e2e8f0] border-[#1e2738] bg-[#0a0f17]'
                  }`}
                  title={`${k.count} snapshots · last ${relTime(k.latest_ts)}`}
                >
                  <span>{k.kind}</span>
                  <span className="text-[10px] text-[#7a8599]">({k.count})</span>
                </button>
              );
            })}
          </div>

          {/* Two-column: latest | history */}
          <div className="grid gap-4 lg:grid-cols-2">
            <section className="bg-[#0a0f17] border border-[#1e2738] rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-[#e2e8f0]">
                  Latest payload
                </h3>
                <span className="text-[10px] text-[#7a8599]">
                  {latestQ.data?.ts ? relTime(latestQ.data.ts) : '—'}
                </span>
              </div>
              {latestQ.isLoading ? (
                <Skeleton className="h-32" />
              ) : latestQ.error ? (
                <ErrorPanel error={latestQ.error} onRetry={latestQ.refetch} />
              ) : latestQ.data === null ? (
                <div className="text-xs text-[#7a8599] italic">
                  No snapshot recorded for <code>{activeKind}</code> yet.
                </div>
              ) : (
                <JsonBlock data={latestQ.data?.payload ?? {}} />
              )}
            </section>

            <section className="bg-[#0a0f17] border border-[#1e2738] rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-[#e2e8f0]">
                  Recent history ({historyQ.data?.count ?? 0})
                </h3>
                <select
                  value={histLimit}
                  onChange={(e) => setHistLimit(Number(e.target.value))}
                  className="bg-[#111820] border border-[#1e2738] rounded px-2 py-1 text-xs text-[#e2e8f0]"
                >
                  {[10, 20, 50, 100].map((n) => (
                    <option key={n} value={n}>last {n}</option>
                  ))}
                </select>
              </div>
              {historyQ.isLoading ? (
                <Skeleton className="h-32" />
              ) : historyQ.error ? (
                <ErrorPanel error={historyQ.error} onRetry={historyQ.refetch} />
              ) : !historyQ.data?.items?.length ? (
                <div className="text-xs text-[#7a8599] italic">No history.</div>
              ) : (
                <ul className="space-y-1 max-h-[320px] overflow-y-auto">
                  {historyQ.data.items.map((item, i) => (
                    <li
                      key={i}
                      className="text-[11px] text-[#cbd5e1] bg-[#111820] border border-[#1e2738] rounded px-2 py-1.5 flex items-start gap-3"
                    >
                      <span className="text-[#7a8599] w-20 flex-shrink-0 font-mono">
                        {relTime(item.ts)}
                      </span>
                      <span className="truncate font-mono">
                        {JSON.stringify(item.payload).slice(0, 180)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

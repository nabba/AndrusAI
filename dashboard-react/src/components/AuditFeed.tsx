import { useState, useMemo } from 'react';
import { useApi } from '../hooks/useApi';
import type { AuditEntry } from '../types/index.ts';

function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse bg-[#1e2738] rounded ${className}`} />;
}

export function AuditFeed() {
  const [actorFilter, setActorFilter] = useState('');
  const [actionFilter, setActionFilter] = useState('');

  const { data: entries, loading, error } = useApi<AuditEntry[]>(
    '/audit?limit=100',
    10000
  );

  const filtered = useMemo(() => {
    if (!entries) return [];
    return entries.filter((e) => {
      const actorOk = !actorFilter || e.actor.toLowerCase().includes(actorFilter.toLowerCase());
      const actionOk = !actionFilter || e.action.toLowerCase().startsWith(actionFilter.toLowerCase());
      return actorOk && actionOk;
    });
  }, [entries, actorFilter, actionFilter]);

  // Unique actors for suggestions
  const actors = useMemo(() => {
    if (!entries) return [];
    return Array.from(new Set(entries.map((e) => e.actor))).sort();
  }, [entries]);

  const actionPrefixes = useMemo(() => {
    if (!entries) return [];
    const prefixes = entries.map((e) => e.action.split('.')[0]);
    return Array.from(new Set(prefixes)).sort();
  }, [entries]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-[#e2e8f0]">Audit Feed</h1>
        <p className="text-sm text-[#7a8599] mt-1">Auto-refreshes every 10 seconds</p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <div className="relative">
          <input
            type="text"
            placeholder="Filter by actor..."
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            list="actors-list"
            className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-2 text-sm text-[#e2e8f0] placeholder-[#7a8599] focus:outline-none focus:border-[#60a5fa] w-48"
          />
          <datalist id="actors-list">
            {actors.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </div>
        <div className="relative">
          <input
            type="text"
            placeholder="Filter by action prefix..."
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            list="actions-list"
            className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-2 text-sm text-[#e2e8f0] placeholder-[#7a8599] focus:outline-none focus:border-[#60a5fa] w-52"
          />
          <datalist id="actions-list">
            {actionPrefixes.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </div>
        {(actorFilter || actionFilter) && (
          <button
            onClick={() => {
              setActorFilter('');
              setActionFilter('');
            }}
            className="text-xs px-3 py-2 border border-[#1e2738] text-[#7a8599] rounded-lg hover:border-[#f87171] hover:text-[#f87171] transition-colors"
          >
            Clear
          </button>
        )}
        {entries && (
          <span className="text-xs text-[#7a8599] self-center">
            {filtered.length} of {entries.length} entries
          </span>
        )}
      </div>

      <div className="bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
        {loading ? (
          <div className="p-4 space-y-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : error ? (
          <div className="p-8 text-center text-[#f87171] text-sm">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center text-[#7a8599] text-sm">
            {entries && entries.length > 0 ? 'No entries match filters.' : 'No audit entries yet.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-[#111820]">
                <tr className="border-b border-[#1e2738]">
                  <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider whitespace-nowrap">
                    Timestamp
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">
                    Actor
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">
                    Action
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">
                    Resource
                  </th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-[#7a8599] uppercase tracking-wider">
                    Cost
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e2738]">
                {filtered.map((entry) => (
                  <tr key={entry.id} className="hover:bg-[#1e2738]/50 transition-colors">
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <div className="text-xs text-[#7a8599]">
                        {new Date(entry.created_at).toLocaleDateString()}
                      </div>
                      <div className="text-xs text-[#7a8599]/70">
                        {new Date(entry.created_at).toLocaleTimeString()}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <span className="text-sm text-[#60a5fa]">{entry.actor}</span>
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <span className="text-sm text-[#e2e8f0]">{entry.action}</span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-sm text-[#7a8599] truncate block max-w-[200px]">
                        {entry.resource ?? '—'}
                        {entry.resource_id ? ` #${entry.resource_id}` : ''}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap">
                      {entry.cost != null ? (
                        <span className="text-sm text-[#34d399]">${entry.cost.toFixed(4)}</span>
                      ) : (
                        <span className="text-sm text-[#7a8599]">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

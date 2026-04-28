import { useState } from 'react';
import { useReflectionsListQuery, useReflectionByDateQuery } from '../../api/affect';

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ReflectionsArchive() {
  const listQuery = useReflectionsListQuery();
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const detailQuery = useReflectionByDateQuery(selectedDate);

  const reflections = listQuery.data?.reflections ?? [];
  const sorted = [...reflections].sort((a, b) => (a.date > b.date ? -1 : 1));

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5">
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-xs text-[#7a8599] uppercase tracking-wider">Reflections archive</div>
        <div className="text-xs text-[#7a8599]">
          {reflections.length} daily report{reflections.length === 1 ? '' : 's'}
        </div>
      </div>

      {listQuery.isLoading ? (
        <div className="text-sm text-[#7a8599]">Loading…</div>
      ) : reflections.length === 0 ? (
        <div className="text-sm text-[#7a8599]">
          No reflection cycles have run yet. The first daily reflection is scheduled for 04:30 Helsinki time.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-1 space-y-1 max-h-[420px] overflow-y-auto">
            {sorted.map((r) => (
              <button
                key={r.date}
                type="button"
                onClick={() => setSelectedDate(r.date)}
                className={`w-full text-left rounded-lg border p-2 transition-colors ${
                  selectedDate === r.date
                    ? 'bg-[#60a5fa]/10 border-[#60a5fa]/30 text-[#60a5fa]'
                    : 'bg-[#0a0e14] border-[#1e2738] text-[#e2e8f0] hover:bg-[#1e2738]'
                }`}
              >
                <div className="text-sm font-mono">{r.date}</div>
                <div className="text-[10px] text-[#7a8599]">{fmtBytes(r.size_bytes)}</div>
              </button>
            ))}
          </div>
          <div className="md:col-span-2">
            {!selectedDate ? (
              <div className="text-sm text-[#7a8599] italic">
                Select a date on the left to view its full reflection report.
              </div>
            ) : detailQuery.isLoading ? (
              <div className="text-sm text-[#7a8599]">Loading {selectedDate}…</div>
            ) : detailQuery.isError ? (
              <div className="text-sm text-[#f87171]">Could not load {selectedDate}.</div>
            ) : detailQuery.data ? (
              <pre className="text-[11px] font-mono text-[#e2e8f0] bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3 max-h-[420px] overflow-auto whitespace-pre-wrap break-words">
                {JSON.stringify(detailQuery.data.report, null, 2)}
              </pre>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

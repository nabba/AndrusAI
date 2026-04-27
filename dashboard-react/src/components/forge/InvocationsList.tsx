import type { ForgeInvocation } from '../../types/forge';

export function InvocationsList({
  invocations,
}: {
  invocations: ForgeInvocation[];
}) {
  if (!invocations || invocations.length === 0) {
    return (
      <div className="text-sm text-[#7a8599] italic">
        No invocations recorded — tool has not been called yet.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead className="border-b border-[#1e2738]">
          <tr className="text-left text-[#7a8599] uppercase tracking-wider">
            <th className="px-3 py-2 font-medium">When</th>
            <th className="px-3 py-2 font-medium">Mode</th>
            <th className="px-3 py-2 font-medium">Caller</th>
            <th className="px-3 py-2 font-medium">Caps used</th>
            <th className="px-3 py-2 font-medium">Duration</th>
            <th className="px-3 py-2 font-medium">Out size</th>
            <th className="px-3 py-2 font-medium">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#1e2738]">
          {invocations.map((inv) => (
            <tr key={inv.id} className="text-[#cbd5e1]">
              <td className="px-3 py-2 font-mono whitespace-nowrap">
                {new Date(inv.created_at).toLocaleTimeString()}
              </td>
              <td className="px-3 py-2 font-mono">{inv.mode}</td>
              <td className="px-3 py-2">
                {inv.caller_agent || inv.caller_crew_id || '—'}
              </td>
              <td className="px-3 py-2 font-mono text-[10px]">
                {(inv.capabilities_used || []).join(', ') || '—'}
              </td>
              <td className="px-3 py-2 font-mono">
                {inv.duration_ms != null ? `${inv.duration_ms}ms` : '—'}
              </td>
              <td className="px-3 py-2 font-mono">
                {inv.output_size != null ? `${inv.output_size}b` : '—'}
              </td>
              <td className="px-3 py-2 text-[#f87171] truncate max-w-[200px]">
                {inv.error || ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

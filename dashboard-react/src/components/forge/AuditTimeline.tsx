import { useState } from 'react';
import type { AuditFinding, ForgeAuditLogEntry } from '../../types/forge';

function FindingRow({ finding }: { finding: AuditFinding }) {
  const [expanded, setExpanded] = useState(false);
  const cls = finding.passed
    ? 'border-[#34d399]/30 bg-[#34d399]/5'
    : 'border-[#f87171]/30 bg-[#f87171]/5';
  const statusLabel = finding.passed ? 'pass' : 'fail';
  return (
    <div
      className={`p-3 rounded border ${cls} cursor-pointer`}
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono uppercase text-[#7a8599]">
            {finding.phase}
          </span>
          <span
            className={`text-xs font-medium ${
              finding.passed ? 'text-[#34d399]' : 'text-[#f87171]'
            }`}
          >
            {statusLabel}
          </span>
          <span className="text-xs text-[#7a8599]">
            score {Number(finding.score).toFixed(1)}
          </span>
        </div>
        <span className="text-xs text-[#7a8599]">
          {new Date(finding.timestamp).toLocaleString()}
        </span>
      </div>
      <div className="text-sm text-[#e2e8f0] mt-1.5">{finding.summary}</div>
      {expanded && (
        <pre className="mt-3 p-3 rounded bg-[#0a0e14] text-xs text-[#cbd5e1] overflow-x-auto">
          {JSON.stringify(finding.details, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function AuditFindings({ findings }: { findings: AuditFinding[] }) {
  if (!findings || findings.length === 0) {
    return (
      <div className="text-sm text-[#7a8599] italic">
        No audit findings recorded yet.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {findings.map((f, i) => (
        <FindingRow key={`${f.phase}-${i}`} finding={f} />
      ))}
    </div>
  );
}

export function AuditLogTimeline({
  entries,
}: {
  entries: ForgeAuditLogEntry[];
}) {
  if (!entries || entries.length === 0) {
    return (
      <div className="text-sm text-[#7a8599] italic">
        No audit log entries yet.
      </div>
    );
  }
  return (
    <div className="space-y-1">
      {entries.map((e) => (
        <div
          key={e.id}
          className="flex items-start gap-3 px-3 py-2 rounded bg-[#0a0e14] border border-[#1e2738] text-xs"
        >
          <span className="text-[#7a8599] flex-shrink-0 font-mono">
            {new Date(e.created_at).toLocaleTimeString()}
          </span>
          <span className="text-[#60a5fa] flex-shrink-0 font-mono">
            {e.event_type}
          </span>
          {e.from_status && e.to_status && (
            <span className="text-[#cbd5e1]">
              <span className="text-[#7a8599]">{e.from_status}</span>
              {' → '}
              <span className="text-[#e2e8f0]">{e.to_status}</span>
            </span>
          )}
          <span className="text-[#cbd5e1] flex-1 min-w-0 truncate">
            {e.reason || ''}
          </span>
          <span className="text-[#7a8599] flex-shrink-0">{e.actor}</span>
        </div>
      ))}
    </div>
  );
}

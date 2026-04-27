import type { ToolStatus } from '../../types/forge';

const STATUS_STYLES: Record<ToolStatus, string> = {
  DRAFT: 'bg-[#1e2738] text-[#7a8599] border-[#1e2738]',
  QUARANTINED: 'bg-[#fbbf24]/15 text-[#fbbf24] border-[#fbbf24]/30',
  SHADOW: 'bg-[#60a5fa]/15 text-[#60a5fa] border-[#60a5fa]/30',
  CANARY: 'bg-[#a78bfa]/15 text-[#a78bfa] border-[#a78bfa]/30',
  ACTIVE: 'bg-[#34d399]/15 text-[#34d399] border-[#34d399]/30',
  DEPRECATED: 'bg-[#7a8599]/15 text-[#7a8599] border-[#7a8599]/30',
  KILLED: 'bg-[#f87171]/15 text-[#f87171] border-[#f87171]/30',
};

export function StatusBadge({ status }: { status: ToolStatus }) {
  const cls = STATUS_STYLES[status] ?? STATUS_STYLES.DRAFT;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}
    >
      {status}
    </span>
  );
}

export function RiskBadge({ score }: { score: number | string | null | undefined }) {
  const n = score === null || score === undefined ? null : Number(score);
  if (n === null || Number.isNaN(n)) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border border-[#1e2738] text-[#7a8599]">
        risk —
      </span>
    );
  }
  let cls = 'bg-[#34d399]/15 text-[#34d399] border-[#34d399]/30';
  if (n >= 7) cls = 'bg-[#f87171]/15 text-[#f87171] border-[#f87171]/30';
  else if (n >= 4) cls = 'bg-[#fbbf24]/15 text-[#fbbf24] border-[#fbbf24]/30';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${cls}`}
    >
      risk {n.toFixed(1)}
    </span>
  );
}

export function CapabilityChip({ cap }: { cap: string }) {
  let cls = 'bg-[#1e2738] text-[#cbd5e1] border-[#1e2738]';
  if (cap.startsWith('http.internet.https_post')) {
    cls = 'bg-[#fbbf24]/10 text-[#fbbf24] border-[#fbbf24]/30';
  } else if (cap.startsWith('exec.')) {
    cls = 'bg-[#f87171]/10 text-[#f87171] border-[#f87171]/30';
  } else if (cap.startsWith('signal.')) {
    cls = 'bg-[#a78bfa]/10 text-[#a78bfa] border-[#a78bfa]/30';
  } else if (cap.startsWith('http.')) {
    cls = 'bg-[#60a5fa]/10 text-[#60a5fa] border-[#60a5fa]/30';
  } else if (cap.startsWith('fs.')) {
    cls = 'bg-[#34d399]/10 text-[#34d399] border-[#34d399]/30';
  }
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono border ${cls}`}
    >
      {cap}
    </span>
  );
}

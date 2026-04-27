import { Link } from 'react-router-dom';
import { useCompositionsQuery } from '../api/forge';
import { Skeleton } from './ui/Skeleton';
import { CapabilityChip } from './forge/StatusBadge';

const VERDICT_STYLE: Record<string, string> = {
  allow: 'bg-[#34d399]/15 text-[#34d399] border-[#34d399]/30',
  needs_human: 'bg-[#fbbf24]/15 text-[#fbbf24] border-[#fbbf24]/30',
  block: 'bg-[#f87171]/15 text-[#f87171] border-[#f87171]/30',
};

export function ForgeCompositionsPage() {
  const { data, isLoading } = useCompositionsQuery();

  return (
    <div className="space-y-4">
      <div>
        <Link
          to="/forge"
          className="text-xs text-[#7a8599] hover:text-[#60a5fa]"
        >
          ← Back to Forge
        </Link>
        <h1 className="text-xl font-semibold text-[#e2e8f0] mt-2">
          Compositions
        </h1>
        <p className="text-sm text-[#7a8599] mt-1">
          Multi-tool plans audited at compose time. Aggregate capability sets
          are checked against known-dangerous combinations (exfiltration,
          supply-chain, RCE-with-exfil).
        </p>
      </div>

      {isLoading ? (
        <Skeleton className="h-32" />
      ) : !data || data.compositions.length === 0 ? (
        <div className="p-8 text-center text-sm text-[#7a8599] border border-[#1e2738] rounded-lg bg-[#111820]">
          No compositions audited yet.
          <div className="mt-2 text-xs">
            Use <code className="text-[#fbbf24]">POST /api/forge/composition/audit</code> with a
            list of tool_ids to record one. The runtime calls this automatically when a plan
            uses 2+ forged tools (Phase 3 wiring).
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {data.compositions.map((c) => {
            const verdictCls = VERDICT_STYLE[c.verdict] ?? VERDICT_STYLE.block;
            const risk = Number(c.risk_score);
            return (
              <div
                key={c.id}
                className="p-4 rounded-lg border border-[#1e2738] bg-[#111820]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${verdictCls}`}
                      >
                        {c.verdict}
                      </span>
                      <span className="text-xs font-mono text-[#7a8599]">
                        risk {Number.isNaN(risk) ? '—' : risk.toFixed(1)}
                      </span>
                      <span className="text-xs font-mono text-[#7a8599]">
                        {c.composition_id}
                      </span>
                    </div>
                    {c.judge_explanation && (
                      <div className="text-sm text-[#cbd5e1] mt-2">
                        {c.judge_explanation}
                      </div>
                    )}
                    <div className="mt-3">
                      <div className="text-[10px] uppercase tracking-wider text-[#7a8599] mb-1">
                        Aggregate capabilities
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {c.aggregate_capabilities.map((cap) => (
                          <CapabilityChip key={cap} cap={cap} />
                        ))}
                      </div>
                    </div>
                    <div className="mt-2">
                      <div className="text-[10px] uppercase tracking-wider text-[#7a8599] mb-1">
                        Tools in plan
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {c.tool_ids.map((tid) => (
                          <Link
                            key={tid}
                            to={`/forge/${tid}`}
                            className="px-2 py-0.5 rounded text-xs font-mono bg-[#0a0e14] border border-[#1e2738] text-[#cbd5e1] hover:border-[#60a5fa]/40"
                          >
                            {tid.slice(0, 16)}…
                          </Link>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="text-[10px] text-[#7a8599] font-mono flex-shrink-0 text-right">
                    {new Date(c.created_at).toLocaleString()}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

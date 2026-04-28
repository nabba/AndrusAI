import { useConsciousnessIndicatorsQuery, usePhase5ProposalsQuery } from '../../api/affect';
import type { IndicatorOverlay } from '../../types/affect';
import { Skeleton } from '../ui/Skeleton';
import { Phase5ProposalActions } from './Phase5ProposalActions';

function IndicatorRow({ ind }: { ind: IndicatorOverlay }) {
  const pct = (ind.score / 1.0) * 100;
  const thresholdPct = ind.threshold * 100;
  const barColor = ind.over_threshold ? '#fb923c' : '#60a5fa';

  return (
    <div className="rounded-lg border border-[#1e2738] bg-[#0a0e14] p-3">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-mono text-[#e2e8f0]">{ind.indicator}</span>
          <span className="text-[10px] text-[#7a8599] truncate">{ind.theory}</span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="text-sm font-mono"
            style={{ color: ind.over_threshold ? '#fb923c' : '#e2e8f0' }}
          >
            {ind.score.toFixed(3)}
          </span>
          {ind.over_threshold ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded font-mono text-[#fb923c] bg-[#fb923c1a]">
              OVER
            </span>
          ) : null}
        </div>
      </div>
      <div className="relative h-1.5 rounded-full bg-[#1e2738] overflow-hidden">
        <div
          className="absolute top-0 h-full w-px bg-[#fb923c]"
          style={{ left: `${thresholdPct}%` }}
          title={`Phase-5 threshold ${ind.threshold.toFixed(2)}`}
        />
        <div
          className="h-full transition-all duration-500"
          style={{ width: `${pct}%`, background: barColor }}
        />
      </div>
      <div className="text-[10px] text-[#7a8599] mt-1 flex justify-between">
        <span title={ind.evidence}>{ind.evidence ? ind.evidence.slice(0, 100) : '—'}</span>
        <span className="font-mono">n={ind.samples} · t={ind.threshold.toFixed(2)}</span>
      </div>
    </div>
  );
}

export function ConsciousnessIndicatorsView() {
  const q = useConsciousnessIndicatorsQuery();
  const proposalsQuery = usePhase5ProposalsQuery();

  if (q.isLoading) return <Skeleton className="h-64" />;
  if (q.isError || !q.data) {
    return (
      <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-4 text-sm text-[#f87171]">
        Could not load consciousness indicators.
      </div>
    );
  }

  const status = q.data.status;
  const proposals = proposalsQuery.data?.proposals ?? [];
  const gateColor = status.raised ? '#fb923c' : '#34d399';
  const gateBg = status.raised ? '#fb923c1a' : '#34d3991a';
  const gateLabel = status.raised ? 'GATE RAISED' : 'GATE CLEAR';

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5 space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="text-xs text-[#7a8599] uppercase tracking-wider">
            Consciousness indicators (Phase 5 — observability only)
          </div>
          <div className="text-[11px] text-[#7a8599] mt-1">
            Butlin/Chalmers + Damasio probes. Pure telemetry — never feeds back into evaluation
            or fitness. Sustained-window: {status.sustained_days_required} days.
          </div>
        </div>
        <span
          className="text-xs px-2 py-0.5 rounded font-mono whitespace-nowrap"
          style={{ color: gateColor, background: gateBg }}
        >
          {gateLabel}
        </span>
      </div>

      <div className="rounded-lg border border-[#1e2738] bg-[#0a0e14] p-3">
        <div className="flex items-baseline justify-between mb-1">
          <span className="text-[11px] text-[#7a8599]">Composite (mean across probes)</span>
          <span className="text-2xl font-mono font-semibold" style={{ color: gateColor }}>
            {status.composite_score.toFixed(3)}
          </span>
        </div>
        <div className="relative h-2 rounded-full bg-[#1e2738] overflow-hidden">
          <div
            className="absolute top-0 h-full w-px bg-[#fb923c]"
            style={{ left: `${status.composite_threshold * 100}%` }}
            title={`Threshold ${status.composite_threshold}`}
          />
          <div
            className="h-full"
            style={{
              width: `${status.composite_score * 100}%`,
              background: gateColor,
            }}
          />
        </div>
        <div className="text-[10px] text-[#7a8599] mt-1">{status.notes}</div>
      </div>

      {status.indicators.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {status.indicators.map((i) => (
            <IndicatorRow key={i.indicator} ind={i} />
          ))}
        </div>
      ) : (
        <div className="text-sm text-[#7a8599]">
          No indicator scores available. The probe runner may not be reporting yet.
        </div>
      )}

      {proposals.length > 0 ? (
        <div>
          <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mb-2">
            Pending feature proposals · {proposals.length}
          </div>
          <div className="space-y-2">
            {proposals.map((p, i) => (
              <div key={`${p.feature_name}-${i}`} className="rounded border border-[#1e2738] bg-[#0a0e14] p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm text-[#e2e8f0]">{p.feature_name}</span>
                  <span className="text-[10px] text-[#7a8599]">{p.review_status}</span>
                </div>
                <div className="text-[11px] text-[#7a8599] mt-0.5">
                  proposed by {p.proposed_by} · {new Date(p.proposal_ts).toLocaleString()}
                </div>
                {p.expected_impact && Object.keys(p.expected_impact).length > 0 ? (
                  <div className="text-[11px] font-mono text-[#7a8599] mt-1">
                    expected impact: {Object.entries(p.expected_impact).map(([k, v]) => `${k}=${String(v)}`).join(' · ')}
                  </div>
                ) : null}
                <Phase5ProposalActions featureName={p.feature_name} />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="text-[11px] text-[#7a8599] italic">
        Indicators are computed by the existing <code className="px-1 bg-[#1e2738] rounded">app.subia.probes.consciousness_probe</code>{' '}
        runner; Phase 5 wraps it with the gate + affect-state overlay. The gate raises whenever any
        single indicator or the composite crosses its threshold; raises are appended to{' '}
        <code className="px-1 bg-[#1e2738] rounded">workspace/affect/phase5_gate.jsonl</code> for sustained-window analysis.
      </div>
    </div>
  );
}

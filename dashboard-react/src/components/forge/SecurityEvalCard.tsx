import type { SecurityEval } from '../../types/forge';
import { CapabilityChip, RiskBadge } from './StatusBadge';

interface Props {
  evalData: (SecurityEval & { risk_score: number | string }) | null;
}

export function SecurityEvalCard({ evalData }: Props) {
  if (!evalData) {
    return (
      <div className="p-4 rounded-lg border border-[#1e2738] bg-[#111820] text-sm text-[#7a8599]">
        No semantic security eval has run yet for this tool. The static audit
        runs unconditionally; the LLM judge runs only when a tool is registered
        with valid Python/declarative source.
      </div>
    );
  }

  const declared = evalData.declared_capabilities ?? [];
  const actual = evalData.actual_capability_footprint ?? [];

  return (
    <div className="p-5 rounded-lg border border-[#1e2738] bg-[#111820] space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[#e2e8f0]">
            Security Eval
          </h3>
          <div className="text-xs text-[#7a8599] mt-0.5">
            judge: <span className="font-mono">{evalData.judge_model || '—'}</span>
            {evalData.judged_at && (
              <> · {new Date(evalData.judged_at).toLocaleString()}</>
            )}
          </div>
        </div>
        <RiskBadge score={evalData.risk_score} />
      </div>

      <section>
        <h4 className="text-xs uppercase tracking-wider text-[#7a8599] mb-2">
          What it does
        </h4>
        <p className="text-sm text-[#e2e8f0] leading-relaxed">
          {evalData.what_it_does}
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h4 className="text-xs uppercase tracking-wider text-[#7a8599] mb-2">
            Declared capabilities
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {declared.length > 0 ? (
              declared.map((c) => <CapabilityChip key={c} cap={c} />)
            ) : (
              <span className="text-xs text-[#7a8599]">none</span>
            )}
          </div>
        </div>
        <div>
          <h4 className="text-xs uppercase tracking-wider text-[#7a8599] mb-2">
            Actual capability footprint
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {actual.length > 0 ? (
              actual.map((c) => <CapabilityChip key={c} cap={c} />)
            ) : (
              <span className="text-xs text-[#7a8599]">none observed</span>
            )}
          </div>
        </div>
      </section>

      {evalData.what_could_go_wrong.length > 0 && (
        <section>
          <h4 className="text-xs uppercase tracking-wider text-[#7a8599] mb-2">
            What could go wrong
          </h4>
          <ul className="space-y-1.5 text-sm text-[#e2e8f0]">
            {evalData.what_could_go_wrong.map((concern, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-[#fbbf24] flex-shrink-0">▸</span>
                <span>{concern}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {evalData.attack_classes_considered.length > 0 && (
        <section>
          <h4 className="text-xs uppercase tracking-wider text-[#7a8599] mb-2">
            Attack classes considered
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {evalData.attack_classes_considered.map((a) => (
              <span
                key={a}
                className="inline-flex items-center px-2 py-0.5 rounded text-xs border border-[#1e2738] text-[#cbd5e1] bg-[#0a0e14]"
              >
                {a}
              </span>
            ))}
          </div>
        </section>
      )}

      {evalData.risk_justification && (
        <section className="pt-3 border-t border-[#1e2738]">
          <h4 className="text-xs uppercase tracking-wider text-[#7a8599] mb-2">
            Risk justification
          </h4>
          <p className="text-sm text-[#cbd5e1] leading-relaxed">
            {evalData.risk_justification}
          </p>
        </section>
      )}
    </div>
  );
}

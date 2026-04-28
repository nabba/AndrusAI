import type { AffectState, ViabilityFrame } from '../../types/affect';

interface NowPanelProps {
  affect: AffectState;
  viability: ViabilityFrame;
}

const ATTRACTOR_COLORS: Record<string, string> = {
  peace: '#34d399',
  contentment: '#34d399',
  oneness: '#a5f3fc',
  excitement: '#fbbf24',
  urgency: '#fbbf24',
  hunger: '#fb923c',
  separation: '#fb923c',
  distress: '#f87171',
  discouragement: '#a78bfa',
  depletion: '#a78bfa',
  boredom: '#7a8599',
  overwhelm: '#f472b6',
  neutral: '#7a8599',
};

const VARIABLE_LABELS: Record<string, string> = {
  compute_reserve: 'Compute reserve',
  latency_pressure: 'Latency pressure',
  memory_pressure: 'Memory pressure',
  epistemic_uncertainty: 'Epistemic uncertainty',
  attachment_security: 'Attachment security',
  autonomy: 'Autonomy',
  task_coherence: 'Task coherence',
  novelty_pressure: 'Novelty pressure',
  ecological_connectedness: 'Ecological connectedness',
  self_continuity: 'Self continuity',
};

function valenceColor(v: number): string {
  if (v >= 0.3) return '#34d399';
  if (v >= 0) return '#a5f3fc';
  if (v >= -0.3) return '#fbbf24';
  return '#f87171';
}

function arousalColor(a: number): string {
  if (a >= 0.7) return '#f87171';
  if (a >= 0.45) return '#fbbf24';
  return '#60a5fa';
}

function VABCDial({ label, value, lo, hi, color, hint }: {
  label: string;
  value: number;
  lo: number;
  hi: number;
  color: string;
  hint?: string;
}) {
  const pct = ((value - lo) / (hi - lo)) * 100;
  return (
    <div className="rounded-lg bg-[#0a0e14] border border-[#1e2738] p-4">
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-xs text-[#7a8599] uppercase tracking-wider">{label}</div>
        <div className="text-2xl font-mono font-semibold" style={{ color }}>
          {value >= 0 && lo < 0 ? '+' : ''}{value.toFixed(2)}
        </div>
      </div>
      <div className="h-2 rounded-full bg-[#1e2738] overflow-hidden">
        <div
          className="h-full transition-all duration-500"
          style={{ width: `${Math.max(0, Math.min(100, pct))}%`, background: color }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-[#7a8599] mt-1">
        <span>{lo}</span>
        <span>{hi}</span>
      </div>
      {hint ? <div className="text-[11px] text-[#7a8599] mt-2 italic">{hint}</div> : null}
    </div>
  );
}

function ViabilityBar({ name, value, setpoint, error, source, outOfBand }: {
  name: string;
  value: number;
  setpoint: number;
  error: number;
  source: string;
  outOfBand: boolean;
}) {
  const label = VARIABLE_LABELS[name] ?? name;
  const isPlaceholder = source.includes('placeholder');

  return (
    <div className={`p-3 rounded-lg border transition-colors ${
      outOfBand ? 'bg-[#1a0e0e] border-[#f87171]/40' : 'bg-[#0a0e14] border-[#1e2738]'
    }`}>
      <div className="flex items-baseline justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-sm text-[#e2e8f0]">{label}</span>
          {isPlaceholder ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#1e2738] text-[#7a8599]">
              placeholder
            </span>
          ) : null}
        </div>
        <div className="text-sm font-mono text-[#e2e8f0]">{value.toFixed(2)}</div>
      </div>
      <div className="relative h-1.5 rounded-full bg-[#1e2738] overflow-hidden">
        {/* Setpoint marker */}
        <div
          className="absolute top-0 h-full w-px bg-[#60a5fa]"
          style={{ left: `${setpoint * 100}%` }}
          title={`Set-point ${setpoint.toFixed(2)}`}
        />
        {/* Current value */}
        <div
          className="h-full transition-all duration-500"
          style={{
            width: `${value * 100}%`,
            background: outOfBand ? '#f87171' : '#34d399',
          }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-[#7a8599] mt-1">
        <span title={source}>src: {source}</span>
        <span>err {error.toFixed(2)}</span>
      </div>
    </div>
  );
}

export function NowPanel({ affect, viability }: NowPanelProps) {
  const attractorColor = ATTRACTOR_COLORS[affect.attractor] ?? '#7a8599';
  const updatedAt = new Date(affect.ts).toLocaleString();

  return (
    <div className="space-y-4">
      {/* Attractor banner */}
      <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5">
        <div className="flex items-baseline justify-between">
          <div>
            <div className="text-xs text-[#7a8599] uppercase tracking-wider mb-1">
              Current attractor
            </div>
            <div className="text-3xl font-semibold" style={{ color: attractorColor }}>
              {affect.attractor}
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-[#7a8599]">E_t (allostatic error)</div>
            <div className="text-lg font-mono text-[#e2e8f0]">
              {viability.total_error.toFixed(3)}
            </div>
          </div>
        </div>
        <div className="text-[11px] text-[#7a8599] mt-3">
          Updated {updatedAt}
          {viability.out_of_band.length > 0 ? (
            <span className="ml-2 text-[#fbbf24]">
              · {viability.out_of_band.length} out-of-band
            </span>
          ) : null}
        </div>
      </div>

      {/* V/A/C dials */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <VABCDial
          label="Valence"
          value={affect.valence}
          lo={-1}
          hi={1}
          color={valenceColor(affect.valence)}
          hint={affect.valence_source}
        />
        <VABCDial
          label="Arousal"
          value={affect.arousal}
          lo={0}
          hi={1}
          color={arousalColor(affect.arousal)}
          hint={affect.arousal_source}
        />
        <VABCDial
          label="Controllability"
          value={affect.controllability}
          lo={0}
          hi={1}
          color="#60a5fa"
          hint={affect.controllability_source}
        />
      </div>

      {/* Viability variables */}
      <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5">
        <div className="text-xs text-[#7a8599] uppercase tracking-wider mb-3">
          Viability (H_t — 10 dimensions of the artificial body)
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {Object.entries(viability.values).map(([name, v]) => (
            <ViabilityBar
              key={name}
              name={name}
              value={v}
              setpoint={viability.setpoints[name] ?? 0.5}
              error={viability.per_variable_error[name] ?? 0}
              source={viability.sources[name] ?? '?'}
              outOfBand={viability.out_of_band.includes(name)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

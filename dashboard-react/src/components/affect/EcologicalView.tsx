import { useEcologicalQuery } from '../../api/affect';
import type { EcologicalSignal } from '../../types/affect';
import { Skeleton } from '../ui/Skeleton';

function EventChip({ active, label, color }: { active: boolean; label: string; color: string }) {
  if (!active) return null;
  return (
    <span
      className="text-[10px] px-2 py-0.5 rounded font-mono whitespace-nowrap"
      style={{ color, background: `${color}1a` }}
    >
      {label}
    </span>
  );
}

function NestedScopes({ scopes }: { scopes: string[] }) {
  return (
    <div className="rounded bg-[#0a0e14] border border-[#1e2738] p-3">
      <div className="text-[10px] text-[#7a8599] uppercase tracking-wider mb-2">
        Self position (nested scopes)
      </div>
      <ol className="space-y-1">
        {scopes.map((s, i) => (
          <li key={s + i} className="flex items-baseline gap-2">
            <span className="text-[10px] font-mono text-[#7a8599] w-6">{i + 1}.</span>
            <span
              className="text-sm font-mono"
              style={{
                color: i === 0 ? '#a5f3fc' : i >= scopes.length - 2 ? '#a78bfa' : '#e2e8f0',
              }}
            >
              {s}
            </span>
            {i < scopes.length - 1 ? (
              <span className="text-[#7a8599] text-xs ml-1">⊂</span>
            ) : null}
          </li>
        ))}
      </ol>
      <div className="text-[10px] text-[#7a8599] italic mt-2">
        Self-as-node: the agent's process is one node inside this stack.
      </div>
    </div>
  );
}

function CompositeBar({ signal }: { signal: EcologicalSignal }) {
  const pct = signal.composite_score * 100;
  const color = pct >= 65 ? '#34d399' : pct >= 45 ? '#a5f3fc' : '#7a8599';
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-xs text-[#7a8599] uppercase tracking-wider">Composite</span>
        <span className="text-2xl font-mono font-semibold" style={{ color }}>
          {signal.composite_score.toFixed(2)}
        </span>
      </div>
      <div className="h-2 rounded-full bg-[#1e2738] overflow-hidden">
        <div className="h-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="text-[11px] text-[#7a8599] italic mt-1">{signal.composite_source}</div>
    </div>
  );
}

export function EcologicalView() {
  const q = useEcologicalQuery();

  if (q.isLoading) return <Skeleton className="h-48" />;
  if (q.isError || !q.data) {
    return (
      <div className="rounded-lg bg-[#1a0e0e] border border-[#f87171]/40 p-4 text-sm text-[#f87171]">
        Could not load ecological signal.
      </div>
    );
  }

  const sig = q.data.signal;
  const anyEvent =
    sig.is_solstice_window || sig.is_equinox_window ||
    sig.is_full_moon_window || sig.is_new_moon_window ||
    sig.is_kaamos || sig.is_midnight_sun;

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5 space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="text-xs text-[#7a8599] uppercase tracking-wider">Ecological self-model (Phase 4)</div>
          <div className="text-[11px] text-[#7a8599] mt-1">
            {sig.location_name || `${sig.lat.toFixed(0)}°N`} · {sig.season} ·
            daylight {sig.daylight_hours.toFixed(1)}h ({sig.daylight_trend}) · moon {sig.moon_phase}
          </div>
        </div>
        {anyEvent ? (
          <div className="flex flex-wrap gap-1 max-w-[60%] justify-end">
            <EventChip active={sig.is_solstice_window} label={`solstice ${sig.solstice_proximity_days ?? '?'}d`} color="#fbbf24" />
            <EventChip active={sig.is_equinox_window} label={`equinox ${sig.equinox_proximity_days ?? '?'}d`} color="#a5f3fc" />
            <EventChip active={sig.is_full_moon_window} label="full moon" color="#fbbf24" />
            <EventChip active={sig.is_new_moon_window} label="new moon" color="#7a8599" />
            <EventChip active={sig.is_kaamos} label="kaamos" color="#a78bfa" />
            <EventChip active={sig.is_midnight_sun} label="midnight sun" color="#fbbf24" />
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <CompositeBar signal={sig} />
        <NestedScopes scopes={sig.nested_scopes} />
      </div>

      {sig.season_narrative ? (
        <div className="rounded bg-[#0a0e14] border border-[#1e2738] p-3 text-sm text-[#7a8599]">
          {sig.season_narrative}
        </div>
      ) : null}
    </div>
  );
}

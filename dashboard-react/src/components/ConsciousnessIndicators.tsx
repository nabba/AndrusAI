import { useMemo } from 'react';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useConsciousnessQuery,
  type ProbeResult,
  type ConsciousnessHistoryEntry,
  type HomeostasisState,
} from '../api/queries';

// Port of the legacy dashboard's "Consciousness Indicators (Garland/Butlin-Chalmers)" card.
// Indicators: HOT-2 (Metacognition) · HOT-3 (Belief Coherence) · GWT (Global Broadcast) ·
// SM-A (Self-Model) · WM-A (World-Model) · SOM (Somatic) · INT (Introspection)

const SCORE_COLOR = (score: number) =>
  score >= 0.7 ? '#34d399' : score >= 0.4 ? '#fb923c' : '#f87171';

const SCORE_BG = (score: number) =>
  score >= 0.7 ? 'bg-[#34d399]/10 border-[#34d399]/30'
  : score >= 0.4 ? 'bg-[#fb923c]/10 border-[#fb923c]/30'
  : 'bg-[#f87171]/10 border-[#f87171]/30';

function ProbeCard({ probe }: { probe: ProbeResult }) {
  const pct = Math.round((probe.score ?? 0) * 100);
  const color = SCORE_COLOR(probe.score ?? 0);
  return (
    <div
      className="relative bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3 overflow-hidden hover:border-[#ec4899]/40 transition-colors"
      title={probe.evidence ?? ''}
    >
      <div className="text-lg font-bold text-[#e2e8f0]">{pct}%</div>
      <div className="text-xs font-semibold text-[#e2e8f0] mt-0.5">{probe.indicator}</div>
      <div className="text-[10px] text-[#7a8599] leading-tight mt-0.5 truncate">{probe.theory}</div>
      {probe.samples != null && probe.samples > 0 && (
        <div className="text-[10px] text-[#7a8599] mt-0.5">n={probe.samples}</div>
      )}
      <div
        className="absolute bottom-0 left-0 h-[3px] transition-all"
        style={{ width: `${pct}%`, backgroundColor: color }}
      />
    </div>
  );
}

const HOMEO_BARS: Array<{ key: keyof HomeostasisState; label: string; color: string }> = [
  { key: 'cognitive_energy', label: 'Energy', color: '#60a5fa' },
  { key: 'frustration', label: 'Frustration', color: '#f87171' },
  { key: 'confidence', label: 'Confidence', color: '#34d399' },
  { key: 'curiosity', label: 'Curiosity', color: '#fbbf24' },
];

function HomeostasisBars({ state }: { state: HomeostasisState }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="text-[11px] text-[#7a8599] uppercase tracking-wider">Homeostasis</div>
        {state.last_updated && (
          <div className="text-[10px] text-[#7a8599]">
            {new Date(state.last_updated).toLocaleTimeString()}
          </div>
        )}
      </div>
      <div className="grid grid-cols-4 gap-2">
        {HOMEO_BARS.map((bar) => {
          const raw = state[bar.key];
          const v = typeof raw === 'number' ? raw : 0;
          const pct = Math.round(v * 100);
          return (
            <div key={bar.key} className="text-center">
              <div className="relative h-8 bg-black/20 rounded overflow-hidden">
                <div
                  className="absolute bottom-0 left-0 right-0 rounded-b"
                  style={{ height: `${pct}%`, backgroundColor: bar.color, opacity: 0.3 }}
                />
                <div className="relative leading-8 text-[11px] font-semibold text-[#e2e8f0]">
                  {pct}%
                </div>
              </div>
              <div className="text-[10px] text-[#7a8599] mt-0.5">{bar.label}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Timeline({ history }: { history: ConsciousnessHistoryEntry[] }) {
  // Oldest → newest, left to right.
  const bars = useMemo(() => [...history].reverse(), [history]);
  if (bars.length === 0) {
    return (
      <div className="flex items-center justify-center h-[60px] bg-black/15 rounded-md text-[10px] text-[#7a8599]">
        No historical scores yet
      </div>
    );
  }
  return (
    <div className="flex items-end gap-0.5 h-[60px] bg-black/15 rounded-md p-1 overflow-hidden">
      {bars.map((h, i) => {
        const pct = Math.round((h.score ?? 0) * 100);
        const color = SCORE_COLOR(h.score ?? 0);
        return (
          <div
            key={`${h.timestamp}-${i}`}
            className="flex-1 rounded-sm transition-all"
            title={`${new Date(h.timestamp).toLocaleString()} — ${pct}%`}
            style={{ height: `${Math.max(pct, 5)}%`, backgroundColor: color, minWidth: '2px' }}
          />
        );
      })}
    </div>
  );
}

export function ConsciousnessIndicators() {
  const { data, isLoading, error, refetch } = useConsciousnessQuery();

  const latest = data?.latest;
  const probes = latest?.probes ?? [];
  const history = data?.history ?? [];
  const composite = latest?.composite_score ?? 0;
  const compositePct = Math.round(composite * 100);
  const compositeColor = SCORE_COLOR(composite);
  const homeostasis = data?.homeostasis;
  const hasHomeostasis =
    !!homeostasis &&
    [homeostasis.cognitive_energy, homeostasis.frustration, homeostasis.confidence, homeostasis.curiosity]
      .some((v) => typeof v === 'number');

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-[#ec4899] uppercase tracking-wider flex items-center gap-2">
          <span>🧠</span>
          <span>Consciousness Indicators (Garland/Butlin-Chalmers)</span>
        </h2>
        {latest?.timestamp && (
          <span className="text-[10px] text-[#7a8599]">
            {new Date(latest.timestamp).toLocaleString()}
          </span>
        )}
      </div>

      <div className="bg-[#111820] border border-[#ec4899]/25 rounded-lg p-4 space-y-4">
        {isLoading ? (
          <Skeleton className="h-40" />
        ) : error ? (
          <ErrorPanel error={error} onRetry={refetch} />
        ) : data?.error ? (
          <div className="text-xs text-[#f87171]">{data.error}</div>
        ) : !latest || probes.length === 0 ? (
          <>
            {hasHomeostasis && homeostasis && <HomeostasisBars state={homeostasis} />}
            <div className="text-sm text-[#7a8599] italic">Waiting for first probe run…</div>
          </>
        ) : (
          <>
            {/* Composite score headline */}
            <div className={`flex items-center justify-between rounded-lg border px-3 py-2 ${SCORE_BG(composite)}`}>
              <div>
                <div className="text-[11px] text-[#7a8599] uppercase tracking-wider">Composite</div>
                <div className="text-2xl font-bold" style={{ color: compositeColor }}>
                  {compositePct}%
                </div>
              </div>
              <div className="text-right text-xs text-[#7a8599]">
                <div>{probes.length}/7 probes</div>
                {latest.report_id && <div className="text-[10px] mt-0.5">#{latest.report_id}</div>}
              </div>
            </div>

            {/* Homeostasis bars — functional control signals */}
            {hasHomeostasis && homeostasis && <HomeostasisBars state={homeostasis} />}

            {/* Probe grid */}
            <div
              className="grid gap-2"
              style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))' }}
            >
              {probes.map((p) => (
                <ProbeCard key={p.indicator} probe={p} />
              ))}
            </div>

            {/* Score timeline */}
            <div>
              <div className="text-[11px] text-[#7a8599] mb-1">
                Composite Score Timeline ({history.length} runs, oldest → newest)
              </div>
              <Timeline history={history} />
            </div>

            {/* Summary + legend */}
            {latest.summary && (
              <p className="text-xs text-[#7a8599] leading-relaxed">{latest.summary}</p>
            )}
            <p className="text-[10px] text-[#7a8599]">
              Probes: HOT-2 (Metacognition) · HOT-3 (Belief Coherence) · GWT (Global Broadcast) ·
              SM-A (Self-Model) · WM-A (World-Model) · SOM (Somatic) · INT (Introspection)
            </p>
          </>
        )}
      </div>
    </section>
  );
}

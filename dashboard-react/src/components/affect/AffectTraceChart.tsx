import { useMemo, useState } from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  Filler,
  type ChartOptions,
  type ChartData,
} from 'chart.js';
import { useTraceQuery } from '../../api/affect';
import { Skeleton } from '../ui/Skeleton';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, Filler);

const WINDOWS: { label: string; hours: number }[] = [
  { label: '1h', hours: 1 },
  { label: '24h', hours: 24 },
  { label: '7d', hours: 24 * 7 },
  { label: '30d', hours: 24 * 30 },
  { label: '90d', hours: 24 * 90 },
];

function fmtTs(ts: string, hours: number): string {
  try {
    const d = new Date(ts);
    if (hours <= 24) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (hours <= 24 * 7) return d.toLocaleDateString([], { weekday: 'short', hour: '2-digit' });
    return d.toLocaleDateString([], { month: 'short', day: '2-digit' });
  } catch {
    return ts;
  }
}

export function AffectTraceChart() {
  const [hours, setHours] = useState(24);
  const traceQuery = useTraceQuery(hours);

  const { data, options } = useMemo(() => {
    const points = traceQuery.data?.points ?? [];
    const labels = points.map((p) => fmtTs(p.ts, hours));
    const valenceData = points.map((p) => p.valence);
    const arousalData = points.map((p) => p.arousal);
    const controlData = points.map((p) => p.controllability);

    const data: ChartData<'line'> = {
      labels,
      datasets: [
        {
          label: 'Valence',
          data: valenceData,
          borderColor: '#34d399',
          backgroundColor: '#34d39926',
          fill: false,
          tension: 0.25,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: 1.5,
        },
        {
          label: 'Arousal',
          data: arousalData,
          borderColor: '#fbbf24',
          backgroundColor: '#fbbf2426',
          fill: false,
          tension: 0.25,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: 1.5,
        },
        {
          label: 'Controllability',
          data: controlData,
          borderColor: '#60a5fa',
          backgroundColor: '#60a5fa26',
          fill: false,
          tension: 0.25,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: 1.5,
        },
      ],
    };

    const options: ChartOptions<'line'> = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#7a8599', font: { size: 11 }, boxWidth: 12, padding: 12 },
        },
        tooltip: {
          backgroundColor: '#111820',
          borderColor: '#1e2738',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: '#e2e8f0',
          callbacks: {
            afterBody: (items) => {
              const i = items[0]?.dataIndex ?? -1;
              const p = points[i];
              return p ? `attractor: ${p.attractor}` : '';
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#7a8599', font: { size: 10 }, maxTicksLimit: 10 },
          grid: { color: '#1e273833' },
        },
        y: {
          min: -1,
          max: 1,
          ticks: { color: '#7a8599', font: { size: 10 }, stepSize: 0.5 },
          grid: { color: '#1e273833' },
        },
      },
    };

    return { data, options };
  }, [traceQuery.data, hours]);

  const points = traceQuery.data?.points ?? [];

  return (
    <div className="rounded-lg bg-[#111820] border border-[#1e2738] p-5">
      <div className="flex items-baseline justify-between mb-3 gap-3 flex-wrap">
        <div>
          <div className="text-xs text-[#7a8599] uppercase tracking-wider">Affect trace</div>
          <div className="text-[11px] text-[#7a8599] mt-1">
            {traceQuery.data
              ? `${traceQuery.data.n_returned} of ${traceQuery.data.n_total} samples · last ${hours}h`
              : 'loading…'}
          </div>
        </div>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w.hours}
              type="button"
              onClick={() => setHours(w.hours)}
              className={`text-[11px] px-2 py-1 rounded font-mono transition-colors ${
                hours === w.hours
                  ? 'bg-[#60a5fa]/20 text-[#60a5fa] border border-[#60a5fa]/30'
                  : 'bg-[#0a0e14] text-[#7a8599] border border-[#1e2738] hover:bg-[#1e2738]'
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {traceQuery.isLoading ? (
        <Skeleton className="h-72" />
      ) : points.length === 0 ? (
        <div className="h-72 flex items-center justify-center text-sm text-[#7a8599]">
          No trace samples in this window. The trace populates as the system runs.
        </div>
      ) : (
        <div className="h-72">
          <Line data={data} options={options} />
        </div>
      )}
    </div>
  );
}

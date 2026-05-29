// Benchmark leaderboard operator surface — /cp/benchmarks.
//
// Phase C.3 (2026-05-22). The benchmark suite runs a fixed set of
// YAML-defined tasks against every tier in the LLM cascade, persists
// scores into a JSONL store, and aggregates a leaderboard at read
// time. This page is where the operator scans the results:
//
//   - Header: master-switch state + "refresh now" button
//   - Per-model leaderboard: mean score, pass rate, p50/p95 latency,
//     total cost (sorted desc by mean_score)
//   - Per-task summary: which task is hardest right now (lowest mean)
//   - Catalog summary: how many tasks are defined, by category
//
// When the master switch (benchmarks_enabled) is OFF, the page still
// renders the on-disk leaderboard from prior runs — operators can
// inspect history even when the suite is dormant.

import { useState } from 'react';
import {
  useBenchmarksCatalogQuery,
  useBenchmarksLeaderboardQuery,
  useBenchmarksStatsQuery,
  useForceBenchmarksRefreshMutation,
  type BenchmarkSummary,
} from '../api/benchmarks';
import { useUpdateRuntimeSettings } from '../api/queries';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT_BG = '#111820';
const ACCENT_BORDER = '#1e2738';

const WINDOW_OPTIONS = [
  { label: '24h', days: 1 },
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
];

function pct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

function ms(n: number): string {
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(1)}s`;
}

function usd(n: number): string {
  if (n === 0) return '$0';
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function ScoreCell({ score }: { score: number }) {
  // Color-code: green ≥0.8, amber 0.5-0.8, red <0.5
  const color =
    score >= 0.8 ? '#22c55e' : score >= 0.5 ? '#f59e0b' : '#ef4444';
  return (
    <span style={{ color, fontWeight: 600 }}>
      {(score * 100).toFixed(1)}
    </span>
  );
}

function SummaryRow({
  label,
  summary,
}: {
  label: string;
  summary: BenchmarkSummary;
}) {
  return (
    <tr style={{ borderTop: `1px solid ${ACCENT_BORDER}` }}>
      <td style={{ padding: '8px 12px', color: TEXT_BRIGHT }}>{label}</td>
      <td style={{ padding: '8px 12px', textAlign: 'right' }}>
        <ScoreCell score={summary.mean_score} />
      </td>
      <td
        style={{
          padding: '8px 12px',
          textAlign: 'right',
          color: TEXT_DIM,
        }}
      >
        {pct(summary.pass_rate)}
      </td>
      <td
        style={{
          padding: '8px 12px',
          textAlign: 'right',
          color: TEXT_DIM,
        }}
      >
        {summary.n}
      </td>
      <td
        style={{
          padding: '8px 12px',
          textAlign: 'right',
          color: summary.error_rate > 0.1 ? '#ef4444' : TEXT_DIM,
        }}
      >
        {pct(summary.error_rate)}
      </td>
      <td
        style={{
          padding: '8px 12px',
          textAlign: 'right',
          color: TEXT_DIM,
        }}
      >
        {ms(summary.p50_latency_ms)}
      </td>
      <td
        style={{
          padding: '8px 12px',
          textAlign: 'right',
          color: TEXT_DIM,
        }}
      >
        {ms(summary.p95_latency_ms)}
      </td>
      <td
        style={{
          padding: '8px 12px',
          textAlign: 'right',
          color: TEXT_DIM,
        }}
      >
        {usd(summary.total_cost_usd)}
      </td>
    </tr>
  );
}

export function BenchmarksPage() {
  const [windowDays, setWindowDays] = useState(7);
  const catalog = useBenchmarksCatalogQuery();
  const leaderboard = useBenchmarksLeaderboardQuery(windowDays);
  const stats = useBenchmarksStatsQuery();
  const refresh = useForceBenchmarksRefreshMutation();
  const updateSettings = useUpdateRuntimeSettings();
  const [refreshMsg, setRefreshMsg] = useState<string>('');
  const [toggleErr, setToggleErr] = useState<string>('');

  const enabled =
    leaderboard.data?.enabled ??
    catalog.data?.enabled ??
    stats.data?.enabled ??
    false;
  const nRuns = leaderboard.data?.n_runs ?? 0;
  const taskCount = catalog.data?.stats.task_count ?? 0;
  const storeBytes = stats.data?.bytes ?? 0;

  const handleToggle = async () => {
    setToggleErr('');
    try {
      await updateSettings.mutateAsync({
        benchmarks_enabled: !enabled,
      });
      // Refetch downstream state so the badge / banner update
      catalog.refetch();
      leaderboard.refetch();
      stats.refetch();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setToggleErr(msg);
    }
  };

  const handleRefresh = async () => {
    setRefreshMsg('Starting…');
    try {
      const r = await refresh.mutateAsync(true);
      // 2026-05-28 — refresh is now fire-and-return so the proxy
      // can't 504 on a long pass. The leaderboard + stats queries
      // already poll on 30s/60s; rows appear as they land in the
      // JSONL store.
      if (r.started) {
        setRefreshMsg(
          'Started in background — results land in the leaderboard as runs finish.',
        );
      } else if (r.ran) {
        // Legacy synchronous shape — kept for back-compat if any
        // caller forces the old behavior.
        setRefreshMsg(
          `Ran ${r.n_runs} benchmark(s) in ${r.elapsed_s.toFixed(1)}s ($${r.cost_usd.toFixed(4)})`,
        );
      } else {
        setRefreshMsg(
          `Skipped: ${r.skipped_reason}${r.error ? ` — ${r.error}` : ''}`,
        );
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setRefreshMsg(`Failed: ${msg}`);
    }
  };

  return (
    <div style={{ padding: '24px', color: TEXT_BRIGHT }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '20px',
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 600 }}>
            Benchmark Leaderboard
          </h1>
          <p style={{ margin: '4px 0 0 0', color: TEXT_DIM, fontSize: '13px' }}>
            Cross-model evaluation. Tasks live in
            {' '}<code>app/benchmarks/tasks/*.yaml</code>;
            scores persisted to{' '}
            <code>workspace/benchmarks/runs.jsonl</code>.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {WINDOW_OPTIONS.map(o => (
            <button
              key={o.days}
              onClick={() => setWindowDays(o.days)}
              style={{
                padding: '6px 12px',
                background: windowDays === o.days ? '#3b82f6' : ACCENT_BG,
                color: windowDays === o.days ? '#fff' : TEXT_BRIGHT,
                border: `1px solid ${ACCENT_BORDER}`,
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '12px',
              }}
            >
              {o.label}
            </button>
          ))}
          <button
            onClick={handleRefresh}
            disabled={refresh.isPending}
            style={{
              padding: '6px 12px',
              background: refresh.isPending ? '#6b7280' : '#10b981',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: refresh.isPending ? 'not-allowed' : 'pointer',
              fontSize: '12px',
              fontWeight: 600,
            }}
          >
            {refresh.isPending ? 'Running…' : 'Refresh now'}
          </button>
        </div>
      </div>

      {/* Status banner */}
      <div
        style={{
          padding: '12px 16px',
          background: enabled ? '#0a2f1a' : '#2f1a0a',
          border: `1px solid ${enabled ? '#10b981' : '#f59e0b'}`,
          borderRadius: '4px',
          marginBottom: '20px',
          fontSize: '13px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <strong>{enabled ? 'Enabled' : 'Disabled'}</strong>
          <button
            onClick={handleToggle}
            disabled={updateSettings.isPending}
            style={{
              padding: '4px 10px',
              background: enabled ? '#7f1d1d' : '#10b981',
              color: '#fff', border: 'none', borderRadius: '4px',
              cursor: updateSettings.isPending ? 'not-allowed' : 'pointer',
              fontSize: '11px', fontWeight: 600,
            }}
            title={
              enabled
                ? 'Stop the scheduled idle pass (catalog stays queryable)'
                : 'Resume the scheduled idle pass'
            }
          >
            {updateSettings.isPending
              ? '…'
              : enabled ? 'Disable' : 'Enable'}
          </button>
          <span>
            {taskCount} task(s) in catalog
            {' • '}
            {nRuns} run(s) in window
            {' • '}
            {storeBytes > 0
              ? `store ${(storeBytes / 1024).toFixed(1)} KB`
              : 'store empty'}
          </span>
          {refreshMsg && (
            <span style={{ marginLeft: '12px', color: TEXT_DIM }}>
              {refreshMsg}
            </span>
          )}
        </div>
        {toggleErr && (
          <div style={{ marginTop: '4px', color: '#f87171' }}>
            Toggle failed: {toggleErr}
          </div>
        )}
        {!enabled && (
          <span style={{ display: 'block', marginTop: '4px', color: TEXT_DIM }}>
            Suite is dormant — click <em>Enable</em> for scheduled passes,
            or <em>Refresh now</em> for a one-shot operator-initiated pass.
          </span>
        )}
      </div>

      {/* Per-model leaderboard */}
      <section style={{ marginBottom: '32px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>
          By model
        </h2>
        <div
          style={{
            border: `1px solid ${ACCENT_BORDER}`,
            borderRadius: '4px',
            overflow: 'hidden',
          }}
        >
          <table
            style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}
          >
            <thead>
              <tr style={{ background: ACCENT_BG, color: TEXT_DIM }}>
                <th style={{ padding: '8px 12px', textAlign: 'left' }}>
                  Model
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Score
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Pass %
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  N
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Err %
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  p50
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  p95
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.isLoading && (
                <tr>
                  <td
                    colSpan={8}
                    style={{ padding: '24px', textAlign: 'center', color: TEXT_DIM }}
                  >
                    Loading…
                  </td>
                </tr>
              )}
              {!leaderboard.isLoading &&
                Object.keys(leaderboard.data?.by_model ?? {}).length === 0 && (
                  <tr>
                    <td
                      colSpan={8}
                      style={{
                        padding: '24px',
                        textAlign: 'center',
                        color: TEXT_DIM,
                      }}
                    >
                      No runs in this window. Click <em>Refresh now</em> or
                      enable the master switch.
                    </td>
                  </tr>
                )}
              {Object.entries(leaderboard.data?.by_model ?? {}).map(
                ([model, summary]) => (
                  <SummaryRow
                    key={model}
                    label={model}
                    summary={summary}
                  />
                ),
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Per-task summary */}
      <section style={{ marginBottom: '32px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>
          By task (lowest mean = hardest)
        </h2>
        <div
          style={{
            border: `1px solid ${ACCENT_BORDER}`,
            borderRadius: '4px',
            overflow: 'hidden',
          }}
        >
          <table
            style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}
          >
            <thead>
              <tr style={{ background: ACCENT_BG, color: TEXT_DIM }}>
                <th style={{ padding: '8px 12px', textAlign: 'left' }}>
                  Task
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Score
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Pass %
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  N
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Err %
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  p50
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  p95
                </th>
                <th style={{ padding: '8px 12px', textAlign: 'right' }}>
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(leaderboard.data?.by_task ?? {})
                .sort((a, b) => a[1].mean_score - b[1].mean_score)
                .map(([taskId, summary]) => (
                  <SummaryRow
                    key={taskId}
                    label={taskId}
                    summary={summary}
                  />
                ))}
              {!leaderboard.isLoading &&
                Object.keys(leaderboard.data?.by_task ?? {}).length === 0 && (
                  <tr>
                    <td
                      colSpan={8}
                      style={{
                        padding: '24px',
                        textAlign: 'center',
                        color: TEXT_DIM,
                      }}
                    >
                      No runs in this window.
                    </td>
                  </tr>
                )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Catalog summary */}
      <section>
        <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '8px' }}>
          Catalog
        </h2>
        <div
          style={{
            padding: '16px',
            background: ACCENT_BG,
            border: `1px solid ${ACCENT_BORDER}`,
            borderRadius: '4px',
            fontSize: '13px',
            color: TEXT_DIM,
          }}
        >
          {catalog.isLoading ? (
            'Loading…'
          ) : (
            <>
              <div>
                <strong style={{ color: TEXT_BRIGHT }}>
                  {catalog.data?.stats.task_count ?? 0}
                </strong>{' '}
                task(s) total. By category:{' '}
                {Object.entries(catalog.data?.stats.by_category ?? {})
                  .map(([cat, n]) => `${cat} (${n})`)
                  .join(' • ')}
              </div>
              <div style={{ marginTop: '6px' }}>
                Scorers in use:{' '}
                {Object.entries(catalog.data?.stats.by_scorer ?? {})
                  .map(([sc, n]) => `${sc} (${n})`)
                  .join(' • ')}
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

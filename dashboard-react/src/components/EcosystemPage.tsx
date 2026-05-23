// /cp/ecosystem — Annual ecosystem snapshot browser (PROGRAM §63, U7).
//
// Each year's snapshot is a structured JSON + rendered markdown. The
// operator browses past years from a sidebar, reads the markdown
// inline, and per major-upgrade row taps Accept to file the
// downstream CR (non-framework) or Tier-3 amendment (framework). The
// acceptance IS the gate — no second approval.

import { useEffect, useState } from 'react';
import { api } from '../api/client';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const ACCENT = '#5ec8a8';
const WARN = '#f87171';
const PANEL_BG = '#111820';
const BORDER = '#1e2738';

type MajorUpgradeRow = {
  package: string;
  from_version: string;
  to_version: string;
  priority: 'low' | 'medium' | 'high';
  is_framework: boolean;
  capability_summary: string;
  status: 'proposed' | 'accepted' | 'deferred' | 'rejected';
  accepted_at: string | null;
  cr_id: string | null;
};

type Snapshot = {
  year: number;
  generated_at: string;
  python_eol: {
    current: string;
    eol_date: string | null;
    days_until_eol: number | null;
    future_versions: { version: string; eol: string }[];
  };
  package_health: { severity: string; count: number }[];
  framework_health: {
    package: string;
    current_version: string;
    latest_version: string;
    last_release_age_days: number | null;
  }[];
  vendor_concentration: Record<string, number>;
  major_upgrades: MajorUpgradeRow[];
};

type SnapshotsListItem = {
  year: number;
  generated_at: string;
  major_upgrade_count: number;
  accepted_count: number;
};

export function EcosystemPage() {
  const [years, setYears] = useState<SnapshotsListItem[]>([]);
  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [markdown, setMarkdown] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState<string | null>(null);
  const [generating, setGenerating] = useState<boolean>(false);
  // B2-P2: confirmation modal state for framework rows.
  const [confirmRow, setConfirmRow] = useState<MajorUpgradeRow | null>(null);

  const refreshList = async () => {
    setError(null);
    try {
      const data = await api('/api/cp/ecosystem/snapshots') as { years: SnapshotsListItem[] };
      setYears(data.years || []);
      if (data.years.length > 0 && selectedYear === null) {
        setSelectedYear(data.years[data.years.length - 1].year);
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const loadSnapshot = async (year: number) => {
    setError(null);
    try {
      const data = await api(`/api/cp/ecosystem/snapshots/${year}`) as {
        snapshot: Snapshot;
        markdown: string;
      };
      setSnapshot(data.snapshot);
      setMarkdown(data.markdown);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void refreshList();
  }, []);

  useEffect(() => {
    if (selectedYear !== null) {
      void loadSnapshot(selectedYear);
    }
  }, [selectedYear]);

  const handleGenerate = async (force: boolean = false) => {
    setError(null);
    setGenerating(true);
    try {
      const data = await api('/api/cp/ecosystem/snapshots/generate', {
        method: 'POST',
        body: JSON.stringify({ force }),
      }) as { year: number };
      await refreshList();
      setSelectedYear(data.year);
    } catch (e) {
      setError(String(e));
    } finally {
      setGenerating(false);
    }
  };

  // B2-P2 — framework rows route through Tier-3 amendment. The
  // amendment files a paper-trail decision record but does NOT
  // change requirements.txt (apply_hook only watches the standard
  // CR audit). Pop a modal so the operator knows what they're
  // signing up for: a multi-week migration project, not a one-click
  // bump.
  const requestAccept = (row: MajorUpgradeRow) => {
    if (!snapshot || row.status === 'accepted') return;
    if (row.is_framework) {
      setConfirmRow(row);
    } else {
      void doAccept(row);
    }
  };

  const doAccept = async (row: MajorUpgradeRow) => {
    if (!snapshot) return;
    setAccepting(row.package + '@' + row.to_version);
    setError(null);
    try {
      await api('/api/cp/ecosystem/major-upgrades/accept', {
        method: 'POST',
        body: JSON.stringify({
          year: snapshot.year,
          package: row.package,
          to_version: row.to_version,
        }),
      });
      await loadSnapshot(snapshot.year);
      await refreshList();
    } catch (e) {
      setError(String(e));
    } finally {
      setAccepting(null);
    }
  };

  if (years.length === 0) {
    return (
      <div className="p-6">
        <h1 className="text-lg font-medium mb-2" style={{ color: TEXT_BRIGHT }}>
          Ecosystem snapshots
        </h1>
        <p className="text-sm" style={{ color: TEXT_DIM }}>
          No annual snapshots have been generated yet. The next snapshot
          is written automatically on the first cron-eligible day of
          January each year (PROGRAM §63 U6). Generate one now to
          preview the surface — it composes live data from PyPI, the
          dependency_radar state, the cost ledger, and the capability
          backlog.
        </p>
        <div className="mt-4">
          <button
            onClick={() => handleGenerate(false)}
            disabled={generating}
            className="px-3 py-2 text-sm rounded"
            style={{
              background: ACCENT,
              color: '#0a0e14',
              opacity: generating ? 0.5 : 1,
            }}
          >
            {generating ? 'Generating…' : 'Generate snapshot now'}
          </button>
          <p className="text-[10px] mt-2" style={{ color: TEXT_DIM }}>
            Takes a few seconds — fetches the 6 framework packages from
            PyPI. Network failures are isolated per source; the
            snapshot still generates with whatever data is available.
          </p>
        </div>
        {error && (
          <p className="text-xs mt-3" style={{ color: WARN }}>
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="p-6">
      <h1 className="text-lg font-medium mb-3" style={{ color: TEXT_BRIGHT }}>
        Ecosystem snapshots
      </h1>

      <div className="flex gap-4">
        {/* Year sidebar */}
        <div
          className="w-32 rounded-lg p-3 border"
          style={{ background: PANEL_BG, borderColor: BORDER }}
        >
          <h3 className="text-xs uppercase tracking-wide mb-2"
              style={{ color: TEXT_DIM }}>
            Years
          </h3>
          <div className="space-y-1">
            {[...years].reverse().map((y) => (
              <button
                key={y.year}
                onClick={() => setSelectedYear(y.year)}
                className="block w-full text-left px-2 py-1 text-xs rounded"
                style={{
                  background: selectedYear === y.year ? ACCENT + '33' : 'transparent',
                  color: selectedYear === y.year ? TEXT_BRIGHT : TEXT_DIM,
                }}
              >
                {y.year}
                <div className="text-[10px]" style={{ color: TEXT_DIM }}>
                  {y.accepted_count}/{y.major_upgrade_count} accepted
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Snapshot detail */}
        <div className="flex-1 space-y-4">
          {snapshot === null ? (
            <p className="text-sm" style={{ color: TEXT_DIM }}>
              {error ? error : 'Loading…'}
            </p>
          ) : (
            <>
              {/* Python EOL banner */}
              <PythonEolBanner eol={snapshot.python_eol} />

              {/* Major-upgrade table — the action surface */}
              <div
                className="rounded-lg border"
                style={{ background: PANEL_BG, borderColor: BORDER }}
              >
                <div className="p-3 border-b" style={{ borderColor: BORDER }}>
                  <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
                    Major-upgrade plan ({snapshot.major_upgrades.length} candidates)
                  </h2>
                  <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
                    Accepting a row routes to a CR (non-framework) or
                    Tier-3 amendment (framework). Operator's acceptance
                    IS the gate — build + deploy run automatically.
                  </p>
                </div>
                {snapshot.major_upgrades.length === 0 ? (
                  <p className="p-3 text-xs" style={{ color: TEXT_DIM }}>
                    No major bumps queued for this year.
                  </p>
                ) : (
                  <table className="w-full text-xs">
                    <thead>
                      <tr style={{ color: TEXT_DIM }}>
                        <th className="text-left p-2">Package</th>
                        <th className="text-left p-2">Bump</th>
                        <th className="text-left p-2">Priority</th>
                        <th className="text-left p-2">Capability</th>
                        <th className="text-left p-2">Status</th>
                        <th className="text-right p-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshot.major_upgrades.map((row) => (
                        <tr
                          key={row.package + '@' + row.to_version}
                          style={{ color: TEXT_BRIGHT, borderTop: `1px solid ${BORDER}` }}
                        >
                          <td className="p-2">
                            {row.package}
                            {row.is_framework && (
                              <span
                                className="ml-1 text-[10px] px-1 rounded"
                                style={{ background: '#3b82f622', color: '#60a5fa' }}
                              >
                                framework
                              </span>
                            )}
                          </td>
                          <td className="p-2">
                            {row.from_version} → <strong>{row.to_version}</strong>
                          </td>
                          <td className="p-2">
                            <PriorityBadge priority={row.priority} />
                          </td>
                          <td className="p-2" style={{ color: TEXT_DIM }}>
                            {row.capability_summary}
                          </td>
                          <td className="p-2">
                            <StatusBadge status={row.status} />
                            {row.cr_id && (
                              <a
                                href="/cp/changes"
                                className="ml-2 text-[10px] underline"
                                style={{ color: ACCENT }}
                              >
                                {row.cr_id}
                              </a>
                            )}
                          </td>
                          <td className="p-2 text-right">
                            {row.status === 'proposed' ? (
                              <button
                                onClick={() => requestAccept(row)}
                                disabled={accepting === row.package + '@' + row.to_version}
                                className="px-2 py-1 text-xs rounded"
                                style={{
                                  background: ACCENT,
                                  color: '#0a0e14',
                                  opacity: accepting === row.package + '@' + row.to_version ? 0.5 : 1,
                                }}
                              >
                                {accepting === row.package + '@' + row.to_version
                                  ? 'Accepting…'
                                  : 'Accept'}
                              </button>
                            ) : (
                              <span style={{ color: TEXT_DIM }}>{row.status}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {/* Vendor concentration */}
              {Object.keys(snapshot.vendor_concentration).length > 0 && (
                <div
                  className="rounded-lg border p-3"
                  style={{ background: PANEL_BG, borderColor: BORDER }}
                >
                  <h2 className="text-sm font-medium mb-2" style={{ color: TEXT_BRIGHT }}>
                    Vendor concentration (past year)
                  </h2>
                  <ul className="space-y-1 text-xs">
                    {Object.entries(snapshot.vendor_concentration)
                      .sort(([, a], [, b]) => b - a)
                      .map(([vendor, fraction]) => (
                        <li key={vendor} style={{ color: TEXT_BRIGHT }}>
                          {vendor}:{' '}
                          <span style={{ color: ACCENT }}>
                            {(fraction * 100).toFixed(1)}%
                          </span>
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {/* Framework health */}
              <div
                className="rounded-lg border p-3"
                style={{ background: PANEL_BG, borderColor: BORDER }}
              >
                <h2 className="text-sm font-medium mb-2" style={{ color: TEXT_BRIGHT }}>
                  Framework health
                </h2>
                <ul className="space-y-1 text-xs">
                  {snapshot.framework_health.map((fw) => (
                    <li key={fw.package} style={{ color: TEXT_BRIGHT }}>
                      {fw.package}: current{' '}
                      <code style={{ color: TEXT_DIM }}>
                        {fw.current_version || '?'}
                      </code>{' '}
                      / latest{' '}
                      <code style={{ color: TEXT_DIM }}>
                        {fw.latest_version || '?'}
                      </code>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Markdown source */}
              <details
                className="rounded-lg border p-3"
                style={{ background: PANEL_BG, borderColor: BORDER }}
              >
                <summary
                  className="text-xs cursor-pointer"
                  style={{ color: TEXT_DIM }}
                >
                  Full markdown source (wiki/self/ecosystem/{snapshot.year}.md)
                </summary>
                <pre
                  className="text-[10px] mt-2 overflow-x-auto"
                  style={{ color: TEXT_DIM, whiteSpace: 'pre-wrap' }}
                >
                  {markdown}
                </pre>
              </details>
            </>
          )}

          {error && (
            <p
              className="text-xs px-2 py-1 rounded"
              style={{ color: WARN, background: '#7f1d1d22' }}
            >
              {error}
            </p>
          )}
        </div>
      </div>

      {/* B2-P2: framework acceptance confirmation modal */}
      {confirmRow && (
        <FrameworkAcceptModal
          row={confirmRow}
          year={snapshot ? snapshot.year : new Date().getFullYear()}
          onCancel={() => setConfirmRow(null)}
          onConfirm={async () => {
            const row = confirmRow;
            setConfirmRow(null);
            await doAccept(row);
          }}
        />
      )}
    </div>
  );
}


function FrameworkAcceptModal({
  row,
  year,
  onCancel,
  onConfirm,
}: {
  row: MajorUpgradeRow;
  year: number;
  onCancel: () => void;
  onConfirm: () => Promise<void> | void;
}) {
  const [submitting, setSubmitting] = useState(false);
  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-50"
      style={{ background: 'rgba(0,0,0,0.65)' }}
      onClick={onCancel}
    >
      <div
        className="rounded-lg p-5 max-w-lg space-y-3 border"
        style={{ background: '#0a0e14', borderColor: WARN }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-medium" style={{ color: WARN }}>
          🏛 Framework migration — read this first
        </h2>
        <p className="text-xs leading-relaxed" style={{ color: TEXT_BRIGHT }}>
          You're accepting <strong>{row.package} {row.from_version}
          → {row.to_version}</strong>, marked as a framework upgrade.
          Frameworks are deliberately excluded from the standard
          requirements-bump auto-apply path.
        </p>
        <div
          className="rounded p-3 text-[11px] space-y-2"
          style={{ background: '#111820', color: TEXT_DIM }}
        >
          <div>
            <strong style={{ color: ACCENT }}>What clicking Confirm does:</strong>
          </div>
          <ul className="list-disc list-inside space-y-1">
            <li>Files a <strong>Tier-3 amendment</strong> as a paper trail at <code>docs/proposed_upgrades/</code></li>
            <li>Marks the row <code>accepted</code> in snapshot {year}</li>
            <li>Emits a <code>framework_migration_started</code> Signal alert</li>
            <li>Auto-creates a thread for tracking the migration</li>
          </ul>
          <div className="pt-1">
            <strong style={{ color: WARN }}>What it does NOT do:</strong>
          </div>
          <ul className="list-disc list-inside space-y-1">
            <li>Change <code>requirements.txt</code> automatically</li>
            <li>Apply any code changes</li>
            <li>Run trials or impact analysis</li>
          </ul>
          <p className="pt-1" style={{ color: TEXT_BRIGHT }}>
            The actual migration follows the playbook at{' '}
            <code>docs/UPGRADE_LIFECYCLE_FRAMEWORK_MIGRATION.md</code> —
            a multi-week, group-by-group hand-authored project.
          </p>
        </div>
        <div className="flex justify-end gap-2">
          <button
            className="px-3 py-1 text-xs rounded"
            style={{ background: '#374151', color: TEXT_BRIGHT }}
            disabled={submitting}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="px-3 py-1 text-xs rounded"
            style={{ background: WARN, color: '#0a0e14' }}
            disabled={submitting}
            onClick={async () => {
              setSubmitting(true);
              try {
                await onConfirm();
              } finally {
                setSubmitting(false);
              }
            }}
          >
            {submitting ? 'Filing…' : 'Confirm — file paper trail'}
          </button>
        </div>
      </div>
    </div>
  );
}

function PythonEolBanner({ eol }: { eol: Snapshot['python_eol'] }) {
  const days = eol.days_until_eol;
  const isUrgent = days !== null && days < 365;
  return (
    <div
      className="rounded-lg border p-3"
      style={{
        background: isUrgent ? '#7f1d1d22' : PANEL_BG,
        borderColor: isUrgent ? WARN : BORDER,
      }}
    >
      <div className="flex items-center gap-2">
        <span style={{ fontSize: 18 }}>🐍</span>
        <span className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
          Python {eol.current}
        </span>
      </div>
      {days !== null && eol.eol_date && (
        <p className="text-xs mt-1" style={{ color: TEXT_DIM }}>
          EOL on <strong>{eol.eol_date}</strong> —{' '}
          <span style={{ color: isUrgent ? WARN : TEXT_BRIGHT }}>
            {days} days
          </span>{' '}
          from today.
          {isUrgent && (
            <span style={{ color: WARN }}>
              {' '}Plan an upgrade window.
            </span>
          )}
        </p>
      )}
      {eol.future_versions.length > 0 && (
        <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
          Future:{' '}
          {eol.future_versions.map((f) => `${f.version} → ${f.eol}`).join(' · ')}
        </p>
      )}
    </div>
  );
}

function PriorityBadge({ priority }: { priority: string }) {
  const colors: Record<string, [string, string]> = {
    high: ['#7f1d1d', '#f87171'],
    medium: ['#78350f', '#fbbf24'],
    low: ['#1e3a8a', '#60a5fa'],
  };
  const [bg, fg] = colors[priority] || ['#374151', '#9ca3af'];
  return (
    <span
      className="px-2 py-0.5 text-[10px] uppercase rounded"
      style={{ background: bg + '44', color: fg }}
    >
      {priority}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, [string, string]> = {
    proposed: ['#374151', TEXT_DIM],
    accepted: ['#065f4644', ACCENT],
    deferred: ['#374151', TEXT_DIM],
    rejected: ['#7f1d1d44', WARN],
  };
  const [bg, fg] = colors[status] || ['#374151', TEXT_DIM];
  return (
    <span
      className="px-2 py-0.5 text-[10px] uppercase rounded"
      style={{ background: bg, color: fg }}
    >
      {status}
    </span>
  );
}

// Settings genealogy — Gap #4 (2026-05-24).
//
// One row per runtime-settings flip with before/after/actor/reason +
// a hash-chained ledger underneath. Surfaces:
//   * The 25 most-recent changes with diff + reason.
//   * Chain integrity status (any tamper / bit-rot becomes visible).
//
// The "last changed" badge per individual switch lives in each
// switch's own card via the ``lastChanges`` map exposed here; this
// card is the operator's chronological view.

import { useEffect, useState } from 'react';
import { api } from '../api/client';

const TEXT_DIM = '#7a8599';
const TEXT_BRIGHT = '#e2e8f0';
const TEXT_OK = '#86efac';
const TEXT_WARN = '#fbbf24';
const TEXT_BAD = '#f87171';
const BORDER = '#1e2738';

type GenealogyRow = {
  ts: number;
  iso: string;
  key: string;
  old: unknown;
  new: unknown;
  actor: string;
  reason: string;
  prev_hash: string;
  hash: string;
};

type GenealogyChain = {
  ok: boolean;
  n_rows: number;
  first_bad_row: number | null;
  reason: string | null;
};

type GenealogyResponse = {
  rows: GenealogyRow[];
  last_by_key: Record<string, GenealogyRow>;
  chain: GenealogyChain;
};

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return '∅';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return v.length > 60 ? v.slice(0, 60) + '…' : v;
  try {
    const s = JSON.stringify(v);
    return s.length > 60 ? s.slice(0, 60) + '…' : s;
  } catch {
    return String(v);
  }
}

function formatTs(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export function SettingsGenealogyCard() {
  const [data, setData] = useState<GenealogyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await api('/api/cp/settings/genealogy?limit=25');
        if (!cancelled) {
          setData(resp as GenealogyResponse);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <div
      className="rounded-lg p-4 border space-y-3"
      style={{ background: '#111820', borderColor: BORDER }}
    >
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium" style={{ color: TEXT_BRIGHT }}>
            Settings genealogy
          </h2>
          <p className="text-[10px] mt-1" style={{ color: TEXT_DIM }}>
            Hash-chained log of every runtime-settings flip with before /
            after / actor / reason. Operator includes a <code>__reason__</code>
            field in any POST to record motivation alongside the change.
          </p>
        </div>
        <button
          className="text-[10px] underline"
          style={{ color: TEXT_DIM }}
          onClick={() => setRefreshKey((k) => k + 1)}
        >
          refresh
        </button>
      </div>

      {error && (
        <div className="text-xs" style={{ color: TEXT_BAD }}>
          {error}
        </div>
      )}

      {data && (
        <>
          <div
            className="text-[10px] flex gap-3 pb-2 border-b"
            style={{ borderColor: BORDER, color: TEXT_DIM }}
          >
            <span>Total rows: {data.chain.n_rows}</span>
            <span>
              Chain:{' '}
              <span style={{ color: data.chain.ok ? TEXT_OK : TEXT_BAD }}>
                {data.chain.ok
                  ? 'OK'
                  : `BROKEN @ row ${data.chain.first_bad_row} (${data.chain.reason})`}
              </span>
            </span>
            <span>Showing: {data.rows.length} most recent</span>
          </div>

          {data.rows.length === 0 ? (
            <p className="text-[10px]" style={{ color: TEXT_DIM }}>
              No flips recorded yet. The ledger starts on the first
              setting change.
            </p>
          ) : (
            <div className="space-y-1.5 max-h-96 overflow-y-auto">
              {data.rows.map((row) => (
                <div
                  key={row.hash}
                  className="text-[10px] py-1 px-2 rounded"
                  style={{ background: '#0d141d' }}
                >
                  <div className="flex justify-between" style={{ color: TEXT_BRIGHT }}>
                    <code>{row.key}</code>
                    <span style={{ color: TEXT_DIM }}>{formatTs(row.iso)}</span>
                  </div>
                  <div style={{ color: TEXT_DIM }}>
                    <span style={{ color: TEXT_WARN }}>{renderValue(row.old)}</span>
                    {' → '}
                    <span style={{ color: TEXT_OK }}>{renderValue(row.new)}</span>
                    {' · by '}
                    <code>{row.actor}</code>
                  </div>
                  {row.reason && (
                    <div className="mt-0.5 italic" style={{ color: TEXT_DIM }}>
                      “{row.reason}”
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

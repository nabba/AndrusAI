import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { endpoints } from '../api/endpoints';

// ── Types ─────────────────────────────────────────────────────────────────

/** Normalized shape every KB endpoint returns (after our adapter below). */
export interface KBDocument {
  id: string;
  title: string;
  author?: string | null;
  themes?: string[];
  chunks?: number;
  added_at?: string | null;
  size_bytes?: number;
  source?: string;
  /** Genre marker used by Literature (Fiction). */
  genre?: string;
  /** Used by experiential entries. */
  snippet?: string;
  emotional_valence?: string;
}

type KBType =
  | 'enterprise' | 'philosophy' | 'literature' | 'episteme'
  | 'experiential' | 'aesthetics' | 'tensions';

// ── Per-KB endpoint mapping + response normalizers ────────────────────────
//
// Each backend endpoint returns slightly different shapes (legacy reasons:
// /kb/documents wraps in {documents}, /episteme/documents returns a bare
// list, /philosophy/documents wraps in {texts}). The adapter below
// normalizes every variant to KBDocument[].

const KB_DOCUMENTS_ENDPOINT: Partial<Record<KBType, string>> = {
  enterprise:   endpoints.kbDocuments(),
  philosophy:   endpoints.philosophyDocuments(),
  literature:   endpoints.fictionDocuments(),
  episteme:     endpoints.epistemeDocuments(),
  experiential: endpoints.experientialDocuments(),
};

function normalizeResponse(raw: unknown): KBDocument[] {
  if (!raw) return [];
  // /kb/documents, /fiction/documents, /experiential/documents:
  // {documents: [...]}
  if (typeof raw === 'object' && raw !== null) {
    const r = raw as Record<string, unknown>;
    if (Array.isArray(r.documents)) return r.documents as KBDocument[];
    // /philosophy/documents: {texts: [...], status: "ok"}
    if (Array.isArray(r.texts)) {
      return (r.texts as Array<Record<string, unknown>>).map((t) => ({
        id: String(t.filename ?? t.id ?? ''),
        title: String(t.title ?? t.filename ?? ''),
        author: (t.author as string | null | undefined) ?? null,
        themes: (t.themes as string[] | undefined) ?? [],
        chunks: (t.chunks as number | undefined) ?? 0,
        added_at: (t.added_at as string | null | undefined) ?? null,
        size_bytes: t.size_bytes as number | undefined,
      }));
    }
  }
  // /episteme/documents: bare array of {filename, title, author, themes, ...}
  if (Array.isArray(raw)) {
    return (raw as Array<Record<string, unknown>>).map((t) => ({
      id: String(t.filename ?? t.id ?? ''),
      title: String(t.title ?? t.filename ?? ''),
      author: (t.author as string | null | undefined) ?? null,
      themes: (t.themes as string[] | undefined) ?? [],
      chunks: (t.chunks as number | undefined) ?? 0,
      added_at: (t.added_at as string | null | undefined) ?? null,
    }));
  }
  return [];
}

function useDocumentsQuery(kbType: KBType) {
  const path = KB_DOCUMENTS_ENDPOINT[kbType];
  return useQuery<KBDocument[]>({
    queryKey: ['kb-documents', kbType],
    enabled: !!path,
    queryFn: async () => {
      if (!path) return [];
      const raw = await api<unknown>(path);
      return normalizeResponse(raw);
    },
    refetchInterval: 60_000,
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    // "MMM DD, YYYY" — short and locale-stable
    return d.toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: '2-digit',
    });
  } catch {
    return '—';
  }
}

function ThemesBadges({ themes }: { themes?: string[] }) {
  if (!themes || themes.length === 0) {
    return <span style={{ color: '#6b7280', fontSize: 11 }}>—</span>;
  }
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {themes.slice(0, 4).map((t, i) => (
        <span
          key={`${t}-${i}`}
          style={{
            background: 'rgba(96,165,250,0.12)',
            border: '1px solid rgba(96,165,250,0.4)',
            color: '#93c5fd',
            fontSize: 10,
            padding: '1px 6px',
            borderRadius: 10,
            whiteSpace: 'nowrap',
          }}
        >
          {t}
        </span>
      ))}
      {themes.length > 4 && (
        <span style={{ color: '#6b7280', fontSize: 10 }}>
          +{themes.length - 4}
        </span>
      )}
    </div>
  );
}

// ── Component ─────────────────────────────────────────────────────────────

interface KBDocumentListProps {
  kbType: KBType;
  /** Hex color for the KB's accent — matches the parent card. */
  color: string;
}

/**
 * Expandable list of documents in a KB. Collapsed by default to keep
 * cards compact; click "Documents (N)" to expand. Each row shows
 * title, author, themes (extracted), chunks, and added date.
 *
 * Endpoints not implemented yet for some KBs (aesthetics, tensions);
 * for those types the component renders nothing.
 */
export function KBDocumentList({ kbType, color }: KBDocumentListProps) {
  const [open, setOpen] = useState(false);
  const path = KB_DOCUMENTS_ENDPOINT[kbType];
  const { data, isLoading, error } = useDocumentsQuery(kbType);

  // KBs without a dedicated /documents endpoint don't render anything —
  // the upload zone above is enough.
  if (!path) return null;

  const docs = data ?? [];
  const total = docs.length;

  if (total === 0 && !isLoading) {
    return null;
  }

  return (
    <div style={{ marginTop: 12 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          background: 'transparent',
          border: `1px solid ${color}40`,
          color: color,
          fontSize: 12,
          padding: '4px 10px',
          borderRadius: 6,
          cursor: 'pointer',
          width: '100%',
          textAlign: 'left',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span>📋 Documents{isLoading ? ' (loading…)' : ` (${total})`}</span>
        <span>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div
          style={{
            marginTop: 8,
            border: `1px solid ${color}30`,
            borderRadius: 6,
            overflow: 'hidden',
            maxHeight: 360,
            overflowY: 'auto',
          }}
        >
          {error && (
            <div style={{ color: '#f87171', padding: 12, fontSize: 12 }}>
              Failed to load documents: {String(error)}
            </div>
          )}
          {!error && docs.length === 0 && !isLoading && (
            <div style={{ color: '#6b7280', padding: 12, fontSize: 12 }}>
              No documents yet.
            </div>
          )}
          {!error && docs.length > 0 && (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.03)' }}>
                  <th style={thStyle}>Title</th>
                  <th style={thStyle}>Author</th>
                  <th style={thStyle}>Themes</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}>Chunks</th>
                  <th style={{ ...thStyle, whiteSpace: 'nowrap' }}>Added</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d, i) => (
                  <tr
                    key={d.id || `${d.title}-${i}`}
                    style={{
                      borderTop: '1px solid rgba(255,255,255,0.05)',
                    }}
                  >
                    <td style={{ ...tdStyle, maxWidth: 280 }}>
                      <div
                        style={{
                          fontWeight: 500,
                          color: '#e5e7eb',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                        title={d.title}
                      >
                        {d.title || '(untitled)'}
                      </div>
                      {d.snippet && (
                        <div
                          style={{
                            fontSize: 10,
                            color: '#9ca3af',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                          title={d.snippet}
                        >
                          {d.snippet}
                        </div>
                      )}
                      {d.genre && (
                        <div style={{ fontSize: 10, color: '#9ca3af' }}>{d.genre}</div>
                      )}
                    </td>
                    <td style={{ ...tdStyle, color: '#d1d5db' }}>
                      {d.author && d.author !== 'Unknown' ? d.author : '—'}
                    </td>
                    <td style={tdStyle}>
                      <ThemesBadges themes={d.themes} />
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right', color: '#d1d5db' }}>
                      {d.chunks ?? 0}
                    </td>
                    <td style={{ ...tdStyle, color: '#9ca3af', whiteSpace: 'nowrap' }}>
                      {formatDate(d.added_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: '6px 10px',
  fontSize: 11,
  fontWeight: 600,
  color: '#9ca3af',
  textTransform: 'uppercase',
  letterSpacing: 0.4,
};

const tdStyle: React.CSSProperties = {
  padding: '6px 10px',
  verticalAlign: 'top',
};

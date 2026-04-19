import { useCallback, useEffect, useMemo, useState } from 'react';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useNotesRootsQuery,
  useNotesTreeQuery,
  useNoteFileQuery,
  useNotesGraphQuery,
  useNotesSearchQuery,
  useNotesTagsQuery,
  type NoteTreeNode,
  type NoteFileReport,
} from '../api/queries';
import { NoteTree } from './notes/NoteTree';
import { NoteRenderer } from './notes/NoteRenderer';
import { NoteGraph } from './notes/NoteGraph';
import './notes/notes.css';

type ViewMode = 'document' | 'graph' | 'search';

const STORAGE_KEYS = {
  root: 'botarmy:notes:root',
  path: 'botarmy:notes:path',
} as const;

// Flatten the tree into a lookup of rel_path → title, used when we only
// have a path (e.g. resolving a wikilink click before the file payload loads).
function flattenTree(node: NoteTreeNode, out: Map<string, string>): void {
  if (node.type === 'note') {
    const title = node.name.replace(/\.(md|mdx|markdown)$/i, '');
    out.set(node.path, title);
  }
  for (const child of node.children ?? []) flattenTree(child, out);
}

// Case-insensitive resolution for wikilinks: try exact match, then basename,
// falling back to null if nothing matches.
function resolveWikiTarget(target: string, treeNode: NoteTreeNode): string | null {
  const needle = target.toLowerCase();
  const candidates = new Map<string, string>();
  flattenTree(treeNode, candidates);
  if (candidates.has(target)) return target;
  for (const [path, title] of candidates) {
    const lowerPath = path.toLowerCase();
    if (lowerPath === needle) return path;
    if (lowerPath === `${needle}.md`) return path;
    if (lowerPath.endsWith(`/${needle}.md`)) return path;
    if (lowerPath.endsWith(`/${needle}`)) return path;
    if (title.toLowerCase() === needle) return path;
  }
  return null;
}

export function NotesPage() {
  const rootsQ = useNotesRootsQuery();

  // Seed root from localStorage immediately; `null` until roots arrive, then
  // reconciled in render below (no setState-in-effect).
  const [root, setRoot] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    return window.localStorage.getItem(STORAGE_KEYS.root);
  });
  const [path, setPath] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    const r = window.localStorage.getItem(STORAGE_KEYS.root);
    return r ? window.localStorage.getItem(`${STORAGE_KEYS.path}:${r}`) : null;
  });
  const [mode, setMode] = useState<ViewMode>('document');
  const [treeFilter, setTreeFilter] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  // Reconcile during render when the roots query arrives:
  // - Seeded root not in the roots list → fall back to default / first root.
  // - Nothing seeded at all → pick default / first.
  // This is the React "store info from previous render" pattern; no effect.
  const rootsData = rootsQ.data;
  if (rootsData && rootsData.roots.length > 0) {
    const rootNames = rootsData.roots.map((r) => r.name);
    if (!root || !rootNames.includes(root)) {
      const next = rootsData.default_root && rootNames.includes(rootsData.default_root)
        ? rootsData.default_root
        : rootNames[0];
      setRoot(next);
      const stored = typeof window !== 'undefined'
        ? window.localStorage.getItem(`${STORAGE_KEYS.path}:${next}`)
        : null;
      setPath(stored);
    }
  }

  // Persist selection to localStorage (external system — legitimate effect).
  useEffect(() => {
    if (typeof window === 'undefined' || !root) return;
    window.localStorage.setItem(STORAGE_KEYS.root, root);
    if (path) window.localStorage.setItem(`${STORAGE_KEYS.path}:${root}`, path);
  }, [root, path]);

  const treeQ = useNotesTreeQuery(root);
  const fileQ = useNoteFileQuery(root, path);
  const graphQ = useNotesGraphQuery(mode === 'graph' ? root : null);
  const searchQ = useNotesSearchQuery(mode === 'search' ? root : null, searchTerm);
  const tagsQ = useNotesTagsQuery(root);

  const tree = treeQ.data?.tree;

  // Auto-select first note when tree arrives and nothing is picked (render-time
  // reconciliation — no effect).
  if (tree && !path) {
    const first = findFirstNote(tree);
    if (first) setPath(first);
  }

  const handleWikiClick = useCallback(
    (target: string, isMarkdownLink: boolean) => {
      if (!tree) return;
      // Markdown-link paths are already root-relative; wikilinks are bare names.
      const resolved = isMarkdownLink
        ? resolveWikiTarget(target, tree) ?? (target.endsWith('.md') ? target : `${target}.md`)
        : resolveWikiTarget(target, tree);
      if (resolved) {
        setPath(resolved);
        setMode('document');
      }
    },
    [tree],
  );

  const hasRoots = !!rootsQ.data?.roots.length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[#e2e8f0]">Notes</h1>
          <p className="text-sm text-[#7a8599] mt-1">
            Obsidian-style viewer for markdown under configured root directories.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {rootsQ.data?.roots && rootsQ.data.roots.length > 0 && (
            <select
              value={root ?? ''}
              onChange={(e) => {
                const next = e.target.value;
                setRoot(next);
                const stored = typeof window !== 'undefined'
                  ? window.localStorage.getItem(`${STORAGE_KEYS.path}:${next}`)
                  : null;
                setPath(stored);
              }}
              className="bg-[#111820] border border-[#1e2738] rounded-lg px-3 py-1.5 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
            >
              {rootsQ.data.roots.map((r) => (
                <option key={r.name} value={r.name}>{r.name} — {r.path}</option>
              ))}
            </select>
          )}
          <div className="flex gap-1 bg-[#111820] rounded-lg p-1 border border-[#1e2738]">
            {(['document', 'graph', 'search'] as ViewMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`px-3 py-1 rounded-md text-xs capitalize transition-colors ${
                  mode === m ? 'bg-[#60a5fa]/15 text-[#60a5fa] font-medium' : 'text-[#7a8599] hover:text-[#e2e8f0]'
                }`}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      </div>

      {rootsQ.isLoading ? (
        <Skeleton className="h-64" />
      ) : rootsQ.error ? (
        <ErrorPanel error={rootsQ.error} onRetry={rootsQ.refetch} />
      ) : !hasRoots ? (
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-6 text-sm text-[#7a8599]">
          No note roots configured. Set the <code className="text-[#60a5fa]">NOTES_ROOTS</code> env var on the gateway:
          <pre className="text-[10px] mt-2 bg-[#0a0e14] p-3 rounded overflow-x-auto">
            {`NOTES_ROOTS='{"docs":"/path/to/docs","notes":"/path/to/notes"}'`}
          </pre>
        </div>
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(220px, 280px) minmax(0, 1fr) minmax(220px, 300px)' }}>
          {/* Left: tree + tree filter */}
          <aside className="bg-[#111820] border border-[#1e2738] rounded-lg p-3 space-y-2 max-h-[calc(100vh-200px)] overflow-y-auto">
            <input
              type="text"
              value={treeFilter}
              onChange={(e) => setTreeFilter(e.target.value)}
              placeholder="Filter tree…"
              className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1 text-xs text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
            />
            {treeQ.isLoading ? (
              <Skeleton className="h-48" />
            ) : treeQ.error ? (
              <ErrorPanel error={treeQ.error} onRetry={treeQ.refetch} />
            ) : tree ? (
              <NoteTree
                tree={tree}
                activePath={path}
                onSelect={(p) => { setPath(p); setMode('document'); }}
                filter={treeFilter}
              />
            ) : null}
          </aside>

          {/* Center: main content */}
          <main className="bg-[#111820] border border-[#1e2738] rounded-lg p-5 min-h-[400px] max-h-[calc(100vh-200px)] overflow-y-auto">
            {mode === 'document' && (
              <DocumentView
                root={root}
                path={path}
                file={fileQ.data}
                isLoading={fileQ.isLoading}
                error={fileQ.error}
                onRetry={fileQ.refetch}
                onNavigate={handleWikiClick}
              />
            )}
            {mode === 'graph' && (
              <>
                {graphQ.isLoading ? (
                  <Skeleton className="h-96" />
                ) : graphQ.error ? (
                  <ErrorPanel error={graphQ.error} onRetry={graphQ.refetch} />
                ) : graphQ.data ? (
                  <NoteGraph
                    nodes={graphQ.data.nodes}
                    edges={graphQ.data.edges}
                    activePath={path}
                    onSelect={(p) => { setPath(p); setMode('document'); }}
                  />
                ) : null}
              </>
            )}
            {mode === 'search' && (
              <SearchView
                value={searchTerm}
                onChange={setSearchTerm}
                hits={searchQ.data?.hits ?? []}
                loading={searchQ.isFetching}
                onOpen={(p) => { setPath(p); setMode('document'); }}
              />
            )}
          </main>

          {/* Right: backlinks + outline + tags */}
          <aside className="bg-[#111820] border border-[#1e2738] rounded-lg p-3 space-y-4 max-h-[calc(100vh-200px)] overflow-y-auto">
            <OutlinePanel body={fileQ.data?.body} onJump={(slug) => scrollToHeading(slug)} />
            <LinksPanel title="Backlinks" links={fileQ.data?.backlinks ?? []} empty="No backlinks" onSelect={(p) => { setPath(p); setMode('document'); }} />
            <LinksPanel title="Outgoing" links={fileQ.data?.forward_links ?? []} empty="No outgoing links" onSelect={(p) => { setPath(p); setMode('document'); }} />
            <TagsPanel tags={tagsQ.data?.tags ?? []} activeTags={fileQ.data?.tags ?? []} />
          </aside>
        </div>
      )}
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function findFirstNote(node: NoteTreeNode): string | null {
  if (node.type === 'note') return node.path;
  for (const child of node.children ?? []) {
    const found = findFirstNote(child);
    if (found) return found;
  }
  return null;
}

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

function scrollToHeading(slug: string) {
  const el = document.getElementById(slug);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Sub-panels ──────────────────────────────────────────────────────────────

function DocumentView({
  root,
  path,
  file,
  isLoading,
  error,
  onRetry,
  onNavigate,
}: {
  root: string | null;
  path: string | null;
  file: NoteFileReport | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  onNavigate: (target: string, isMarkdownLink: boolean) => void;
}) {
  if (!path) {
    return (
      <div className="text-sm text-[#7a8599] italic">
        Select a note in the tree to start reading. Use <kbd className="px-1.5 py-0.5 bg-[#1e2738] rounded text-xs">Graph</kbd> for the force-directed view.
      </div>
    );
  }
  if (isLoading) return <Skeleton className="h-64" />;
  if (error) return <ErrorPanel error={error} onRetry={onRetry} />;
  if (!file) return null;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <h1 className="text-2xl font-semibold text-[#e2e8f0] leading-tight">{file.title}</h1>
        <span className="text-[10px] text-[#7a8599] whitespace-nowrap">
          {new Date(file.mtime * 1000).toLocaleString()}
        </span>
      </div>
      <div className="text-xs text-[#7a8599] font-mono mb-4 truncate">
        {file.root}/{file.path}
      </div>
      {file.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          {file.tags.map((t) => (
            <span key={t} className="text-[10px] px-2 py-0.5 rounded-full border border-[#a78bfa]/30 bg-[#a78bfa]/10 text-[#a78bfa]">
              #{t}
            </span>
          ))}
        </div>
      )}
      {Object.keys(file.frontmatter ?? {}).length > 0 && (
        <details className="mb-4">
          <summary className="text-xs text-[#7a8599] cursor-pointer hover:text-[#e2e8f0]">Frontmatter ({Object.keys(file.frontmatter).length})</summary>
          <pre className="text-[10px] bg-[#0a0e14] border border-[#1e2738] rounded p-3 mt-2 overflow-x-auto">
            {JSON.stringify(file.frontmatter, null, 2)}
          </pre>
        </details>
      )}
      <NoteRenderer body={file.body} root={root as string} currentPath={path} onNavigate={onNavigate} />
    </div>
  );
}

function OutlinePanel({ body, onJump }: { body: string | undefined; onJump: (slug: string) => void }) {
  const headings = useMemo(() => {
    if (!body) return [] as { level: number; text: string; slug: string }[];
    const out: { level: number; text: string; slug: string }[] = [];
    for (const line of body.split('\n')) {
      const m = line.match(/^(#{1,6})\s+(.+?)\s*$/);
      if (m) {
        const level = m[1].length;
        const text = m[2].replace(/[#*_`]/g, '').trim();
        out.push({ level, text, slug: slugify(text) });
      }
    }
    return out;
  }, [body]);

  if (headings.length === 0) return null;
  return (
    <div>
      <h3 className="text-[10px] uppercase tracking-wider text-[#7a8599] font-medium mb-2">Outline</h3>
      <div className="space-y-0.5 text-xs">
        {headings.map((h, i) => (
          <button
            key={`${h.slug}-${i}`}
            onClick={() => onJump(h.slug)}
            className="block w-full text-left text-[#e2e8f0] hover:text-[#60a5fa] truncate"
            style={{ paddingLeft: `${(h.level - 1) * 10}px` }}
            title={h.text}
          >
            {h.text}
          </button>
        ))}
      </div>
    </div>
  );
}

function LinksPanel({
  title,
  links,
  empty,
  onSelect,
}: {
  title: string;
  links: { path: string; title: string }[];
  empty: string;
  onSelect: (path: string) => void;
}) {
  return (
    <div>
      <h3 className="text-[10px] uppercase tracking-wider text-[#7a8599] font-medium mb-2">
        {title} ({links.length})
      </h3>
      {links.length === 0 ? (
        <div className="text-[11px] text-[#7a8599] italic">{empty}</div>
      ) : (
        <div className="space-y-1">
          {links.map((l) => (
            <button
              key={l.path}
              onClick={() => onSelect(l.path)}
              className="block w-full text-left bg-[#0a0e14] border border-[#1e2738] rounded px-2 py-1.5 text-xs text-[#e2e8f0] hover:border-[#60a5fa]/40 transition-colors truncate"
              title={l.path}
            >
              {l.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function TagsPanel({ tags, activeTags }: { tags: { tag: string; count: number }[]; activeTags: string[] }) {
  if (tags.length === 0) return null;
  const set = new Set(activeTags);
  return (
    <div>
      <h3 className="text-[10px] uppercase tracking-wider text-[#7a8599] font-medium mb-2">Tags ({tags.length})</h3>
      <div className="flex flex-wrap gap-1">
        {tags.slice(0, 40).map((t) => (
          <span
            key={t.tag}
            className={`text-[10px] px-2 py-0.5 rounded-full border ${
              set.has(t.tag)
                ? 'border-[#a78bfa]/60 bg-[#a78bfa]/20 text-[#a78bfa]'
                : 'border-[#1e2738] bg-[#0a0e14] text-[#7a8599]'
            }`}
            title={`${t.count} note${t.count === 1 ? '' : 's'}`}
          >
            #{t.tag}
            <span className="ml-1 opacity-60">{t.count}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function SearchView({
  value,
  onChange,
  hits,
  loading,
  onOpen,
}: {
  value: string;
  onChange: (v: string) => void;
  hits: { path: string; title: string; snippet: string; tags: string[] }[];
  loading: boolean;
  onOpen: (path: string) => void;
}) {
  return (
    <div className="space-y-3">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search note titles + bodies…"
        className="w-full bg-[#0a0e14] border border-[#1e2738] rounded px-3 py-2 text-sm text-[#e2e8f0] focus:outline-none focus:border-[#60a5fa]"
        autoFocus
      />
      {value.trim().length === 0 ? (
        <div className="text-sm text-[#7a8599] italic">Start typing to search.</div>
      ) : loading ? (
        <Skeleton className="h-48" />
      ) : hits.length === 0 ? (
        <div className="text-sm text-[#7a8599] italic">No matches.</div>
      ) : (
        <div className="space-y-2">
          {hits.map((h) => (
            <button
              key={h.path}
              onClick={() => onOpen(h.path)}
              className="block w-full text-left bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3 hover:border-[#60a5fa]/40 transition-colors"
            >
              <div className="text-sm font-semibold text-[#e2e8f0]">{h.title}</div>
              <div className="text-[10px] text-[#7a8599] font-mono truncate">{h.path}</div>
              {h.snippet && (
                <div className="text-xs text-[#7a8599] mt-1 leading-snug">{h.snippet}</div>
              )}
              {h.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {h.tags.map((t) => (
                    <span key={t} className="text-[9px] px-1.5 py-0.5 rounded-full border border-[#a78bfa]/30 bg-[#a78bfa]/10 text-[#a78bfa]">
                      #{t}
                    </span>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

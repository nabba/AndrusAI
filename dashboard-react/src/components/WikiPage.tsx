import { useMemo, useState } from 'react';
import {
  useNotesRootsQuery,
  useNotesTreeQuery,
  useNoteFileQuery,
  useNotesGraphQuery,
  useNotesSearchQuery,
  type NoteTreeNode,
} from '../api/queries';
import { NoteTree } from './notes/NoteTree';
import { NoteRenderer } from './notes/NoteRenderer';
import { NoteGraph } from './notes/NoteGraph';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';

const ROOT = 'wiki';

type ViewMode = 'reader' | 'graph';

function findFirstNote(node: NoteTreeNode | undefined): string | null {
  if (!node) return null;
  if (node.type === 'note') return node.path;
  if (node.children) {
    const indexNode = node.children.find((c) => c.name === 'index.md');
    if (indexNode) return indexNode.path;
    for (const c of node.children) {
      const found = findFirstNote(c);
      if (found) return found;
    }
  }
  return null;
}

export function WikiPage() {
  const rootsQ = useNotesRootsQuery();
  const treeQ = useNotesTreeQuery(ROOT);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [search, setSearch] = useState('');
  const [view, setView] = useState<ViewMode>('reader');

  const fileQ = useNoteFileQuery(ROOT, activePath);
  const graphQ = useNotesGraphQuery(view === 'graph' ? ROOT : null);
  const searchQ = useNotesSearchQuery(ROOT, search.trim());

  const wikiAvailable = useMemo(
    () => rootsQ.data?.roots.some((r) => r.name === ROOT) ?? false,
    [rootsQ.data],
  );

  // Auto-select index.md (or the first note found) when the tree arrives and
  // nothing is selected. Render-time reconciliation — no setState in an effect.
  if (!activePath && treeQ.data) {
    const first = findFirstNote(treeQ.data.tree);
    if (first) setActivePath(first);
  }

  if (rootsQ.isLoading) {
    return <Skeleton className="h-64" />;
  }

  if (rootsQ.isError) {
    return <ErrorPanel error={rootsQ.error} onRetry={() => rootsQ.refetch()} />;
  }

  if (!wikiAvailable) {
    return (
      <div className="rounded-lg border border-[#fbbf24]/30 bg-[#fbbf24]/5 p-4 text-sm text-[#fbbf24]">
        Wiki root is not configured on the gateway. Set <code>NOTES_ROOTS</code> or
        ensure <code>/app/wiki</code> exists inside the container.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold text-[#e2e8f0]">Knowledge Wiki</h1>
          <p className="text-xs text-[#7a8599]">
            Auto-synthesised pages from agents and skill files. Open as Obsidian vault for graph view.
          </p>
        </div>
        <div className="flex gap-1 bg-[#111820] rounded-lg p-1 border border-[#1e2738]">
          <button
            onClick={() => setView('reader')}
            className={`px-3 py-1 text-xs rounded ${
              view === 'reader'
                ? 'bg-[#60a5fa]/15 text-[#60a5fa]'
                : 'text-[#7a8599] hover:text-[#e2e8f0]'
            }`}
          >
            📄 Reader
          </button>
          <button
            onClick={() => setView('graph')}
            className={`px-3 py-1 text-xs rounded ${
              view === 'graph'
                ? 'bg-[#60a5fa]/15 text-[#60a5fa]'
                : 'text-[#7a8599] hover:text-[#e2e8f0]'
            }`}
          >
            🕸️ Graph
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="mb-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search wiki pages…"
          className="w-full px-3 py-2 bg-[#111820] border border-[#1e2738] rounded-lg text-sm text-[#e2e8f0] placeholder:text-[#7a8599] focus:outline-none focus:border-[#60a5fa]/50"
        />
      </div>

      {/* Search results overlay */}
      {search.trim() && (
        <div className="mb-3 rounded-lg border border-[#1e2738] bg-[#111820] p-3 max-h-64 overflow-y-auto">
          {searchQ.isLoading && <div className="text-xs text-[#7a8599]">Searching…</div>}
          {searchQ.data && searchQ.data.hits.length === 0 && (
            <div className="text-xs text-[#7a8599]">No matches.</div>
          )}
          {searchQ.data?.hits.map((hit) => (
            <button
              key={hit.path}
              onClick={() => {
                setActivePath(hit.path);
                setView('reader');
                setSearch('');
              }}
              className="block w-full text-left px-2 py-1.5 hover:bg-[#1e2738]/50 rounded"
            >
              <div className="text-sm text-[#e2e8f0]">{hit.title}</div>
              <div className="text-xs text-[#7a8599] truncate">{hit.snippet}</div>
            </button>
          ))}
        </div>
      )}

      {/* Main split */}
      <div className="flex flex-1 min-h-0 gap-3">
        {/* Tree */}
        <aside className="w-64 flex-shrink-0 flex flex-col bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden">
          <div className="p-2 border-b border-[#1e2738]">
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter tree…"
              className="w-full px-2 py-1 bg-[#0a0e14] border border-[#1e2738] rounded text-xs text-[#e2e8f0] placeholder:text-[#7a8599] focus:outline-none focus:border-[#60a5fa]/50"
            />
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {treeQ.isLoading && <Skeleton className="h-32" />}
            {treeQ.isError && <ErrorPanel error={treeQ.error} onRetry={() => treeQ.refetch()} />}
            {treeQ.data && (
              <NoteTree
                tree={treeQ.data.tree}
                activePath={activePath}
                onSelect={setActivePath}
                filter={filter}
              />
            )}
          </div>
        </aside>

        {/* Reader / Graph */}
        <main className="flex-1 min-w-0 bg-[#111820] border border-[#1e2738] rounded-lg overflow-hidden flex flex-col">
          {view === 'reader' && (
            <div className="flex-1 overflow-y-auto p-6">
              {!activePath && (
                <div className="text-sm text-[#7a8599] italic">
                  Select a page from the tree on the left.
                </div>
              )}
              {activePath && fileQ.isLoading && <Skeleton className="h-64" />}
              {activePath && fileQ.isError && (
                <ErrorPanel error={fileQ.error} onRetry={() => fileQ.refetch()} />
              )}
              {activePath && fileQ.data && (
                <article>
                  <header className="mb-4 pb-3 border-b border-[#1e2738]">
                    <h1 className="text-xl font-semibold text-[#e2e8f0]">{fileQ.data.title}</h1>
                    <div className="flex flex-wrap items-center gap-3 mt-2 text-xs text-[#7a8599]">
                      <span>{fileQ.data.path}</span>
                      {fileQ.data.tags.length > 0 && (
                        <span>
                          {fileQ.data.tags.map((t) => (
                            <span
                              key={t}
                              className="ml-1 px-1.5 py-0.5 rounded bg-[#a78bfa]/10 text-[#a78bfa]"
                            >
                              #{t}
                            </span>
                          ))}
                        </span>
                      )}
                      <span>updated {new Date(fileQ.data.updated_at).toLocaleDateString()}</span>
                    </div>
                  </header>
                  <NoteRenderer
                    body={fileQ.data.body}
                    root={ROOT}
                    currentPath={fileQ.data.path}
                    onNavigate={(target) => {
                      const guess = target.endsWith('.md') ? target : `${target}.md`;
                      setActivePath(guess);
                    }}
                  />
                  {(fileQ.data.backlinks.length > 0 || fileQ.data.forward_links.length > 0) && (
                    <footer className="mt-6 pt-4 border-t border-[#1e2738] grid grid-cols-1 md:grid-cols-2 gap-4">
                      {fileQ.data.forward_links.length > 0 && (
                        <div>
                          <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider mb-2">
                            Outgoing
                          </h3>
                          <ul className="space-y-1">
                            {fileQ.data.forward_links.map((l) => (
                              <li key={l.path}>
                                <button
                                  onClick={() => setActivePath(l.path)}
                                  className="text-xs text-[#60a5fa] hover:underline"
                                >
                                  {l.title}
                                </button>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {fileQ.data.backlinks.length > 0 && (
                        <div>
                          <h3 className="text-xs font-semibold text-[#7a8599] uppercase tracking-wider mb-2">
                            Backlinks
                          </h3>
                          <ul className="space-y-1">
                            {fileQ.data.backlinks.map((l) => (
                              <li key={l.path}>
                                <button
                                  onClick={() => setActivePath(l.path)}
                                  className="text-xs text-[#60a5fa] hover:underline"
                                >
                                  {l.title}
                                </button>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </footer>
                  )}
                </article>
              )}
            </div>
          )}
          {view === 'graph' && (
            <div className="flex-1 min-h-0">
              {graphQ.isLoading && <Skeleton className="h-64" />}
              {graphQ.isError && (
                <ErrorPanel error={graphQ.error} onRetry={() => graphQ.refetch()} />
              )}
              {graphQ.data && (
                <NoteGraph
                  nodes={graphQ.data.nodes}
                  edges={graphQ.data.edges}
                  activePath={activePath}
                  onSelect={(p) => {
                    setActivePath(p);
                    setView('reader');
                  }}
                />
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

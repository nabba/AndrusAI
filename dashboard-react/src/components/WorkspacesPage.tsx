import { useState } from 'react';
import type { WorkspaceItem } from '../types';
import { Skeleton } from './ui/Skeleton';
import { ErrorPanel } from './ui/ErrorPanel';
import {
  useWorkspacesQuery,
  useWorkspaceItemsQuery,
  useWorkspacesMetaQuery,
  useCreateWorkspace,
} from '../api/queries';

/* ── Consciousness Workspaces ─────────────────────────────────────────────
   Per-project workspaces (competitive gating) + global meta-workspace
   (cross-project insights). Read-only: the AI controls what enters the
   workspace, not the user.
   ──────────────────────────────────────────────────────────────────────── */

function SalienceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    value > 0.7 ? 'bg-[#34d399]' : value > 0.4 ? 'bg-[#60a5fa]' : 'bg-[#7a8599]';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-[#1e2738] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-[#7a8599] w-8 text-right">{pct}%</span>
    </div>
  );
}

function ItemCard({ item }: { item: WorkspaceItem }) {
  return (
    <div className={`bg-[#0a0e14] border rounded-lg p-3 ${item.consumed ? 'border-[#7a8599]/30 opacity-60' : 'border-[#1e2738]'}`}>
      <p className="text-sm text-[#e2e8f0] leading-snug mb-2">{item.content.slice(0, 120)}</p>
      <SalienceBar value={item.salience ?? 0} />
      <div className="flex items-center justify-between mt-2 text-[10px] text-[#7a8599]">
        <span>{item.source_agent ?? ''} / {item.source_channel ?? ''}</span>
        {item.cycles != null && <span>cycle {item.cycles}</span>}
      </div>
    </div>
  );
}

function CreateWorkspaceModal({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const create = useCreateWorkspace();

  const handleCreate = async () => {
    if (!name.trim()) return;
    try {
      // Capacity is auto-tuned per crew run from personality + homeostasis
      // (see subia/scene/personality_workspace.py). Any seed value is
      // overwritten the first time a crew touches the workspace, so we let
      // the backend default (3) stand — no need to ask the user.
      await create.mutateAsync({ project_id: name.trim().toLowerCase(), capacity: 3 });
      onClose();
    } catch {
      // surfaced via create.error
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-[#e2e8f0] mb-2">Create Workspace</h2>
        <p className="text-xs text-[#7a8599] mb-4">
          Capacity (how many thoughts compete for attention) is auto-tuned per cycle from the agent's focus,
          developmental stage, and homeostasis — no need to set it manually. The initial seed is overwritten
          on the first crew run.
        </p>
        <label className="block text-sm text-[#7a8599] mb-1">Name</label>
        <input
          className="w-full bg-[#0a0e14] border border-[#1e2738] rounded-lg px-3 py-2 text-[#e2e8f0] text-sm mb-4 focus:outline-none focus:border-[#60a5fa]"
          placeholder="e.g. new-venture"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter' && name.trim() && !create.isPending) {
              e.preventDefault();
              void handleCreate();
            }
          }}
        />
        {create.error && (
          <p className="text-sm text-[#f87171] mb-3">{(create.error as Error).message}</p>
        )}
        <div className="flex gap-3 justify-end">
          <button onClick={onClose} className="px-4 py-2 text-sm text-[#7a8599] hover:text-[#e2e8f0]">Cancel</button>
          <button
            onClick={handleCreate}
            disabled={create.isPending || !name.trim()}
            className="px-4 py-2 text-sm bg-[#60a5fa] text-[#0a0e14] rounded-lg font-medium hover:bg-[#60a5fa]/80 disabled:opacity-50"
          >
            {create.isPending ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

function WorkspaceBoard({ projectId }: { projectId: string }) {
  const { data, isLoading, error, refetch } = useWorkspaceItemsQuery(projectId);

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-4 mt-4">
        {[0, 1].map((i) => (
          <div key={i} className="space-y-3">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        ))}
      </div>
    );
  }

  if (error) return <div className="mt-4"><ErrorPanel error={error} onRetry={refetch} /></div>;
  if (!data) return null;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
      <div>
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-medium text-[#34d399]">Active</span>
          <span className="text-xs text-[#7a8599] bg-[#182030] px-2 py-0.5 rounded-full">
            {data.active?.length ?? 0} / {data.capacity}
          </span>
        </div>
        <div className="space-y-2">
          {data.active?.length ? (
            data.active.map((item) => <ItemCard key={item.item_id} item={item} />)
          ) : (
            <p className="text-xs text-[#7a8599] italic">No active items</p>
          )}
        </div>
      </div>

      <div>
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-medium text-[#7a8599]">Peripheral</span>
          <span className="text-xs text-[#7a8599] bg-[#182030] px-2 py-0.5 rounded-full">
            {data.peripheral?.length ?? 0}
          </span>
        </div>
        <div className="space-y-2">
          {data.peripheral?.length ? (
            data.peripheral.map((item) => <ItemCard key={item.item_id} item={item} />)
          ) : (
            <p className="text-xs text-[#7a8599] italic">No peripheral items</p>
          )}
        </div>
      </div>
    </div>
  );
}

function MetaView() {
  const { data } = useWorkspacesMetaQuery();
  if (!data || !data.by_project || Object.keys(data.by_project).length === 0) {
    return (
      <div className="mt-6 p-4 bg-[#111820] border border-[#1e2738] rounded-xl">
        <h3 className="text-sm font-medium text-[#a78bfa] mb-2">Meta-Workspace (Cross-Project)</h3>
        <p className="text-xs text-[#7a8599]">No cross-project items yet</p>
      </div>
    );
  }

  return (
    <div className="mt-6 p-4 bg-[#111820] border border-[#1e2738] rounded-xl">
      <h3 className="text-sm font-medium text-[#a78bfa] mb-3">
        Meta-Workspace (Cross-Project) — {data.project_count} workspaces
      </h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {Object.entries(data.by_project).map(([project, items]) => (
          <div key={project} className="bg-[#0a0e14] border border-[#1e2738] rounded-lg p-3">
            <span className="text-xs font-medium text-[#60a5fa] uppercase">{project}</span>
            {(items as { content: string; salience: number }[]).map((item, i) => (
              <div key={i} className="mt-2">
                <p className="text-xs text-[#e2e8f0]">{item.content.slice(0, 80)}</p>
                <SalienceBar value={item.salience} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function WorkspacesPage() {
  const { data, isLoading, error, refetch } = useWorkspacesQuery();
  const [selected, setSelected] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const workspaces = data?.workspaces ?? [];
  const activeWs = selected ?? workspaces[0]?.project_id ?? null;

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-[#e2e8f0]">Consciousness Workspaces</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-[#60a5fa] text-[#0a0e14] rounded-lg font-medium hover:bg-[#60a5fa]/80"
        >
          + New Workspace
        </button>
      </div>

      {isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : error ? (
        <ErrorPanel error={error} onRetry={refetch} />
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {workspaces.map((ws) => {
              const isActive = ws.project_id === activeWs;
              if (ws.project_id === '__meta__') return null;
              return (
                <button
                  key={ws.project_id}
                  onClick={() => setSelected(ws.project_id)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-[#60a5fa] text-[#0a0e14]'
                      : 'bg-[#111820] text-[#7a8599] border border-[#1e2738] hover:border-[#60a5fa]/40 hover:text-[#e2e8f0]'
                  }`}
                >
                  <span className="capitalize">{ws.project_id}</span>
                  <span className="ml-2 text-xs opacity-70">
                    {ws.active_count}/{ws.capacity}
                  </span>
                </button>
              );
            })}
          </div>

          {activeWs && (
            <div className="bg-[#111820] border border-[#1e2738] rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-base font-semibold text-[#e2e8f0] capitalize">{activeWs}</h2>
                <span className="text-xs text-[#7a8599]">
                  cycle {workspaces.find((w) => w.project_id === activeWs)?.cycle ?? 0}
                </span>
              </div>
              <WorkspaceBoard projectId={activeWs} />
            </div>
          )}

          <MetaView />
        </>
      )}

      {showCreate && <CreateWorkspaceModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}

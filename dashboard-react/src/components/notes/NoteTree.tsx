import { useMemo, useState } from 'react';
import type { NoteTreeNode } from '../../api/queries';

interface Props {
  tree: NoteTreeNode;
  activePath: string | null;
  onSelect: (path: string) => void;
  filter: string;
}

function matchesFilter(node: NoteTreeNode, needle: string): boolean {
  if (!needle) return true;
  if (node.name.toLowerCase().includes(needle)) return true;
  if (node.children) {
    return node.children.some((c) => matchesFilter(c, needle));
  }
  return false;
}

function NodeRow({
  node,
  depth,
  activePath,
  onSelect,
  filter,
  openMap,
  setOpen,
}: Props & {
  node: NoteTreeNode;
  depth: number;
  openMap: Map<string, boolean>;
  setOpen: (path: string, open: boolean) => void;
}) {
  const needle = filter.toLowerCase();
  const visible = matchesFilter(node, needle);
  if (!visible) return null;

  if (node.type === 'dir') {
    // Auto-open while filtering so all matches are visible.
    const open = needle ? true : openMap.get(node.path) ?? depth === 0;
    return (
      <div>
        <button
          type="button"
          onClick={() => setOpen(node.path, !open)}
          className="flex items-center gap-1 w-full text-left px-2 py-1 rounded hover:bg-[#1e2738]/50 text-xs text-[#7a8599]"
          style={{ paddingLeft: `${depth * 10 + 8}px` }}
        >
          <span className="w-3 inline-block">{open ? '▾' : '▸'}</span>
          <span>📁 {node.name}</span>
        </button>
        {open &&
          node.children?.map((child) => (
            <NodeRow
              key={child.path || child.name}
              tree={child}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              onSelect={onSelect}
              filter={filter}
              openMap={openMap}
              setOpen={setOpen}
            />
          ))}
      </div>
    );
  }

  const isActive = node.path === activePath;
  const isAttachment = node.type === 'attachment';
  return (
    <button
      type="button"
      disabled={isAttachment}
      onClick={() => !isAttachment && onSelect(node.path)}
      title={node.path}
      className={`flex items-center gap-1.5 w-full text-left px-2 py-1 rounded text-xs truncate ${
        isActive
          ? 'bg-[#60a5fa]/15 text-[#60a5fa]'
          : isAttachment
          ? 'text-[#7a8599]/60'
          : 'text-[#e2e8f0] hover:bg-[#1e2738]/50'
      }`}
      style={{ paddingLeft: `${depth * 10 + 22}px` }}
    >
      <span>{isAttachment ? '📎' : '📄'}</span>
      <span className="truncate">{node.name}</span>
    </button>
  );
}

export function NoteTree(props: Props) {
  const [openMap, setOpenMap] = useState<Map<string, boolean>>(new Map([['', true]]));
  const setOpen = (path: string, open: boolean) => {
    setOpenMap((prev) => {
      const next = new Map(prev);
      next.set(path, open);
      return next;
    });
  };

  const topLevel = useMemo(() => props.tree.children ?? [], [props.tree]);

  return (
    <div className="space-y-0.5 text-xs">
      {topLevel.length === 0 ? (
        <div className="text-[#7a8599] italic text-xs p-3">Empty folder.</div>
      ) : (
        topLevel.map((child) => (
          <NodeRow
            key={child.path || child.name}
            tree={child}
            node={child}
            depth={0}
            activePath={props.activePath}
            onSelect={props.onSelect}
            filter={props.filter}
            openMap={openMap}
            setOpen={setOpen}
          />
        ))
      )}
    </div>
  );
}

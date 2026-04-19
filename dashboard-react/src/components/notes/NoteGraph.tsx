import { useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import type { NoteGraphNode, NoteGraphEdge } from '../../api/queries';

interface Props {
  nodes: NoteGraphNode[];
  edges: NoteGraphEdge[];
  activePath: string | null;
  onSelect: (path: string) => void;
}

interface FgNode extends NoteGraphNode {
  x?: number;
  y?: number;
}

// Generate a stable color per folder group.
function colorFor(group: string): string {
  let hash = 0;
  for (const c of group) hash = (hash * 31 + c.charCodeAt(0)) | 0;
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 60%, 60%)`;
}

export function NoteGraph({ nodes, edges, activePath, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [dims, setDims] = useState<{ w: number; h: number }>({ w: 800, h: 520 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setDims({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    const nodeIds = new Set(nodes.map((n) => n.id));
    return {
      nodes: nodes.map((n) => ({ ...n, color: colorFor(n.group) })),
      links: edges
        .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target)
        .map((e) => ({ source: e.source, target: e.target })),
    };
  }, [nodes, edges]);

  return (
    <div ref={containerRef} className="w-full h-[560px] bg-[#0a0e14] rounded-lg border border-[#1e2738] overflow-hidden relative">
      <ForceGraph2D
        width={dims.w}
        height={dims.h}
        graphData={data}
        backgroundColor="#0a0e14"
        linkColor={() => 'rgba(122, 133, 153, 0.35)'}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={0.9}
        nodeRelSize={3}
        nodeVal={(n: FgNode) => 1 + Math.min(5, (n.tags?.length ?? 0))}
        onNodeClick={(n: FgNode) => onSelect(n.id)}
        nodeCanvasObjectMode={() => 'after'}
        nodeCanvasObject={(n: FgNode, ctx, globalScale) => {
          const active = n.id === activePath;
          const fontSize = 11 / globalScale;
          ctx.font = `${fontSize}px system-ui, sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillStyle = active ? '#60a5fa' : '#7a8599';
          if (globalScale > 1.3 || active) {
            const label = n.label.length > 24 ? n.label.slice(0, 24) + '…' : n.label;
            ctx.fillText(label, n.x ?? 0, (n.y ?? 0) + 7);
          }
          if (active) {
            ctx.strokeStyle = '#60a5fa';
            ctx.lineWidth = 1.5 / globalScale;
            ctx.beginPath();
            ctx.arc(n.x ?? 0, n.y ?? 0, 6 / globalScale + 2, 0, 2 * Math.PI);
            ctx.stroke();
          }
        }}
      />
      <div className="absolute bottom-2 left-2 text-[10px] text-[#7a8599]">
        {nodes.length} nodes · {edges.length} edges — click to open
      </div>
    </div>
  );
}

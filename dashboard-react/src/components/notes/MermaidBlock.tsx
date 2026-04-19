import { useEffect, useRef, useState } from 'react';

// Lazy-loaded Mermaid renderer — only pulled in when a `mermaid` code block
// actually appears on the page. Keeps the notes bundle slim for text-only pages.

let mermaidModule: Promise<typeof import('mermaid').default> | null = null;

function loadMermaid() {
  if (!mermaidModule) {
    mermaidModule = import('mermaid').then((m) => {
      const mermaid = m.default;
      mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        securityLevel: 'strict',
        darkMode: true,
        fontFamily: 'system-ui, -apple-system, sans-serif',
      });
      return mermaid;
    });
  }
  return mermaidModule;
}

let idCounter = 0;

export function MermaidBlock({ source }: { source: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const id = `mermaid-${++idCounter}`;
    loadMermaid()
      .then(async (mermaid) => {
        try {
          const { svg } = await mermaid.render(id, source);
          if (!cancelled && ref.current) ref.current.innerHTML = svg;
          if (!cancelled) setError(null);
        } catch (err) {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Render failed');
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Mermaid load failed');
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  return (
    <div className="mermaid-block my-3">
      {error ? (
        <pre className="bg-[#0a0e14] border border-[#f87171]/30 rounded p-3 text-xs text-[#f87171] overflow-x-auto">
          {`mermaid error: ${error}\n\n${source}`}
        </pre>
      ) : (
        <div ref={ref} className="flex justify-center" />
      )}
    </div>
  );
}

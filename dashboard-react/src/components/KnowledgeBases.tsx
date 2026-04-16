import { useState, useRef, useCallback } from 'react';
import { useApi } from '../hooks/useApi';

// ── Types ────────────────────────────────────────────────────────────────────

interface KBStats {
  collection_name: string;
  total_chunks: number;
  total_texts?: number;
  total_documents?: number;
  total_entries?: number;
  total_patterns?: number;
  total_tensions?: number;
  traditions?: string[];
  authors?: string[];
  titles?: string[];
  paper_types?: string[];
  entry_types?: string[];
  pattern_types?: string[];
  tension_types?: string[];
  resolution_statuses?: Record<string, number>;
  persist_dir?: string;
}

// ── Colors ───────────────────────────────────────────────────────────────────

const KB_CONFIGS = {
  enterprise: { label: 'Knowledge Base', icon: '📄', color: '#60a5fa', prefix: '/kb', uploadType: 'file', desc: 'User documents — factual knowledge.' },
  philosophy: { label: 'Philosophy', icon: '🏛️', color: '#c084fc', prefix: '/philosophy', uploadType: 'file', desc: 'Humanist texts — ethical grounding. Read-only for agents.' },
  literature: { label: 'Literature', icon: '📚', color: '#fb923c', prefix: '/fiction', uploadType: 'file', desc: 'Fiction, poetry, mythology — creative inspiration. Never factual.' },
  episteme:   { label: 'Research (Episteme)', icon: '🔬', color: '#22d3ee', prefix: '/episteme', uploadType: 'file', desc: 'Research papers, architecture decisions, design patterns.' },
  experiential: { label: 'Journal', icon: '📓', color: '#34d399', prefix: '/experiential', uploadType: 'text', desc: 'Reflected experiences — subjective, phenomenological.' },
  aesthetics: { label: 'Aesthetics', icon: '🎨', color: '#a78bfa', prefix: '/aesthetics', uploadType: 'text', desc: 'Elegant code, beautiful prose — quality patterns.' },
  tensions:   { label: 'Tensions', icon: '⚡', color: '#fbbf24', prefix: '/tensions', uploadType: 'form', desc: 'Contradictions, competing values — growth edges.' },
} as const;

type KBType = keyof typeof KB_CONFIGS;

// ── Upload form components ──────────────────────────────────────────────────

function FileUploadZone({ kbType, color, onUploadDone }: { kbType: KBType; color: string; onUploadDone: () => void }) {
  const [status, setStatus] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const cfg = KB_CONFIGS[kbType];

  const upload = useCallback(async (file: File) => {
    if (file.size > 50 * 1024 * 1024) { setStatus('File too large (max 50MB)'); return; }
    setStatus('Uploading...');
    const fd = new FormData();
    fd.append('file', file);

    try {
      const res = await fetch(`${cfg.prefix}/upload`, { method: 'POST', body: fd });
      const j = await res.json();
      setStatus(j.chunks_created ? `${j.chunks_created} chunks ingested` : 'Uploaded');
      setTimeout(() => setStatus(''), 5000);
      onUploadDone();
    } catch {
      setStatus('Upload failed');
    }
  }, [cfg.prefix, onUploadDone]);

  return (
    <div
      className={`border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-all ${dragOver ? 'border-opacity-100' : 'border-opacity-40'}`}
      style={{ borderColor: color }}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={e => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); }}
    >
      <input ref={inputRef} type="file" accept=".md,.txt,.pdf,.docx" className="hidden" onChange={e => { if (e.target.files?.[0]) upload(e.target.files[0]); }} />
      <p className="text-xs text-[#7a8599]">Drop files here or <span style={{ color }} className="font-medium">click to upload</span></p>
      {status && <p className="text-xs mt-2" style={{ color: status.includes('fail') ? '#f87171' : '#34d399' }}>{status}</p>}
    </div>
  );
}

function TextUploadForm({ kbType, color, onUploadDone }: { kbType: KBType; color: string; onUploadDone: () => void }) {
  const [text, setText] = useState('');
  const [status, setStatus] = useState('');
  const cfg = KB_CONFIGS[kbType];

  const submit = useCallback(async () => {
    if (!text.trim()) return;
    setStatus('Submitting...');
    const fd = new FormData();
    fd.append('text', text.trim());
    if (kbType === 'experiential') {
      fd.append('entry_type', 'interaction_narrative');
      fd.append('agent', 'user');
      fd.append('emotional_valence', 'neutral');
    } else if (kbType === 'aesthetics') {
      fd.append('pattern_type', 'creative_solution');
      fd.append('domain', 'general');
      fd.append('quality_score', '0.8');
      fd.append('flagged_by', 'user');
    }
    try {
      const res = await fetch(`${cfg.prefix}/upload`, { method: 'POST', body: fd });
      const j = await res.json();
      if (j.status === 'ok') { setText(''); setStatus('Saved'); onUploadDone(); }
      else setStatus('Failed');
      setTimeout(() => setStatus(''), 3000);
    } catch { setStatus('Failed'); }
  }, [text, kbType, cfg.prefix, onUploadDone]);

  return (
    <div>
      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder={kbType === 'experiential' ? 'Write a journal entry or reflection...' : 'Paste an example of elegant code, beautiful prose, or a well-structured argument...'}
        className="w-full min-h-[80px] bg-[#0a0e14] text-[#e2e8f0] border border-[#1e2738] rounded-lg p-3 text-xs resize-y font-mono"
      />
      <div className="flex items-center gap-2 mt-2">
        <button onClick={submit} className="px-3 py-1.5 rounded text-xs font-medium transition-colors" style={{ backgroundColor: `${color}20`, borderColor: `${color}40`, color, border: '1px solid' }}>
          Submit
        </button>
        {status && <span className="text-xs" style={{ color: status === 'Failed' ? '#f87171' : '#34d399' }}>{status}</span>}
      </div>
    </div>
  );
}

function TensionUploadForm({ color, onUploadDone }: { color: string; onUploadDone: () => void }) {
  const [poleA, setPoleA] = useState('');
  const [poleB, setPoleB] = useState('');
  const [context, setContext] = useState('');
  const [status, setStatus] = useState('');

  const submit = useCallback(async () => {
    if (!poleA.trim() || !poleB.trim()) return;
    setStatus('Submitting...');
    const fd = new FormData();
    fd.append('pole_a', poleA.trim());
    fd.append('pole_b', poleB.trim());
    fd.append('tension_type', 'unresolved_question');
    fd.append('context', context.trim());
    fd.append('detected_by', 'user');
    try {
      const res = await fetch('/tensions/upload', { method: 'POST', body: fd });
      const j = await res.json();
      if (j.status === 'ok') { setPoleA(''); setPoleB(''); setContext(''); setStatus('Recorded'); onUploadDone(); }
      else setStatus('Failed');
      setTimeout(() => setStatus(''), 3000);
    } catch { setStatus('Failed'); }
  }, [poleA, poleB, context, onUploadDone]);

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <input value={poleA} onChange={e => setPoleA(e.target.value)} placeholder="Pole A (one side)" className="bg-[#0a0e14] text-[#e2e8f0] border border-[#1e2738] rounded px-2 py-1.5 text-xs" />
        <input value={poleB} onChange={e => setPoleB(e.target.value)} placeholder="Pole B (other side)" className="bg-[#0a0e14] text-[#e2e8f0] border border-[#1e2738] rounded px-2 py-1.5 text-xs" />
      </div>
      <input value={context} onChange={e => setContext(e.target.value)} placeholder="Context: what revealed this tension?" className="w-full bg-[#0a0e14] text-[#e2e8f0] border border-[#1e2738] rounded px-2 py-1.5 text-xs" />
      <div className="flex items-center gap-2">
        <button onClick={submit} className="px-3 py-1.5 rounded text-xs font-medium transition-colors" style={{ backgroundColor: `${color}20`, borderColor: `${color}40`, color, border: '1px solid' }}>
          Record Tension
        </button>
        {status && <span className="text-xs" style={{ color: status === 'Failed' ? '#f87171' : '#34d399' }}>{status}</span>}
      </div>
    </div>
  );
}

// ── KB Card ─────────────────────────────────────────────────────────────────

function KBCard({ kbType }: { kbType: KBType }) {
  const cfg = KB_CONFIGS[kbType];
  const statsPath = kbType === 'enterprise' ? '/kb/status' : kbType === 'literature' ? '/fiction/status' : `${cfg.prefix}/stats`;
  const { data: stats, refetch } = useApi<KBStats>(statsPath, 30000);

  const total = stats?.total_chunks ?? stats?.total_documents ?? stats?.total_entries ?? stats?.total_patterns ?? stats?.total_tensions ?? 0;
  const textCount = stats?.total_texts ?? stats?.total_documents ?? 0;

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{cfg.icon}</span>
          <h3 className="text-sm font-semibold text-[#e2e8f0]">{cfg.label}</h3>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={{ backgroundColor: `${cfg.color}15`, color: cfg.color }}>
            {total} chunks
          </span>
          {textCount > 0 && (
            <span className="text-xs text-[#7a8599]">{textCount} docs</span>
          )}
        </div>
      </div>

      {/* Description */}
      <p className="text-xs text-[#7a8599] mb-3 leading-relaxed">{cfg.desc}</p>

      {/* Stats chips */}
      {stats && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {stats.traditions?.map(t => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.paper_types?.map(t => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.pattern_types?.map(t => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.tension_types?.map(t => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.entry_types?.map(t => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.authors?.slice(0, 8).map(a => <Chip key={a} label={a} color="#7a8599" />)}
        </div>
      )}

      {/* Resolution status for tensions */}
      {stats?.resolution_statuses && (
        <div className="flex gap-2 mb-3">
          {Object.entries(stats.resolution_statuses).map(([s, n]) => (
            <span key={s} className="text-xs text-[#7a8599]">{s}: <strong className="text-[#e2e8f0]">{n}</strong></span>
          ))}
        </div>
      )}

      {/* Upload area */}
      <div className="mt-3 pt-3 border-t border-[#1e2738]">
        {cfg.uploadType === 'file' && (
          <FileUploadZone kbType={kbType} color={cfg.color} onUploadDone={refetch} />
        )}
        {cfg.uploadType === 'text' && (
          <TextUploadForm kbType={kbType} color={cfg.color} onUploadDone={refetch} />
        )}
        {cfg.uploadType === 'form' && (
          <TensionUploadForm color={cfg.color} onUploadDone={refetch} />
        )}
      </div>
    </div>
  );
}

function Chip({ label, color }: { label: string; color: string }) {
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded border" style={{ borderColor: `${color}30`, color, backgroundColor: `${color}08` }}>
      {label}
    </span>
  );
}

// ── Main Page ───────────────────────────────────────────────────────────────

export function KnowledgeBases() {
  const [filter, setFilter] = useState<'all' | 'factual' | 'creative' | 'growth'>('all');

  const factual: KBType[] = ['enterprise', 'episteme'];
  const creative: KBType[] = ['philosophy', 'literature', 'aesthetics'];
  const growth: KBType[] = ['experiential', 'tensions'];

  const visible = filter === 'all'
    ? [...factual, ...creative, ...growth]
    : filter === 'factual' ? factual
    : filter === 'creative' ? creative
    : growth;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold text-[#e2e8f0]">Knowledge Bases</h1>
          <p className="text-xs text-[#7a8599] mt-1">7 epistemically separated knowledge bases — each serves a different cognitive function</p>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-2 mb-4">
        {(['all', 'factual', 'creative', 'growth'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              filter === f
                ? 'bg-[#60a5fa]/10 text-[#60a5fa] border border-[#60a5fa]/20'
                : 'text-[#7a8599] hover:text-[#e2e8f0] hover:bg-[#1e2738] border border-transparent'
            }`}
          >
            {f === 'all' ? 'All (7)' : f === 'factual' ? '📄 Factual' : f === 'creative' ? '🎨 Creative' : '🌱 Growth'}
          </button>
        ))}
      </div>

      {/* KB grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {visible.map(kb => <KBCard key={kb} kbType={kb} />)}
      </div>

      {/* Legend */}
      <div className="mt-6 p-4 bg-[#111820] border border-[#1e2738] rounded-lg">
        <h3 className="text-xs font-semibold text-[#7a8599] mb-2">Epistemic Architecture</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[10px] text-[#7a8599]">
          <div>
            <span className="text-[#60a5fa] font-medium">Factual</span> — Enterprise KB + Episteme: verified knowledge, grounded in evidence
          </div>
          <div>
            <span className="text-[#c084fc] font-medium">Creative</span> — Philosophy + Literature + Aesthetics: values, imagination, taste
          </div>
          <div>
            <span className="text-[#fbbf24] font-medium">Growth</span> — Journal + Tensions: experiential memory, unresolved contradictions
          </div>
        </div>
      </div>
    </div>
  );
}

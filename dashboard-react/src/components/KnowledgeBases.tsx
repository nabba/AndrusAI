import { useState, useRef, useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { endpoints } from '../api/endpoints';
import { useKbBusinessesQuery, type BusinessKB, keys } from '../api/queries';
import { ErrorPanel } from './ui/ErrorPanel';
import { KBDocumentList } from './KBDocumentList';

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

// ── KB config ────────────────────────────────────────────────────────────────

type UploadKind = 'file' | 'text' | 'form';

interface KBConfig {
  label: string;
  icon: string;
  color: string;
  statsPath: string;
  uploadPath: string;
  uploadType: UploadKind;
  desc: string;
}

const KB_CONFIGS = {
  enterprise: {
    label: 'Knowledge Base',
    icon: '📄',
    color: '#60a5fa',
    statsPath: endpoints.kbStatus(),
    uploadPath: endpoints.kbUpload(),
    uploadType: 'file',
    desc: 'User documents — factual knowledge.',
  },
  philosophy: {
    label: 'Philosophy',
    icon: '🏛️',
    color: '#c084fc',
    statsPath: endpoints.philosophyStats(),
    uploadPath: endpoints.philosophyUpload(),
    uploadType: 'file',
    desc: 'Humanist texts — ethical grounding. Read-only for agents.',
  },
  literature: {
    label: 'Literature',
    icon: '📚',
    color: '#fb923c',
    statsPath: endpoints.fictionStatus(),
    uploadPath: endpoints.fictionUpload(),
    uploadType: 'file',
    desc: 'Fiction, poetry, mythology — creative inspiration. Never factual.',
  },
  episteme: {
    label: 'Research (Episteme)',
    icon: '🔬',
    color: '#22d3ee',
    statsPath: endpoints.epistemeStats(),
    uploadPath: endpoints.epistemeUpload(),
    uploadType: 'file',
    desc: 'Research papers, architecture decisions, design patterns.',
  },
  experiential: {
    label: 'Journal',
    icon: '📓',
    color: '#34d399',
    statsPath: endpoints.experientialStats(),
    uploadPath: endpoints.experientialUpload(),
    uploadType: 'text',
    desc: 'Reflected experiences — subjective, phenomenological.',
  },
  aesthetics: {
    label: 'Aesthetics',
    icon: '🎨',
    color: '#a78bfa',
    statsPath: endpoints.aestheticsStats(),
    uploadPath: endpoints.aestheticsUpload(),
    uploadType: 'text',
    desc: 'Elegant code, beautiful prose — quality patterns.',
  },
  tensions: {
    label: 'Tensions',
    icon: '⚡',
    color: '#fbbf24',
    statsPath: endpoints.tensionsStats(),
    uploadPath: endpoints.tensionsUpload(),
    uploadType: 'form',
    desc: 'Contradictions, competing values — growth edges.',
  },
} as const satisfies Record<string, KBConfig>;

type KBType = keyof typeof KB_CONFIGS;

function useKBStatsQuery(kbType: KBType) {
  return useQuery({
    queryKey: keys.kbStats(kbType),
    queryFn: () => api<KBStats>(KB_CONFIGS[kbType].statsPath),
    refetchInterval: 30_000,
  });
}

// ── Upload form components ──────────────────────────────────────────────────

/**
 * Custom error class for upload failures. Carries the HTTP status and,
 * on 409 Conflict, the parsed duplicate-detection detail so the
 * caller can show a "replace?" prompt instead of a raw error string.
 */
class UploadError extends Error {
  status: number;
  detail: Record<string, unknown> | null;
  constructor(message: string, status: number, detail: Record<string, unknown> | null) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function uploadFormData(path: string, body: FormData): Promise<unknown> {
  return fetch(path, { method: 'POST', body }).then(async (res) => {
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      let detail: Record<string, unknown> | null = null;
      try {
        const parsed = JSON.parse(text);
        detail = (parsed?.detail ?? parsed) as Record<string, unknown>;
      } catch {
        // non-JSON body — keep detail null
      }
      throw new UploadError(`Upload failed: ${res.status} ${text}`, res.status, detail);
    }
    return res.json();
  });
}

function FileUploadZone({ kbType, color, onUploadDone }: { kbType: KBType; color: string; onUploadDone: () => void }) {
  const [status, setStatus] = useState('');
  const [dragOver, setDragOver] = useState(false);
  /** Pending duplicate state: when set, user is prompted to confirm overwrite. */
  const [duplicate, setDuplicate] = useState<{
    files: File[];
    matched_by?: string;
    existing_filename?: string;
    added_at?: string | null;
  } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const cfg = KB_CONFIGS[kbType];

  const doUpload = useCallback(async (files: File[], overwrite: boolean) => {
    setStatus(`Uploading ${files.length} file${files.length > 1 ? 's' : ''}${overwrite ? ' (replacing)' : ''}...`);
    const fd = new FormData();
    for (const f of files) fd.append('file', f);
    if (overwrite) fd.append('overwrite', 'true');

    try {
      const j = await uploadFormData(cfg.uploadPath, fd) as {
        chunks_created?: number;
        total_chunks?: number;
        files_processed?: number;
        errors?: number;
      };
      if (files.length === 1) {
        setStatus(j.chunks_created ? `${j.chunks_created} chunks ingested${overwrite ? ' (replaced)' : ''}` : 'Uploaded');
      } else {
        const chunks = j.total_chunks ?? j.chunks_created ?? 0;
        const errCount = j.errors ?? 0;
        setStatus(`${j.files_processed ?? files.length} files → ${chunks} chunks${errCount ? ` (${errCount} failed)` : ''}`);
      }
      setDuplicate(null);
      setTimeout(() => setStatus(''), 5000);
      onUploadDone();
    } catch (err) {
      // Duplicate-detected → surface a confirm prompt instead of raw error.
      if (err instanceof UploadError && err.status === 409 && err.detail) {
        const d = err.detail as Record<string, unknown>;
        setDuplicate({
          files,
          matched_by: typeof d.matched_by === 'string' ? d.matched_by : 'filename',
          existing_filename: typeof d.existing_filename === 'string' ? d.existing_filename : undefined,
          added_at: typeof d.added_at === 'string' ? d.added_at : null,
        });
        setStatus('');
        return;
      }
      setStatus(err instanceof Error ? err.message : 'Upload failed');
    }
  }, [cfg.uploadPath, onUploadDone]);

  const upload = useCallback(async (files: FileList | File[]) => {
    const fileArr = Array.from(files);
    if (fileArr.length === 0) return;

    const oversized = fileArr.filter((f) => f.size > 50 * 1024 * 1024);
    if (oversized.length) {
      setStatus(`${oversized.length} file(s) too large (max 50MB)`);
      return;
    }
    await doUpload(fileArr, false);
  }, [doUpload]);

  return (
    <div>
      <div
        className={`border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-all ${dragOver ? 'border-opacity-100' : 'border-opacity-40'}`}
        style={{ borderColor: color }}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) void upload(e.dataTransfer.files); }}
      >
        <input ref={inputRef} type="file" accept=".md,.txt,.pdf,.docx" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) void upload(e.target.files); }} />
        <p className="text-xs text-[#7a8599]">Drop files here or <span style={{ color }} className="font-medium">click to upload</span></p>
        {status && <p className="text-xs mt-2" style={{ color: status.toLowerCase().includes('fail') ? '#f87171' : '#34d399' }}>{status}</p>}
      </div>

      {/* Duplicate-detected confirm prompt (2026-04-26 dedup feature). */}
      {duplicate && (
        <div
          className="mt-2 p-3 rounded-lg border text-xs"
          style={{ borderColor: '#f59e0b40', backgroundColor: '#f59e0b10' }}
        >
          <div className="font-semibold mb-1" style={{ color: '#fbbf24' }}>
            Already in this knowledge base
          </div>
          <div className="text-[#d1d5db] mb-2">
            <strong>{duplicate.existing_filename}</strong>
            {duplicate.added_at && (
              <span className="text-[#9ca3af]"> · added {new Date(duplicate.added_at).toLocaleDateString()}</span>
            )}
            <span className="text-[#9ca3af]"> · matched by {duplicate.matched_by}</span>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="px-3 py-1 rounded text-xs"
              style={{ background: '#f59e0b', color: '#1f1300', fontWeight: 600 }}
              onClick={() => void doUpload(duplicate.files, true)}
            >
              Replace
            </button>
            <button
              type="button"
              className="px-3 py-1 rounded text-xs"
              style={{ background: 'transparent', border: '1px solid #4b5563', color: '#d1d5db' }}
              onClick={() => setDuplicate(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
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
      const j = await uploadFormData(cfg.uploadPath, fd) as { status?: string };
      if (j.status === 'ok') {
        setText('');
        setStatus('Saved');
        onUploadDone();
      } else {
        setStatus('Failed');
      }
      setTimeout(() => setStatus(''), 3000);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Failed');
    }
  }, [text, kbType, cfg.uploadPath, onUploadDone]);

  return (
    <div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
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
      const j = await uploadFormData(endpoints.tensionsUpload(), fd) as { status?: string };
      if (j.status === 'ok') {
        setPoleA('');
        setPoleB('');
        setContext('');
        setStatus('Recorded');
        onUploadDone();
      } else {
        setStatus('Failed');
      }
      setTimeout(() => setStatus(''), 3000);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Failed');
    }
  }, [poleA, poleB, context, onUploadDone]);

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <input value={poleA} onChange={(e) => setPoleA(e.target.value)} placeholder="Pole A (one side)" className="bg-[#0a0e14] text-[#e2e8f0] border border-[#1e2738] rounded px-2 py-1.5 text-xs" />
        <input value={poleB} onChange={(e) => setPoleB(e.target.value)} placeholder="Pole B (other side)" className="bg-[#0a0e14] text-[#e2e8f0] border border-[#1e2738] rounded px-2 py-1.5 text-xs" />
      </div>
      <input value={context} onChange={(e) => setContext(e.target.value)} placeholder="Context: what revealed this tension?" className="w-full bg-[#0a0e14] text-[#e2e8f0] border border-[#1e2738] rounded px-2 py-1.5 text-xs" />
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
  const qc = useQueryClient();
  const { data: stats, error } = useKBStatsQuery(kbType);
  const refetch = () => qc.invalidateQueries({ queryKey: keys.kbStats(kbType) });

  const total = stats?.total_chunks ?? stats?.total_documents ?? stats?.total_entries ?? stats?.total_patterns ?? stats?.total_tensions ?? 0;
  const textCount = stats?.total_texts ?? stats?.total_documents ?? 0;

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{cfg.icon}</span>
          <h3 className="text-sm font-semibold text-[#e2e8f0]">{cfg.label}</h3>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={{ backgroundColor: `${cfg.color}15`, color: cfg.color }}>
            {total} chunks
          </span>
          {textCount > 0 && <span className="text-xs text-[#7a8599]">{textCount} docs</span>}
        </div>
      </div>

      <p className="text-xs text-[#7a8599] mb-3 leading-relaxed">{cfg.desc}</p>

      {error && <div className="mb-3"><ErrorPanel error={error} onRetry={refetch} /></div>}

      {stats && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {stats.traditions?.map((t) => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.paper_types?.map((t) => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.pattern_types?.map((t) => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.tension_types?.map((t) => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.entry_types?.map((t) => <Chip key={t} label={t} color={cfg.color} />)}
          {stats.authors?.slice(0, 8).map((a) => <Chip key={a} label={a} color="#7a8599" />)}
        </div>
      )}

      {stats?.resolution_statuses && (
        <div className="flex gap-2 mb-3">
          {Object.entries(stats.resolution_statuses).map(([s, n]) => (
            <span key={s} className="text-xs text-[#7a8599]">
              {s}: <strong className="text-[#e2e8f0]">{n}</strong>
            </span>
          ))}
        </div>
      )}

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

      {/* Per-document list (collapsed by default). Renders nothing when
          the KB type has no /documents endpoint yet (aesthetics, tensions). */}
      <KBDocumentList kbType={kbType} color={cfg.color} />
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

// ── Business KB Section ─────────────────────────────────────────────────────

function BusinessKBCard({ biz, onRefresh }: { biz: BusinessKB; onRefresh: () => void }) {
  const [status, setStatus] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const color = '#38bdf8';

  const upload = useCallback(async (file: File) => {
    if (file.size > 50 * 1024 * 1024) { setStatus('File too large'); return; }
    setStatus('Uploading...');
    const fd = new FormData();
    fd.append('file', file);
    fd.append('category', 'general');
    try {
      const j = await uploadFormData(endpoints.kbBusinessUpload(biz.business_name), fd) as { chunks_created?: number };
      setStatus(j.chunks_created ? `${j.chunks_created} chunks` : 'Done');
      setTimeout(() => setStatus(''), 4000);
      onRefresh();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Failed');
    }
  }, [biz.business_name, onRefresh]);

  return (
    <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">🏢</span>
          <h3 className="text-sm font-semibold text-[#e2e8f0] capitalize">{biz.business_name}</h3>
        </div>
        <span className="text-xs px-2 py-0.5 rounded-full font-medium" style={{ backgroundColor: `${color}15`, color }}>
          {biz.total_chunks} chunks
        </span>
      </div>
      <p className="text-[10px] text-[#7a8599] mb-2">
        Collection: <code className="text-[#38bdf8]">{biz.collection_name}</code>
        {biz.total_documents ? ` · ${biz.total_documents} docs` : ''}
      </p>

      {biz.categories && Object.keys(biz.categories).length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {Object.entries(biz.categories).map(([cat, n]) => (
            <Chip key={cat} label={`${cat}: ${n}`} color={color} />
          ))}
        </div>
      )}

      <div
        className={`border-2 border-dashed rounded-lg p-3 text-center cursor-pointer transition-all ${dragOver ? 'border-opacity-100' : 'border-opacity-40'}`}
        style={{ borderColor: color }}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files[0]) void upload(e.dataTransfer.files[0]); }}
      >
        <input ref={inputRef} type="file" accept=".md,.txt,.pdf,.docx,.csv,.xlsx,.json,.html" className="hidden" onChange={(e) => { if (e.target.files?.[0]) void upload(e.target.files[0]); }} />
        <p className="text-[10px] text-[#7a8599]">Drop files or <span style={{ color }} className="font-medium">click to upload</span></p>
        {status && <p className="text-[10px] mt-1" style={{ color: status.toLowerCase().includes('fail') || status.includes('large') ? '#f87171' : '#34d399' }}>{status}</p>}
      </div>
    </div>
  );
}

function BusinessKBSection() {
  const qc = useQueryClient();
  const { data } = useKbBusinessesQuery();
  const refetch = () => qc.invalidateQueries({ queryKey: keys.kbBusinesses });
  const businesses = data?.businesses ?? [];

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-base font-semibold text-[#e2e8f0]">🏢 Business Knowledge Bases</h2>
          <p className="text-xs text-[#7a8599] mt-0.5">Per-business isolated KBs — auto-queried when working on that business's tasks</p>
        </div>
        <span className="text-xs text-[#7a8599]">{businesses.length} businesses</span>
      </div>

      {businesses.length === 0 ? (
        <div className="bg-[#111820] border border-[#1e2738] rounded-lg p-6 text-center">
          <p className="text-xs text-[#7a8599]">No business KBs yet — they are created automatically when you create a new business/project.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
          {businesses.map((biz) => (
            <BusinessKBCard key={biz.business_name} biz={biz} onRefresh={refetch} />
          ))}
        </div>
      )}
    </div>
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
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold text-[#e2e8f0]">Knowledge Bases</h1>
          <p className="text-xs text-[#7a8599] mt-1">7 epistemically separated knowledge bases — each serves a different cognitive function</p>
        </div>
      </div>

      <div className="flex gap-2 mb-4">
        {(['all', 'factual', 'creative', 'growth'] as const).map((f) => (
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {visible.map((kb) => <KBCard key={kb} kbType={kb} />)}
      </div>

      <BusinessKBSection />

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

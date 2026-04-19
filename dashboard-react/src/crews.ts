// Canonical crew registry — single source of truth for labels, icons, and the
// user-addressable / internal distinction. Every component that renders a crew
// name reads from here so roster coverage stays consistent.

export type CrewKind = 'user' | 'internal';

export interface CrewMeta {
  name: string;         // backend identifier (matches Firestore + dispatch router)
  label: string;        // human-facing display name
  icon: string;         // emoji
  kind: CrewKind;
  description: string;  // one-line tooltip / caption
}

export const CREW_REGISTRY: readonly CrewMeta[] = [
  // User-addressable (11)
  { name: 'research',       label: 'Research',       icon: '🔬', kind: 'user',     description: 'Web research, data gathering, fact synthesis' },
  { name: 'coding',         label: 'Coding',         icon: '💻', kind: 'user',     description: 'Code generation, debugging, architecture' },
  { name: 'writing',        label: 'Writing',        icon: '✍️', kind: 'user',     description: 'Documents, reports, summaries' },
  { name: 'media',          label: 'Media',          icon: '📸', kind: 'user',     description: 'YouTube, image, audio, video analysis' },
  { name: 'creative',       label: 'Creative',       icon: '🎨', kind: 'user',     description: 'Divergent → discussion → convergence synthesis' },
  { name: 'pim',            label: 'PIM',            icon: '📧', kind: 'user',     description: 'Email, calendar, tasks' },
  { name: 'financial',      label: 'Financial',      icon: '💵', kind: 'user',     description: 'Stock, SEC filings, valuation' },
  { name: 'desktop',        label: 'Desktop',        icon: '🖥️', kind: 'user',     description: 'macOS automation, screenshots' },
  { name: 'repo_analysis',  label: 'Repo Analysis',  icon: '📦', kind: 'user',     description: 'GitHub repos, architecture, tech stack' },
  { name: 'devops',         label: 'DevOps',         icon: '🚢', kind: 'user',     description: 'Scaffolding, CI/CD, deployment' },
  { name: 'tech_radar',     label: 'Tech Radar',     icon: '🔭', kind: 'user',     description: 'Background tech-stack monitoring' },
  // Internal (4)
  { name: 'commander',        label: 'Commander',        icon: '🧭', kind: 'internal', description: 'Top-level orchestrator and router' },
  { name: 'critic',           label: 'Critic',           icon: '🎯', kind: 'internal', description: 'Quality review, vetting, safety checks' },
  { name: 'retrospective',    label: 'Retrospective',    icon: '🔁', kind: 'internal', description: 'System reflection, post-run analysis' },
  { name: 'self_improvement', label: 'Self-Improvement', icon: '🧠', kind: 'internal', description: 'Learning, evolution, skill acquisition' },
];

const BY_NAME = new Map(CREW_REGISTRY.map((c) => [c.name, c]));

const FALLBACK: CrewMeta = {
  name: 'unknown',
  label: 'Unknown',
  icon: '🤖',
  kind: 'internal',
  description: 'Unrecognised crew',
};

export function crewMeta(name: string | undefined | null): CrewMeta {
  if (!name) return FALLBACK;
  const exact = BY_NAME.get(name);
  if (exact) return exact;
  // Tolerate casing / whitespace drift from Firestore / old records.
  const normalized = name.trim().toLowerCase();
  const fuzzy = BY_NAME.get(normalized);
  if (fuzzy) return fuzzy;
  return { ...FALLBACK, name, label: name.replace(/_/g, ' ') };
}

export function crewIcon(name: string | undefined | null): string {
  return crewMeta(name).icon;
}

export function crewLabel(name: string | undefined | null): string {
  return crewMeta(name).label;
}

export const USER_CREWS = CREW_REGISTRY.filter((c) => c.kind === 'user');
export const INTERNAL_CREWS = CREW_REGISTRY.filter((c) => c.kind === 'internal');

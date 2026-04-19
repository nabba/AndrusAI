import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import { endpoints } from './endpoints';
import type {
  Project,
  Ticket,
  Budget,
  AuditEntry,
  GovernanceRequest,
  OrgChartAgent,
  HealthStatus,
  CostEntry,
  AgentCost,
  KanbanBoard,
  WorkspaceList,
  WorkspaceItems,
  MetaWorkspace,
} from '../types';

const POLL = {
  fast: 5_000,
  normal: 10_000,
  slow: 15_000,
  verySlow: 30_000,
  oneMin: 60_000,
} as const;

export const keys = {
  projects: ['projects'] as const,
  tickets: (projectId?: string) => ['tickets', projectId ?? 'all'] as const,
  ticketsBoard: (projectId?: string) => ['tickets', 'board', projectId ?? 'all'] as const,
  budgets: (projectId?: string) => ['budgets', projectId ?? 'all'] as const,
  audit: (limit: number) => ['audit', limit] as const,
  governancePending: ['governance', 'pending'] as const,
  orgChart: ['org-chart'] as const,
  costsDaily: (days: number) => ['costs', 'daily', days] as const,
  costsByAgent: ['costs', 'by-agent'] as const,
  health: ['health'] as const,
  evolutionSummary: ['evolution', 'summary'] as const,
  evolutionResults: (engine: string, status: string) => ['evolution', 'results', engine, status] as const,
  evolutionEngine: ['evolution', 'engine'] as const,
  workspaces: ['workspaces'] as const,
  workspaceItems: (projectId: string) => ['workspaces', projectId, 'items'] as const,
  workspacesMeta: ['workspaces', 'meta'] as const,
  creativeMode: ['creative-mode'] as const,
  kbStats: (kind: string) => ['kb', 'stats', kind] as const,
  kbBusinesses: ['kb', 'businesses'] as const,
  consciousness: (limit: number) => ['consciousness', limit] as const,
  tokens: ['tokens'] as const,
  crewTasks: (limit: number) => ['crew-tasks', limit] as const,
  errors: (limit: number) => ['errors', limit] as const,
  anomalies: (limit: number) => ['anomalies', limit] as const,
  deploys: (limit: number) => ['deploys', limit] as const,
  techRadar: (limit: number) => ['tech-radar', limit] as const,
  llmCatalog: ['llms', 'catalog'] as const,
  llmRoles: ['llms', 'roles'] as const,
  llmDiscovery: (limit: number) => ['llms', 'discovery', limit] as const,
  evolutionVariants: (n: number) => ['evolution', 'variants', n] as const,
  evolutionVariantLineage: (id: string) => ['evolution', 'variant-lineage', id] as const,
  notesRoots: ['notes', 'roots'] as const,
  notesTree: (root: string) => ['notes', 'tree', root] as const,
  notesFile: (root: string, path: string) => ['notes', 'file', root, path] as const,
  notesGraph: (root: string) => ['notes', 'graph', root] as const,
  notesSearch: (root: string, q: string) => ['notes', 'search', root, q] as const,
  notesTags: (root: string) => ['notes', 'tags', root] as const,
};

// ── Projects ────────────────────────────────────────────────────────────────
export function useProjectsQuery() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: () => api<Project[]>(endpoints.projects()),
    refetchInterval: POLL.oneMin,
  });
}

// ── Tickets ─────────────────────────────────────────────────────────────────
export function useTicketsQuery(projectId?: string, interval: number = POLL.verySlow) {
  return useQuery({
    queryKey: keys.tickets(projectId),
    queryFn: () => api<Ticket[]>(endpoints.tickets(projectId)),
    refetchInterval: interval,
  });
}

export function useTicketBoardQuery(projectId?: string) {
  return useQuery({
    queryKey: keys.ticketsBoard(projectId),
    queryFn: () => api<KanbanBoard>(endpoints.ticketsBoard(projectId)),
    refetchInterval: POLL.slow,
  });
}

export function useUpdateTicketStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api<void>(endpoints.ticket(id), { method: 'PUT', body: JSON.stringify({ status }) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tickets'] });
    },
  });
}

export function useAddTicketComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: string }) =>
      api<void>(endpoints.ticketComments(id), { method: 'POST', body: JSON.stringify({ body }) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tickets'] });
    },
  });
}

// ── Budgets ─────────────────────────────────────────────────────────────────
export function useBudgetsQuery(projectId?: string, interval: number = POLL.slow) {
  return useQuery({
    queryKey: keys.budgets(projectId),
    queryFn: () => api<Budget[]>(endpoints.budgets(projectId)),
    refetchInterval: interval,
  });
}

export function useOverrideBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { budget_id: string; new_limit: number; reason?: string }) =>
      api<void>(endpoints.budgetsOverride(), { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['budgets'] });
    },
  });
}

// ── Audit ───────────────────────────────────────────────────────────────────
export function useAuditQuery(limit = 100, projectId?: string, interval: number = POLL.normal) {
  return useQuery({
    queryKey: [...keys.audit(limit), projectId ?? 'all'],
    queryFn: () => api<AuditEntry[]>(endpoints.audit(limit, projectId)),
    refetchInterval: interval,
  });
}

// ── Governance ──────────────────────────────────────────────────────────────
export function useGovernancePendingQuery(projectId?: string, interval: number = POLL.slow) {
  return useQuery({
    queryKey: [...keys.governancePending, projectId ?? 'all'],
    queryFn: () => api<GovernanceRequest[]>(endpoints.governancePending(projectId)),
    refetchInterval: interval,
  });
}

export function useApproveGovernance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<void>(endpoints.governanceApprove(id), { method: 'POST', body: JSON.stringify({}) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['governance'] }),
  });
}

export function useRejectGovernance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      api<void>(endpoints.governanceReject(id), {
        method: 'POST',
        body: JSON.stringify(reason ? { reason } : {}),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['governance'] }),
  });
}

// ── Org chart ───────────────────────────────────────────────────────────────
export function useOrgChartQuery() {
  return useQuery({
    queryKey: keys.orgChart,
    queryFn: () => api<OrgChartAgent[]>(endpoints.orgChart()),
    refetchInterval: POLL.oneMin,
  });
}

// ── Costs ───────────────────────────────────────────────────────────────────
export function useDailyCostsQuery(days = 30, projectId?: string) {
  return useQuery({
    queryKey: [...keys.costsDaily(days), projectId ?? 'all'],
    queryFn: () => api<CostEntry[]>(endpoints.costsDaily(days, projectId)),
    refetchInterval: POLL.oneMin,
  });
}

export function useAgentCostsQuery(projectId?: string) {
  return useQuery({
    queryKey: [...keys.costsByAgent, projectId ?? 'all'],
    queryFn: () => api<{ by_actor: AgentCost[]; total_cost: number }>(endpoints.costsByAgent(projectId)),
    refetchInterval: POLL.oneMin,
  });
}

// ── Health ──────────────────────────────────────────────────────────────────
export function useHealthQuery(interval: number = POLL.verySlow) {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => api<HealthStatus>(endpoints.health()),
    refetchInterval: interval,
  });
}

// ── Evolution ───────────────────────────────────────────────────────────────
export interface EvolutionResult {
  ts: string;
  experiment_id: string;
  hypothesis: string;
  change_type: string;
  status: string;
  delta: number;
  metric_before: number;
  metric_after: number;
  detail: string;
  engine: string;
  files_changed: string[];
}

export interface EngineStat {
  total: number;
  kept: number;
  kept_ratio: number;
}

export interface EvolutionSummary {
  total_experiments: number;
  kept: number;
  discarded: number;
  crashed: number;
  kept_ratio: number;
  best_score: number;
  current_score: number;
  score_trend: number[];
  current_engine: string;
  subia_safety: number;
  engines: Record<string, EngineStat>;
}

export interface EngineInfo {
  config_mode: string;
  selected_engine: string;
  shinka_available: boolean;
}

export function useEvolutionSummaryQuery() {
  return useQuery({
    queryKey: keys.evolutionSummary,
    queryFn: () => api<EvolutionSummary>(endpoints.evolutionSummary()),
    refetchInterval: POLL.slow,
  });
}

export function useEvolutionResultsQuery(engine: string, status: string) {
  return useQuery({
    queryKey: keys.evolutionResults(engine, status),
    queryFn: () =>
      api<{ results: EvolutionResult[] }>(
        endpoints.evolutionResults({ limit: 100, engine: engine || undefined, status: status || undefined }),
      ),
    refetchInterval: POLL.slow,
  });
}

export function useEvolutionEngineQuery() {
  return useQuery({
    queryKey: keys.evolutionEngine,
    queryFn: () => api<EngineInfo>(endpoints.evolutionEngine()),
    refetchInterval: POLL.verySlow,
  });
}

// ── Workspaces ──────────────────────────────────────────────────────────────
export function useWorkspacesQuery() {
  return useQuery({
    queryKey: keys.workspaces,
    queryFn: () => api<WorkspaceList>(endpoints.workspaces()),
    refetchInterval: POLL.normal,
  });
}

export function useWorkspaceItemsQuery(projectId: string | null) {
  return useQuery({
    queryKey: projectId ? keys.workspaceItems(projectId) : ['workspaces', 'items', 'none'],
    queryFn: () => api<WorkspaceItems>(endpoints.workspaceItems(projectId as string)),
    enabled: !!projectId,
    refetchInterval: POLL.normal,
  });
}

export function useWorkspacesMetaQuery() {
  return useQuery({
    queryKey: keys.workspacesMeta,
    queryFn: () => api<MetaWorkspace>(endpoints.workspacesMeta()),
    refetchInterval: POLL.slow,
  });
}

export function useCreateWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_id: string; capacity: number }) =>
      api<void>(endpoints.workspaceCreate(), { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

// ── Creative mode ───────────────────────────────────────────────────────────
export interface CreativeSettings {
  creative_run_budget_usd: number;
  originality_wiki_weight: number;
  mem0_weight: number;
}

export function useCreativeModeQuery() {
  return useQuery({
    queryKey: keys.creativeMode,
    queryFn: () => api<CreativeSettings>(endpoints.creativeMode()),
  });
}

export function useUpdateCreativeMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { creative_run_budget_usd: number; originality_wiki_weight: number }) =>
      api<CreativeSettings & { status: string }>(endpoints.creativeMode(), {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.creativeMode }),
  });
}

// ── KB businesses ───────────────────────────────────────────────────────────
export interface BusinessKB {
  business_name: string;
  collection_name: string;
  total_chunks: number;
  total_documents?: number;
  total_characters?: number;
  categories?: Record<string, number>;
}

// ── Notes viewer ────────────────────────────────────────────────────────────
export interface NoteRoot {
  name: string;
  path: string;
}

export interface NotesRootsReport {
  roots: NoteRoot[];
  default_root?: string | null;
}

export type NoteTreeNodeType = 'dir' | 'note' | 'attachment';

export interface NoteTreeNode {
  name: string;
  path: string;
  type: NoteTreeNodeType;
  size?: number;
  children?: NoteTreeNode[];
}

export interface NotesTreeReport {
  root: string;
  tree: NoteTreeNode;
}

export interface NoteLink {
  path: string;
  title: string;
}

export interface NoteFileReport {
  root: string;
  path: string;
  title: string;
  frontmatter: Record<string, unknown>;
  body: string;
  size: number;
  mtime: number;
  backlinks: NoteLink[];
  forward_links: NoteLink[];
  tags: string[];
  updated_at: string;
}

export interface NoteGraphNode {
  id: string;
  label: string;
  group: string;
  size: number;
  tags: string[];
}

export interface NoteGraphEdge {
  source: string;
  target: string;
}

export interface NotesGraphReport {
  root: string;
  nodes: NoteGraphNode[];
  edges: NoteGraphEdge[];
  tags: string[];
  updated_at: string;
}

export interface NoteSearchHit {
  path: string;
  title: string;
  snippet: string;
  tags: string[];
}

export interface NotesSearchReport {
  query: string;
  hits: NoteSearchHit[];
  total: number;
}

export interface NoteTagEntry {
  tag: string;
  count: number;
  paths: string[];
}

export interface NotesTagsReport {
  root: string;
  tags: NoteTagEntry[];
}

export function useNotesRootsQuery() {
  return useQuery({
    queryKey: keys.notesRoots,
    queryFn: () => api<NotesRootsReport>(endpoints.notesRoots()),
    staleTime: POLL.oneMin,
  });
}

export function useNotesTreeQuery(root: string | null) {
  return useQuery({
    queryKey: keys.notesTree(root ?? ''),
    queryFn: () => api<NotesTreeReport>(endpoints.notesTree(root as string)),
    enabled: !!root,
    staleTime: POLL.verySlow,
  });
}

export function useNoteFileQuery(root: string | null, path: string | null) {
  return useQuery({
    queryKey: keys.notesFile(root ?? '', path ?? ''),
    queryFn: () => api<NoteFileReport>(endpoints.notesFile(root as string, path as string)),
    enabled: !!root && !!path,
    staleTime: POLL.slow,
  });
}

export function useNotesGraphQuery(root: string | null) {
  return useQuery({
    queryKey: keys.notesGraph(root ?? ''),
    queryFn: () => api<NotesGraphReport>(endpoints.notesGraph(root as string)),
    enabled: !!root,
    staleTime: POLL.oneMin,
  });
}

export function useNotesSearchQuery(root: string | null, q: string) {
  return useQuery({
    queryKey: keys.notesSearch(root ?? '', q),
    queryFn: () => api<NotesSearchReport>(endpoints.notesSearch(root as string, q)),
    enabled: !!root && q.trim().length > 0,
    staleTime: POLL.normal,
  });
}

export function useNotesTagsQuery(root: string | null) {
  return useQuery({
    queryKey: keys.notesTags(root ?? ''),
    queryFn: () => api<NotesTagsReport>(endpoints.notesTags(root as string)),
    enabled: !!root,
    staleTime: POLL.oneMin,
  });
}

// ── Ops: errors / anomalies / deploys ───────────────────────────────────────
export interface ErrorEntry {
  ts?: string;
  crew?: string;
  error_type?: string;
  error_msg?: string;
  user_input?: string;
  context?: string;
  diagnosed?: boolean;
  fix_applied?: boolean;
}

export interface ErrorsReport {
  recent: ErrorEntry[];
  patterns: Record<string, number>;
  total_recent: number;
  updated_at: string;
  error?: string | null;
}

export interface AnomalyAlert {
  metric?: string;
  value?: number;
  mean?: number;
  sigma?: number;
  direction?: string;
  type?: string;
  ts?: string;
}

export interface AnomaliesReport {
  recent_alerts: AnomalyAlert[];
  total: number;
  updated_at: string;
  error?: string | null;
}

export interface DeployEntry {
  ts?: string;
  status?: string;               // success | blocked | rollback | auto_rollback
  reason?: string;
  files?: string[];
  error?: string;
  backup_dir?: string;
}

export interface DeploysReport {
  recent: DeployEntry[];
  auto_deploy_enabled?: boolean | null;
  updated_at: string;
  error?: string | null;
}

export function useErrorsQuery() {
  return useQuery({
    queryKey: keys.errors(20),
    queryFn: () => api<ErrorsReport>(endpoints.errors(20)),
    refetchInterval: POLL.slow,
  });
}

export function useAnomaliesQuery() {
  return useQuery({
    queryKey: keys.anomalies(20),
    queryFn: () => api<AnomaliesReport>(endpoints.anomalies(20)),
    refetchInterval: POLL.slow,
  });
}

export function useDeploysQuery() {
  return useQuery({
    queryKey: keys.deploys(20),
    queryFn: () => api<DeploysReport>(endpoints.deploys(20)),
    refetchInterval: POLL.slow,
  });
}

// ── Tech radar ──────────────────────────────────────────────────────────────
export interface TechDiscovery {
  category: string;       // models | frameworks | research | tools | unknown
  title: string;
  summary?: string;
  action?: string;
}

export interface TechRadarReport {
  discoveries: TechDiscovery[];
  updated_at: string;
  error?: string | null;
}

export function useTechRadarQuery() {
  return useQuery({
    queryKey: keys.techRadar(20),
    queryFn: () => api<TechRadarReport>(endpoints.techRadar(20)),
    refetchInterval: POLL.oneMin,
  });
}

// ── LLM catalog / roles / discovery ─────────────────────────────────────────
export interface LlmModel {
  name: string;
  tier?: string;
  provider?: string;
  model_id?: string;
  context?: number;
  multimodal?: boolean;
  cost_input_per_m?: number;
  cost_output_per_m?: number;
  tool_use_reliability?: number;
  supports_tools?: boolean;
  description?: string;
  strengths?: Record<string, number>;
  [key: string]: unknown;
}

export interface LlmCatalogReport {
  models: LlmModel[];
  role_assignments: Record<string, string>;
  cost_mode: string;
  updated_at: string;
  error?: string | null;
}

export interface LlmRoleAssignment {
  role: string;
  cost_mode: string;
  model: string;
  priority?: number;
  source?: string;
  reason?: string;
  assigned_by?: string;
  active?: boolean;
  created_at?: string;
}

export interface LlmRolesReport {
  assignments: LlmRoleAssignment[];
  updated_at: string;
  error?: string | null;
}

export interface DiscoveredModel {
  model_id: string;
  provider?: string;
  display_name?: string;
  context_window?: number;
  cost_input_per_m?: number;
  cost_output_per_m?: number;
  multimodal?: boolean;
  tool_calling?: boolean;
  benchmark_score?: number;
  benchmark_role?: string;
  per_role_scores?: Record<string, number>;
  status?: string;
  promoted_tier?: string;
  promoted_roles?: string[];
  created_at?: string;
  updated_at?: string;
  promoted_at?: string | null;
}

export interface LlmDiscoveryReport {
  discovered: DiscoveredModel[];
  updated_at: string;
  error?: string | null;
}

export function useLlmCatalogQuery() {
  return useQuery({
    queryKey: keys.llmCatalog,
    queryFn: () => api<LlmCatalogReport>(endpoints.llmCatalog()),
    refetchInterval: POLL.oneMin,
  });
}

export function useLlmRolesQuery() {
  return useQuery({
    queryKey: keys.llmRoles,
    queryFn: () => api<LlmRolesReport>(endpoints.llmRoles()),
    refetchInterval: POLL.oneMin,
  });
}

export function useLlmDiscoveryQuery() {
  return useQuery({
    queryKey: keys.llmDiscovery(50),
    queryFn: () => api<LlmDiscoveryReport>(endpoints.llmDiscovery(50)),
    refetchInterval: POLL.oneMin,
  });
}

export function useRunLlmDiscovery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (max_benchmarks: number) =>
      api<{ status: string; result: Record<string, unknown> }>(endpoints.llmDiscoveryRun(), {
        method: 'POST',
        body: JSON.stringify({ max_benchmarks }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['llms'] });
    },
  });
}

// ── Evolution variants / genealogy ──────────────────────────────────────────
export interface Variant {
  id: string;
  parent_id?: string;
  generation?: number;
  hypothesis?: string;
  change_type?: string;
  fitness_before?: number;
  fitness_after?: number;
  delta?: number;
  test_pass_rate?: number;
  status?: string;
  files_changed?: string[];
  mutation_summary?: string;
  timestamp?: string;
}

export interface VariantsReport {
  variants: Variant[];
  drift_score: number;
  error?: string | null;
}

export interface VariantLineageReport {
  lineage: Variant[];
  error?: string | null;
}

export function useEvolutionVariantsQuery(n = 30) {
  return useQuery({
    queryKey: keys.evolutionVariants(n),
    queryFn: () => api<VariantsReport>(endpoints.evolutionVariants(n)),
    refetchInterval: POLL.slow,
  });
}

export function useEvolutionVariantLineageQuery(id: string | null) {
  return useQuery({
    queryKey: keys.evolutionVariantLineage(id ?? ''),
    queryFn: () => api<VariantLineageReport>(endpoints.evolutionVariantLineage(id as string)),
    enabled: !!id,
  });
}

// ── Crew tasks (live execution) ─────────────────────────────────────────────
export type CrewTaskState = 'running' | 'completed' | 'failed' | string;

export interface CrewTask {
  id: string;
  crew: string;
  summary?: string;
  state: CrewTaskState;
  started_at?: string;
  completed_at?: string | null;
  eta?: string | null;
  model?: string;
  tokens_used?: number | null;
  cost_usd?: number | null;
  result_preview?: string | null;
  error?: string | null;
  parent_task_id?: string | null;
  is_sub_agent?: boolean;
  delegated_from?: string | null;
  delegated_to?: string | null;
  delegation_reason?: string | null;
  delegation_ts?: string | null;
  sub_agent_progress?: string | null;
  last_updated?: string;
}

export interface CrewStatus {
  name: string;
  state?: string;                // 'idle' | 'running' | 'unknown' | ...
  kind?: 'user' | 'internal';    // backend backfill classifies the roster
  current_task?: string | null;
  started_at?: string | null;
  eta?: string | null;
  [key: string]: unknown;
}

export interface CrewTasksReport {
  tasks: CrewTask[];
  crews: CrewStatus[];
  agents: OrgChartAgent[];
  updated_at: string;
  error?: string | null;
}

export function useCrewTasksQuery(limit = 20, projectId?: string) {
  return useQuery({
    queryKey: [...keys.crewTasks(limit), projectId ?? 'all'],
    queryFn: () => api<CrewTasksReport>(endpoints.tasks(limit, projectId)),
    refetchInterval: POLL.normal,
  });
}

// ── Consciousness indicators ────────────────────────────────────────────────
export interface ProbeResult {
  indicator: string;
  theory: string;
  score: number;
  evidence?: string;
  samples?: number;
}

export interface ConsciousnessLatest {
  report_id?: string;
  timestamp?: string;
  probes: ProbeResult[];
  composite_score?: number;
  summary?: string;
}

export interface ConsciousnessHistoryEntry {
  score: number;
  timestamp: string;
  probes: ProbeResult[];
}

export interface ConsciousnessReport {
  latest: ConsciousnessLatest;
  history: ConsciousnessHistoryEntry[];
  updated_at?: string | null;
  error?: string;
}

// ── Token usage & projection ────────────────────────────────────────────────
export type TokenPeriod = 'hour' | 'day' | 'week' | 'month' | 'year';

export interface TokenStat {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total: number;
  cost_usd: number;
  calls: number;
}

export interface RequestCostStat {
  requests: number;
  total_cost_usd: number;
  avg_cost_usd: number;
  avg_calls: number;
  avg_tokens: number;
}

export interface CrewCostStat {
  crew: string;
  requests: number;
  total_cost_usd: number;
  avg_cost_usd: number;
  avg_tokens: number;
}

export interface TokenUsageReport {
  stats: Record<TokenPeriod, TokenStat[]>;
  request_costs: Record<'day' | 'week' | 'month', RequestCostStat>;
  by_crew: { day: CrewCostStat[] };
  projection: {
    day_cost_usd: number;
    mtd_cost_usd: number;
    projected_monthly_usd: number;
  };
  updated_at: string;
  error?: string;
}

export function useTokenUsageQuery(projectId?: string) {
  return useQuery({
    queryKey: [...keys.tokens, projectId ?? 'all'],
    queryFn: () => api<TokenUsageReport>(endpoints.tokens(projectId)),
    refetchInterval: POLL.verySlow,
  });
}

export function useConsciousnessQuery(historyLimit = 30) {
  return useQuery({
    queryKey: keys.consciousness(historyLimit),
    queryFn: () => api<ConsciousnessReport>(endpoints.consciousness(historyLimit)),
    refetchInterval: POLL.verySlow,
  });
}

export function useKbBusinessesQuery() {
  return useQuery({
    queryKey: keys.kbBusinesses,
    queryFn: () => api<{ businesses: BusinessKB[] }>(endpoints.kbBusinesses()),
    refetchInterval: POLL.verySlow,
  });
}

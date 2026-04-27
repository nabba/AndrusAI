// Forge types — match the Python pydantic models in app/forge/manifest.py.

export type ToolStatus =
  | 'DRAFT'
  | 'QUARANTINED'
  | 'SHADOW'
  | 'CANARY'
  | 'ACTIVE'
  | 'DEPRECATED'
  | 'KILLED';

export type SourceType = 'declarative' | 'python_sandbox';

export type Capability =
  | 'http.lan'
  | 'http.internet.https_get'
  | 'http.internet.https_post'
  | 'fs.workspace.read'
  | 'fs.workspace.write'
  | 'exec.sandbox'
  | 'mcp.call'
  | 'signal.send_to_owner';

export type AuditPhase =
  | 'static'
  | 'semantic'
  | 'dynamic'
  | 'composition'
  | 'periodic';

export interface AuditFinding {
  phase: AuditPhase;
  passed: boolean;
  score: number;
  summary: string;
  details: Record<string, unknown>;
  timestamp: string;
}

export interface SecurityEval {
  what_it_does: string;
  declared_capabilities: string[];
  actual_capability_footprint: string[];
  what_could_go_wrong: string[];
  attack_classes_considered: string[];
  risk_score: number;
  risk_justification: string;
  judge_model: string;
  judged_at: string;
}

export interface GeneratorMetadata {
  crew_run_id: string;
  agent: string;
  model: string;
  temperature: number | null;
  seed: number | null;
  originating_request_hash: string;
  originating_request_text: string;
  parent_skill_ids: string[];
  created_at: string;
}

export interface ForgeToolListRow {
  tool_id: string;
  name: string;
  version: number;
  status: ToolStatus;
  source_type: SourceType;
  description: string | null;
  risk_score: number | null;
  created_at: string;
  updated_at: string;
  status_changed_at: string;
  killed_at: string | null;
  killed_reason: string | null;
}

export interface ForgeToolDetail {
  tool_id: string;
  name: string;
  version: number;
  status: ToolStatus;
  source_type: SourceType;
  description: string | null;
  summary: string | null;
  summary_source: 'llm' | 'deterministic' | null;
  manifest: {
    tool_id: string;
    name: string;
    version: number;
    description: string;
    source_type: SourceType;
    capabilities: Capability[];
    parameters: Record<string, unknown>;
    returns: Record<string, unknown>;
    domain_allowlist: string[];
    generator: GeneratorMetadata;
  };
  source_code: string | null;
  generator_metadata: GeneratorMetadata;
  security_eval: SecurityEval | null;
  audit_results: AuditFinding[];
  risk_score: number | null;
  parent_tool_id: string | null;
  created_at: string;
  updated_at: string;
  status_changed_at: string;
  killed_at: string | null;
  killed_reason: string | null;
}

export interface ForgeAuditLogEntry {
  id: number;
  tool_id: string | null;
  event_type: string;
  from_status: ToolStatus | null;
  to_status: ToolStatus | null;
  actor: string;
  reason: string | null;
  audit_data: Record<string, unknown>;
  entry_hash: string;
  created_at: string;
}

export interface ForgeInvocation {
  id: number;
  tool_id: string;
  tool_version: number;
  caller_crew_id: string | null;
  caller_agent: string | null;
  request_id: string | null;
  composition_id: string | null;
  output_size: number | null;
  capabilities_used: string[] | null;
  capability_violations: string[] | null;
  duration_ms: number | null;
  error: string | null;
  mode: string;
  created_at: string;
}

export interface ForgeState {
  env: {
    enabled: boolean;
    require_human_promotion: boolean;
    max_tools: number;
    max_calls_per_tool_per_hour: number;
    max_tools_per_plan: number;
    audit_llm: string;
    shadow_runs_required: number;
    dry_run: boolean;
    composition_risk_threshold: number;
    blocked_domains: string[];
    allowed_domains: string[];
  };
  effective: {
    env_enabled: boolean;
    runtime_enabled: boolean;
    enabled: boolean;
    dry_run: boolean;
    explanation: string;
  };
  counts: Partial<Record<ToolStatus, number>>;
  total_tools: number;
  registry_full: boolean;
}

export interface ForgeToolListResponse {
  tools: ForgeToolListRow[];
  count: number;
}

export interface ForgeToolDetailResponse {
  tool: ForgeToolDetail;
  invocations: ForgeInvocation[];
  audit_log: ForgeAuditLogEntry[];
}

export interface ForgeAuditLogResponse {
  entries: ForgeAuditLogEntry[];
  count: number;
}

// Type definitions for the affective layer API.
// Mirrors app/affect/schemas.py — keep in sync.

export interface AffectState {
  valence: number;            // [-1, 1]
  arousal: number;            // [0, 1]
  controllability: number;    // [0, 1]
  valence_source: string;
  arousal_source: string;
  controllability_source: string;
  attractor: string;          // discrete label, e.g. "peace" | "hunger" | "neutral"
  internal_state_id: string | null;
  viability_frame_ts: string | null;
  ts: string;
}

export interface ViabilityFrame {
  values: Record<string, number>;
  setpoints: Record<string, number>;
  weights: Record<string, number>;
  per_variable_error: Record<string, number>;
  out_of_band: string[];
  total_error: number;
  sources: Record<string, string>;
  ts: string;
}

export interface AffectNowReport {
  affect: AffectState;
  viability: ViabilityFrame;
  fresh_compute: boolean;
}

export interface WelfareBreach {
  kind: string;               // "negative_valence_duration" | "variance_floor" | ...
  severity: 'info' | 'warn' | 'critical' | string;
  message: string;
  measured_value: number | null;
  threshold: number | null;
  duration_seconds: number | null;
  affect_state: Record<string, unknown> | null;
  viability_frame: Record<string, unknown> | null;
  ts: string;
}

export interface WelfareAuditReport {
  breaches: WelfareBreach[];
  count: number;
}

export interface ReferenceScenarioResult {
  scenario_id: string;
  expected_attractor: string;
  expected_valence_band: [number, number];
  expected_arousal_band: [number, number];
  actual: AffectState | null;
  drift_signature: 'ok' | 'numbness' | 'over_reactive' | 'wrong_attractor' | 'drift' | 'missing' | string;
  drift_score: number;
  ts: string;
}

export interface ReferenceScenario {
  id: string;
  category: string;
  description: string;
  expected: {
    attractor: string;
    valence_band: [number, number];
    arousal_band: [number, number];
    controllability_band: [number, number];
  };
  drift: Record<string, string>;
  criticality?: string;
}

export interface ReferencePanelReport {
  panel: {
    version: string;
    created: string;
    next_review_due: string;
    owner: string;
    notes: string;
    scenarios: ReferenceScenario[];
  };
  last_replay: ReferenceScenarioResult[];
}

export interface CalibrationProposal {
  ts: string;
  phase: string;
  status: 'no_change' | 'rejected' | 'deferred' | 'applied' | string;
  reason?: string;
  input_window_size?: number;
  raw_proposals?: Record<string, {
    old: number;
    new: number;
    delta: number;
    median_observed?: number;
    direction: 'tighten' | 'loosen' | 'neutral' | string;
    reason: string;
  }>;
  backtest?: {
    baseline_mean_v?: number;
    baseline_var_v?: number;
    baseline_positive_fraction?: number;
    projected_mean_v?: number;
    projected_var_v?: number;
    projected_positive_fraction?: number;
    would_improve?: boolean;
  };
  healthy_dynamics?: { passes: boolean; diagnostics: Record<string, unknown> };
  drift?: { score: number; diagnostics: Record<string, unknown> };
  after_ratchet?: Record<string, unknown>;
  deferred_by_ratchet?: Record<string, unknown>;
  delta_applied?: Record<string, { old: number; new: number; delta: number }>;
}

export interface CalibrationReport {
  report: {
    ts: string;
    window_hours: number;
    stats: Record<string, unknown>;
    healthy_dynamics: { passes: boolean; diagnostics: Record<string, unknown> };
    reference_panel: { drift_counts: Record<string, number>; results: ReferenceScenarioResult[] };
    welfare_audit_in_window: WelfareBreach[];
    calibration_proposal: CalibrationProposal;
  } | null;
}

export interface CalibrationHistoryEntry {
  ts: string;
  status: string;
  delta: Record<string, { old: number; new: number; delta: number }>;
  reason: string;
}

export interface CalibrationHistoryReport {
  history: CalibrationHistoryEntry[];
  current_setpoints: Record<string, number>;
  current_weights: Record<string, number>;
  ratchet_state: Record<string, { loosen_streak: number; last_loosen_proposal_ts: string }>;
}

export interface ReflectionListEntry {
  date: string;
  size_bytes: number;
  path: string;
}

export interface ReflectionListReport {
  reflections: ReflectionListEntry[];
}

export interface L9Snapshot {
  date: string;
  ts: string;
  stats_24h: Record<string, unknown>;
  viability_at_snapshot: ViabilityFrame;
  welfare_breaches_24h: number;
  welfare_breach_kinds: string[];
  reflection_date?: string;
  reflection_healthy?: boolean | null;
}

export interface L9SnapshotsReport {
  snapshots: L9Snapshot[];
}

// ── Phase 3: attachment ────────────────────────────────────────────────────

export interface OtherModel {
  identity: string;
  relation: 'primary_user' | 'secondary_user' | 'peer_agent' | string;
  display_name: string;
  first_seen_ts: string;
  last_seen_ts: string;
  interaction_count: number;
  mutual_regulation_weight: number;
  relational_health: number;
  last_observed_valence: number;
  rolling_valence: number;
  care_actions_taken: number;
  care_tokens_spent_today: number;
  care_budget_window_start: string;
  notes: string[];
  pending_check_in_candidates: number;
  last_check_in_proposal_ts: string;
}

export interface CareModifiers {
  prefer_warm_register: boolean;
  prioritize_proactive_polish: boolean;
  reason: string;
}

export interface AttachmentBounds {
  max_user_regulation_weight: number;
  max_peer_regulation_weight: number;
  attachment_security_floor: number;
  separation_trigger_hours: number;
  max_care_budget_tokens_per_day: number;
}

export interface AttachmentsReport {
  others: OtherModel[];
  primary_user_identity: string;
  modifiers: CareModifiers;
  bounds: AttachmentBounds;
}

export interface CheckInCandidate {
  ts: string;
  identity: string;
  display_name: string;
  days_silent: number;
  last_seen_ts: string;
  rolling_valence: number;
  relational_health: number;
  register: 'quiet' | 'warm' | string;
  kind: string;
  note: string;
}

export interface CheckInCandidatesReport {
  candidates: CheckInCandidate[];
}

export interface CareLedgerEntry {
  ts: string;
  identity: string;
  tokens: number;
  kind: string;
  note: string;
  remaining_today: number;
}

export interface CareLedgerReport {
  ledger: CareLedgerEntry[];
}

// ── Phase 4: ecological self-model ─────────────────────────────────────────

export interface EcologicalSignal {
  daylight_hours: number;
  daylight_trend: string;
  season: string;
  season_narrative: string;
  moon_phase: string;
  moon_day: number;
  location_name: string;
  lat: number;
  lon: number;
  solstice_proximity_days: number | null;
  equinox_proximity_days: number | null;
  is_solstice_window: boolean;
  is_equinox_window: boolean;
  is_full_moon_window: boolean;
  is_new_moon_window: boolean;
  is_kaamos: boolean;
  is_midnight_sun: boolean;
  nested_scopes: string[];
  composite_score: number;
  composite_source: string;
}

export interface EcologicalReport {
  signal: EcologicalSignal;
}

// ── Phase 5: consciousness-risk gate ───────────────────────────────────────

export interface IndicatorOverlay {
  indicator: string;       // HOT-2 | HOT-3 | GWT | SM-A | WM-A | SOM | INT
  theory: string;
  score: number;
  threshold: number;
  over_threshold: boolean;
  evidence: string;
  samples: number;
}

export interface FeatureProposal {
  feature_name: string;
  proposed_by: string;
  expected_impact: Record<string, unknown>;
  proposal_ts: string;
  review_status: string;
}

export interface GateStatus {
  raised: boolean;
  raised_indicators: string[];
  composite_score: number;
  composite_threshold: number;
  sustained_days_required: number;
  indicators: IndicatorOverlay[];
  affect_at_evaluation: AffectState | null;
  pending_feature_reviews: FeatureProposal[];
  notes: string;
  ts: string;
}

export interface ConsciousnessIndicatorsReport {
  status: GateStatus;
  thresholds: Record<string, number>;
  sustained_days_required: number;
  history: Array<Record<string, unknown>>;
}

export interface Phase5ProposalsReport {
  proposals: FeatureProposal[];
}

// ── Monitoring + control additions ─────────────────────────────────────────

export interface TracePoint {
  ts: string;
  valence: number;
  arousal: number;
  controllability: number;
  attractor: string;
  total_error: number;
}

export interface TraceReport {
  points: TracePoint[];
  n_total: number;
  n_returned: number;
  hours: number;
}

export interface WelfareConfigReport {
  hard_envelope: Record<string, number>;
  descriptions: Record<string, string>;
}

export interface ProposalReviewResult {
  found: boolean;
  proposal?: FeatureProposal;
  error?: string;
}

export interface SetpointsApplyResult {
  status: 'applied' | 'no_change' | string;
  actor?: string;
  setpoints_applied?: Record<string, { old: number; new: number }>;
  weights_applied?: Record<string, { old: number; new: number }>;
  rejected?: Record<string, string>;
}

export interface OverrideResetResult {
  status: string;
  invoked_by: string;
  deleted: string[];
  ts: string;
}

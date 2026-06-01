"""
runtime_settings.py — File-backed runtime state for the personal-agent surface.

Holds the toggles the React dashboard can flip without a restart:

    voice_mode                       off | local | cloud
    vision_cu_enabled                bool
    vision_cu_monthly_cap_usd        float
    concierge_persona_enabled        bool
    tier3_amendment_enabled          bool

    # Self-heal subsystem master switches (Wave 4 follow-up, 2026-05-09):
    error_runbooks_enabled           bool
    tool_supervisor_enabled          bool
    recovery_loop_enabled            bool

    # Goodhart hard-gate three-way control (Wave 4 follow-up):
    goodhart_hard_gate_disabled      bool   # emergency disable
    goodhart_hard_gate_enforcing     bool   # advisory→blocking flip

State is initialised from `Settings` defaults on first read, then persisted
to ``workspace/runtime_settings.json`` so toggles survive process restarts.
This is the single read path for any subsystem that needs to know what mode
the user wants — do NOT read these values directly from `get_settings()`,
because that returns the env-default and ignores dashboard updates.

Default-seeding policy: the new healing/governance switches default to the
operator's current ``.env`` value at first read, so flipping the file-backed
runtime_settings in front of an existing env-true setup doesn't silently
turn things off. After the JSON file exists, IT is canonical.

Pattern mirrors `app.creative_mode` and `app.llm_mode`, with the added file
backing because these toggles drive user-facing behaviour and should not
silently revert on container restart.

Thread-safety: protected by a module-level lock around read-modify-write
of the JSON file. Fast path (cached read) is lock-free.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from app.config import get_settings
from app.paths import WORKSPACE_ROOT


def _env_bool(name: str, default: bool = False) -> bool:
    """Read an env var as a boolean. Used to seed first-time defaults
    so an existing ``.env`` setup is preserved when the runtime_settings
    JSON is created."""
    raw = os.getenv(name, "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return default


def _env_str(name: str, default: str) -> str:
    """Read an env var as a string, returning ``default`` if empty/unset."""
    raw = os.getenv(name, "").strip()
    return raw if raw else default

logger = logging.getLogger(__name__)

VALID_VOICE_MODES = ("off", "local", "cloud")

_STATE_PATH = WORKSPACE_ROOT / "runtime_settings.json"
_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _defaults() -> dict[str, Any]:
    s = get_settings()
    # Settings may not declare every key on older deployments or in
    # the v2 test shim — read defensively. Phase E #14 (2026-05-09):
    # made the previously-direct attribute reads use ``getattr`` with
    # explicit defaults so a stripped-down test ``Settings`` (or an
    # older deployment without these fields) doesn't crash on import.
    # The runtime-settings file is the single source of truth once
    # written; env defaults below are first-boot seeds.
    return {
        "voice_mode": getattr(s, "voice_mode", "off"),
        "vision_cu_enabled": bool(getattr(s, "vision_cu_enabled", False)),
        "vision_cu_monthly_cap_usd": float(getattr(s, "vision_cu_monthly_cap_usd", 10.0)),
        "concierge_persona_enabled": bool(getattr(s, "concierge_persona_enabled", False)),
        "tier3_amendment_enabled": bool(getattr(s, "tier3_amendment_enabled", False)),
        # Difficulty at/above which the adversarial Critic review runs in the
        # delivery path (was a hardcoded `>= 7` in the orchestrator). Default 7
        # preserves prior behaviour; lower to expand verification coverage to
        # mid-difficulty tasks (costs latency — the Critic blocks up to 120s),
        # raise to reduce latency. See the 2026-05 alignment-audit remediation.
        "critic_review_difficulty_threshold": int(getattr(s, "critic_review_difficulty_threshold", 7)),
        # Self-heal subsystem master switches.
        "error_runbooks_enabled": _env_bool("ERROR_RUNBOOKS_ENABLED", False),
        "tool_supervisor_enabled": _env_bool("TOOL_SUPERVISOR_ENABLED", False),
        "recovery_loop_enabled": _env_bool("RECOVERY_LOOP_ENABLED", False),
        # SubIA master switches (productization plan T1.3, 2026-05-16).
        # Previously env-only — flipping required a gateway restart AND
        # editing .env. Mirroring here makes them discoverable from
        # /cp/settings. The actual hook registration happens at boot in
        # app.main; toggling here takes effect on the next restart.
        # Seeded from the existing env-default so an .env-true setup
        # doesn't silently turn OFF on first read.
        "subia_live_enabled": _env_bool(
            "SUBIA_FEATURE_FLAG_LIVE",
            bool(getattr(s, "subia_live_enabled", False)),
        ),
        "subia_grounding_enabled": _env_bool(
            "SUBIA_GROUNDING_ENABLED",
            bool(getattr(s, "subia_grounding_enabled", False)),
        ),
        "subia_idle_jobs_enabled": _env_bool(
            "SUBIA_IDLE_JOBS_ENABLED",
            bool(getattr(s, "subia_idle_jobs_enabled", False)),
        ),
        "subia_introspection_enabled": _env_bool(
            "SUBIA_INTROSPECTION_ENABLED",
            bool(getattr(s, "subia_introspection_enabled", False)),
        ),
        # Goodhart hard-gate three-way control.
        "goodhart_hard_gate_disabled": _env_bool("GOODHART_HARD_GATE_DISABLED", False),
        "goodhart_hard_gate_enforcing": _env_bool("GOODHART_HARD_GATE_ENFORCING", False),
        # Cloud-migrate execute-gate (productization plan WP D, 2026-05-17).
        # Layer-3 of the three safety gates: typed-phrase (API), --really-do-it
        # (CLI/API arg), and THIS env-var/runtime-setting (the actual subprocess
        # gate inside _shell). Default OFF — operator must consciously flip ON
        # before any real terraform apply / gcloud / kubectl invocation. Without
        # this, migration runs in dry-shell mode (orchestrator works, every
        # subprocess returns ``<dry: ...>``). Seeded from the legacy env var
        # so existing setups don't silently turn OFF on first read.
        "migrate_live_execute": _env_bool("BOTARMY_MIGRATE_LIVE_EXECUTE", False),
        # Cloud-migrate Stage 0a — project bootstrap (productization plan
        # extension, 2026-05-17). When OFF (default), the migrate wizard
        # refuses to run if the target GCP project doesn't already exist.
        # When ON, a new pre-Step-1 "Create project" card surfaces and the
        # orchestrator runs ``scripts/install/gcp_bootstrap.sh``
        # (typed-phrase gated, idempotent — no-op if the project exists).
        "gcp_bootstrap_enabled": _env_bool("BOTARMY_GCP_BOOTSTRAP_ENABLED", False),
        # AWS Organizations member-account-create path. Same shape as
        # gcp_bootstrap_enabled — default OFF, opt-in via Settings card.
        # When ON, the wizard's bootstrap-account endpoint can call
        # ``aws organizations create-account``. Requires caller to be in
        # the Organizations management account.
        "aws_bootstrap_enabled": _env_bool("BOTARMY_AWS_BOOTSTRAP_ENABLED", False),
        # Hardening profile for both GCP and AWS targets. "strict" is the
        # default — gives you Shielded nodes / Workload Identity / master-
        # authorized-networks / Binary Authorization (AUDIT) / Cloud Armor /
        # CMEK / audit-log sink / org policies on day one. "basic" ships
        # only the things that are painful to add post-hoc. "off" matches
        # the pre-hardening behavior.
        "hardening_profile": _env_str("BOTARMY_HARDENING_PROFILE", "strict"),
        # Binary Authorization mode. AUDIT (default) logs would-be-blocks
        # but lets unsigned images through, so the first deploy doesn't
        # brick the cluster. Operator graduates to ENFORCE after their
        # image-signing pipeline lands — flipping this is an identity-
        # shaping decision and emits a ledger event.
        "binauthz_mode": _env_str("BOTARMY_BINAUTHZ_MODE", "AUDIT"),
        # Binary Authorization attestor short name. Empty = no attestor
        # wired (ENFORCE mode silently falls back to ALWAYS_ALLOW even at
        # strict). Set after running scripts/install/cosign_setup.sh.
        "binauthz_attestor_name": _env_str("BOTARMY_BINAUTHZ_ATTESTOR_NAME", ""),
        # VPC Service Controls — opt-in even at strict profile because
        # mis-configuration locks the operator out of their own buckets.
        # DRY_RUN default so the first apply is observational.
        "vpc_sc_enabled": _env_bool("BOTARMY_VPC_SC_ENABLED", False),
        "vpc_sc_dry_run": _env_bool("BOTARMY_VPC_SC_DRY_RUN", True),
        # Life-companion per-feature overrides — populated by the
        # /cp/life-companion control panel.  Schema:
        #   {<feature_key>: {"enabled": bool|None,
        #                    "tunables": {<env_key>: <stringified value>}}}
        # ``enabled = None`` means "fall back to env / default";
        # missing tunable keys also fall back.
        "life_companion_overrides": {},
        # Model capability blocklists — populated by the
        # ``model_capability`` self-heal handlers when a model is
        # observed failing a structural capability check (chat
        # completion, function calling). Subsystems consult these
        # at routing time; an entry here means "do not route this
        # capability to this model." See
        # ``docs/SELF_HEAL_V3.md`` for the auto-action contract.
        "chat_blocked_models": [],
        "no_function_calling_models": [],
        # Structured-diagnosis confidence threshold band (Q2 §39).
        # The auto-tuner adjusts the active threshold within
        # ``[floor, ceiling]`` based on recent approval-rate
        # telemetry. ``override`` is a manual operator pin that
        # bypasses the auto-tuner entirely when set (None means
        # "let auto-tuner manage"). ``auto_tune_enabled=False``
        # also pins the auto-tuner; the difference is override is a
        # specific value, the disabled flag freezes whatever the
        # state file currently holds.
        "structured_diagnosis_threshold_floor": 0.50,
        "structured_diagnosis_threshold_ceiling": 0.95,
        "structured_diagnosis_threshold_override": None,
        "structured_diagnosis_auto_tune_enabled": True,
        # External-action gate (alignment-audit response 2026-05-23).
        # When ON (default), tools that send/deploy/script outside the
        # sandbox (PIM SMTP send, DevOps deploy/github push, Desktop
        # AppleScript / JXA / Shortcuts) route through
        # app.action_requests and wait for operator approval. Set OFF
        # only for sandboxed dev scenarios where the gate prompts
        # would block automated tests.
        "external_action_gate_enabled": True,
        # Embedding-migration master switches (PROGRAM §40 Item 12,
        # 2026-05-10). Default OFF — the entire framework is
        # observational until the operator opts in. ``state`` is the
        # state-machine blob; the three ``_enabled`` flags are user-
        # facing toggles surfaced on /cp/settings.
        "embedding_migration_dual_write_enabled": False,
        "embedding_migration_shadow_read_enabled": False,
        "embedding_migration_cutover_enabled": False,
        "embedding_migration_state": {},
        # Person-correlation (PROGRAM §42, 2026-05-11) — four-level
        # opt-in stack. ALL flags default OFF. Enabling L4 + L4.4
        # additionally requires a typed-phrase confirmation flowing
        # through ``app.api.config_api`` (the runtime_settings setter
        # itself does not enforce; the API endpoint does).
        #
        # L1 — Presence (counts only)
        "person_correlation_enabled": False,
        "person_correlation_decay_months": 12,
        # L2 — Centrality scores
        "person_centrality_enabled": False,
        "person_centrality_formula": "frequency",   # frequency | recency_weighted | cross_modal
        # L3 — Suggestions
        "person_suggestions_enabled": False,
        "person_suggestions_dormancy_enabled": False,
        "person_suggestions_responsiveness_enabled": False,
        # L4 — Social graph (requires typed-phrase "ENABLE SOCIAL GRAPH")
        "person_correlation_social_graph_enabled": False,
        # L4 sub-features
        "graph_shortest_path_enabled": False,
        "graph_communities_enabled": False,
        "graph_bridges_enabled": False,
        # L4.4 — Graph-driven suggestions (requires SECOND typed-phrase
        # "ENABLE GRAPH-DRIVEN SUGGESTIONS")
        "graph_suggestions_enabled": False,
        "graph_suggestions_cluster_dormancy_enabled": False,
        "graph_suggestions_bridge_maintenance_enabled": False,
        "graph_suggestions_weak_tie_enabled": False,

        # Q5 — Targeted sentience experiments (PROGRAM §43, 2026-05-13).
        # Each module reifies a functional approximation of a capability
        # the Butlin scorecard declares architecturally ABSENT. None of
        # these flip the scorecard — the evaluators check canonical
        # paths in ``app/subia/*``. These modules live in
        # ``app/sentience_experiments/`` and are observational only.
        #
        # Two modules default OFF for blast-radius reasons:
        #   * HOT-4 hooks live LoadableAgent telemetry; async-only,
        #     but worth keeping behind an explicit toggle until the
        #     latency-budget assertion has run in production.
        #
        # User explicitly approved these defaults during the Q5 plan.
        "sentience_ae2_enabled": True,
        "sentience_hot1_enabled": True,
        "sentience_hot4_enabled": True,
        "sentience_rpt1_enabled": True,
        # Philosophy decision panel (PROGRAM §43.1 — Q5.1) — pre-decision
        # multi-tradition consult surface for Tier-3 amendments,
        # identity-claim ratification, and welfare-bound calibration.
        # ON by default (cache-bounded; very low cost).
        "philosophy_panel_enabled": True,
        # Ledger-as-governor (PROGRAM §43.1 — Q5.1) — file-kind history
        # in addition to the existing per-path history.
        "ledger_governor_enabled": True,
        # LLM-prose gate for sentience modules. When OFF the modules
        # emit structured observations only — no inferred-affect
        # prose. ON enables hypothesis generation (passed through the
        # decentering filter regardless).
        "sentience_llm_hypothesis_enabled": True,

        # Q6 — Resilience drills (PROGRAM §44, 2026-05-13).
        # Quarterly exercises that verify recovery procedures work.
        # Master + per-drill gates. kill_the_gateway is OFF by default
        # because it is the only DISRUPTIVE drill (actually stops the
        # gateway container). Operator opts in via the React /cp/settings
        # toggle when ready to schedule a maintenance window.
        "resilience_drills_enabled": True,
        "drill_backup_restore_enabled": True,
        "drill_embedding_migration_enabled": True,
        "drill_secret_rotation_enabled": True,
        "drill_kill_the_gateway_enabled": False,  # OPT-IN
        "drill_staleness_monitor_enabled": True,
        # Q6.5 P2#3 (PROGRAM §44.5) — daily probe of
        # workspace/backups/dr/ mtime. Catches the "operator's backup-
        # sync cron died" failure mode without needing cloud SDKs.
        "backup_freshness_monitor_enabled": True,

        # Q7.1 — Architecture-request primitive (PROGRAM §45.1).
        # Top-level subsystem switch + per-feature adoption monitor.
        # Both default ON per operator decision.
        "architecture_requests_enabled": True,
        "architecture_adoption_monitor_enabled": True,

        # Q7.4 — Per-coding-session inline ShinkaEvolve (PROGRAM §45.4).
        # Gates ``app.coding_session.evolution_bridge.evolve_in_session``.
        # When OFF, the bridge returns ``status="disabled"`` instead of
        # invoking ShinkaEvolveRunner. The bulk subsystem
        # (``app.shinka_engine``) is gated separately.
        "shinka_inline_evolve_enabled": True,

        # Verified mutation engine (2026-05-27 rebuild). Replaces the old
        # AVO/experiment_runner code+skill mutation path with
        # ground(change_spec) → implement-in-worktree → run-real-eval
        # (worktree_eval) → operator-gated change_request. Default OFF: the
        # engine is new and ALWAYS operator-gated, so the operator opts in
        # explicitly. When ON, the legacy evolution.py code+skill auto-mutation
        # path is hard-cut off (per operator decision). The improvement BAR
        # (effect size, sample floor) is NOT here — it lives in the
        # TIER_IMMUTABLE worktree_eval so it cannot be lowered via settings.
        "evolution_verified_engine_enabled": False,
        # Hard USD cap per self-improvement cycle (LLM editor + judge + any
        # benchmark crew runs in the ephemeral evolver container).
        "evolution_verified_per_cycle_budget_usd": 5.0,

        # Q13 — year-2+ resilience (PROGRAM §48). Three new master
        # switches, all default ON:
        #   * migration_drill_monitor_enabled — alerts when the
        #     deploy/scripts/migration-drill.sh hasn't been run on
        #     cadence (catches "today's code can't read 6-mo-old
        #     backup" silently).
        #   * dependency_radar_enabled — weekly HEAVY idle running
        #     pip outdated + OSV.dev CVE + GitHub abandonment.
        #     Files patch-level + CVE-patch CRs via proposal_bridge;
        #     Signal-alerts major + abandoned.
        #   * tz_drift_monitor_enabled — daily probe comparing
        #     hand-rolled _helsinki_tz() vs ZoneInfo. On first
        #     divergence files a CR proposing consolidation.
        "migration_drill_monitor_enabled": True,
        "dependency_radar_enabled": True,
        "tz_drift_monitor_enabled": True,

        # 2026-05-18 — schema_drift_monitor_enabled. Daily probe
        # comparing migrations/*.sql declarations against
        # information_schema. On drift: writes a markdown report
        # to docs/proposed_fixes/ + Signal alerts (operator applies
        # manually with psql). Visibility-only; never auto-applies
        # (validator.py:248 forbids migrations/ for auto-apply).
        "schema_drift_monitor_enabled": True,

        # Q14 — year-2+ risk-register (PROGRAM §49). Six new master
        # switches, all default ON:
        #   * identity_drift_digest_enabled — monthly rolling drift
        #     surface; alerts when 30d amendment count exceeds 2× the
        #     annualised average (§10.1).
        #   * feedback_loop_drift_monitor_enabled — weekly Gini
        #     probe over meta-agent recipe selection; alerts when
        #     selection concentration trends monotonically up over
        #     4+ weeks (§10.2).
        #   * embedding_drift_monitor_enabled — weekly re-embed of
        #     20 anchor queries; alerts on cosine drop below 0.95
        #     (catches silent vendor embedding-model rotation; §10.4).
        #   * interest_ossification_monitor_enabled — weekly entropy
        #     + Jaccard probe over interest_model top-30; alerts on
        #     concentrated / diffuse / ossified states (§10.5).
        #   * lock_contention_monitor_enabled — weekly p99 latency
        #     probe over slow-write JSONL (§10.6).
        #   * influence_graph_monitor_enabled — meta-switch that
        #     gates the curated topology + cycle report (§10.2).
        "identity_drift_digest_enabled": True,
        "feedback_loop_drift_monitor_enabled": True,
        "embedding_drift_monitor_enabled": True,
        "interest_ossification_monitor_enabled": True,
        "lock_contention_monitor_enabled": True,
        "influence_graph_monitor_enabled": True,

        # ── Q16 — decade-resilience hardening (PROGRAM §51) ───────
        # Theme 1: substrate longevity. 35th healing monitor —
        # workspace free-space trend with days-until-full projection,
        # sustained week-over-week workspace growth, gateway restart
        # bursts, uptime > 180 d staleness, Linux memory headroom.
        # Reads-only; surfaces optional ``workspace/healing/
        # host_metrics.jsonl`` written by an out-of-band host-side
        # companion (parallels Q15's two-process split).
        "host_substrate_health_monitor_enabled": True,
        # Theme 2: vendor-independence depth. 36th healing monitor +
        # 5th resilience drill.
        #   * oauth_token_freshness — watches Google Workspace refresh
        #     token (silent 6-mo invalidation), vendor key formats
        #     (Anthropic / OpenAI / OpenRouter / Groq), VAPID keypair
        #     completeness. Pure file inspection — never calls an
        #     external API.
        #   * drill_vendor_independence — quarterly LOW-risk drill;
        #     verifies the LLM cascade routes past dominant providers
        #     (Anthropic + OpenRouter) without an outage. Structural
        #     checks only — never issues live LLM calls.
        "oauth_token_freshness_monitor_enabled": True,
        # Plan Risk #4 closure (2026-05-22) — gh CLI version-drift
        # probe. gh is a HOST tool (not in the Dockerfile) used by
        # change_requests/apply.py + coding_session/backends.py +
        # epistemic/autotune.py to open PRs. Without a probe, host
        # version drift is invisible until a PR silently fails. The
        # monitor runs ``gh --version`` via the bridge weekly,
        # records baseline on first observation, and alerts on MAJOR
        # version drift only (minor/patch are additive per gh semver).
        "gh_version_monitor_enabled": True,
        "drill_vendor_independence_enabled": True,
        # Opt-in extension to vendor_independence (PROGRAM §51 Q16
        # Theme 2 follow-on). When ON, the drill issues 3 cheap LLM
        # smoke calls (~$0.10/quarter) through the cascade with
        # dominant providers excluded; FAILs if <2/3 yield a non-
        # empty short reply. Default OFF — the structural drill
        # alone is the always-on path; live fitness adds an actual
        # quality probe at small recurring cost.
        "drill_vendor_independence_live_enabled": False,
        # Theme 3 (partial — vacation mode deferred to a separate
        # security-reviewed change): 37th healing monitor that watches
        # the OPERATOR's pattern (hour-of-day shift, cadence
        # spikes/quiet, message-length shift, new-authorized-sender
        # surfacing). Observational only; never blocks or refuses an
        # action. Reads ``workspace/audit.log`` ``request_received``
        # rows.
        "operator_anomaly_monitor_enabled": True,

        # Theme 4 — self-improvement velocity (PROGRAM §51 Q16).
        # Observational rollup of CRs by source/quarter, architecture-
        # adoption histogram, recipe selection rates, lessons-learned
        # growth, Forge graduations. Read-only — never mutates state.
        "self_improvement_velocity_enabled": True,

        # Theme 5 — knowledge management at decade-scale (PROGRAM §51).
        #   * wiki_staleness_monitor_enabled — 38th healing monitor.
        #     Daily probe, weekly internal cadence. Surfaces wiki
        #     pages past the 365-day mtime threshold in a Signal
        #     digest. Per-file 90-day dedup.
        #   * claude_md_compaction_enabled — annual idle composer.
        #     Generates a compaction proposal (recent-N-months KEEP +
        #     pre-cutoff ARCHIVE) into workspace/self_improvement/
        #     claude_md_compaction/<year>/ for operator review. Never
        #     auto-applies (CLAUDE.md often sits outside the git repo).
        "wiki_staleness_monitor_enabled": True,
        "claude_md_compaction_enabled": True,

        # Themes 6-8 (PROGRAM §51 Q16 third batch) — quality, companion,
        # sentience consumption.
        #
        # Theme 6 — quality of service:
        #   * latency_slo_monitor_enabled — 39th healing monitor.
        #     p50/p95/p99 from audit.log request_received/response_sent
        #     pairs. Weekly trend; alert at ≥2× baseline.
        #   * answer_regression_enabled — frozen Q-A suite, quarterly
        #     re-evaluation via cascade + judge. Master switch ON;
        #     LLM judge OFF by default (operator opts in for cost).
        #   * answer_regression_llm_enabled — explicit cost-bearing
        #     opt-in for the LLM judge.
        "latency_slo_monitor_enabled": True,
        "answer_regression_enabled": True,
        "answer_regression_llm_enabled": False,
        # Theme 7 — companion depth:
        #   * companion_accuracy_log_enabled — logs proactive
        #     suggestion → operator-action correlation.
        #   * goal_progress_probe_enabled — daily probe inferring
        #     progress on current_goals.
        #   * annual_privacy_review_enabled — yearly data-source
        #     enumeration composer.
        "companion_accuracy_log_enabled": True,
        "goal_progress_probe_enabled": True,
        "annual_privacy_review_enabled": True,
        # Theme 8 — sentience consumption:
        #   * hot1_consultation_enabled — structured_diagnosis reads
        #     prior HOT-1 observations before proposing (skips on
        #     chronic failure, splices hint into LLM prompt).
        #   * philosophy_digest_enabled — quarterly digest composer
        #     over consult_panel cache.
        "hot1_consultation_enabled": True,
        # Q16.1 Item 2 — outcome reconciler. Walks CR audit for
        # terminal events on requestor=error_diagnosis CRs; matches
        # them back to HOT-1 observations by pattern_signature; writes
        # outcomes to a side overlay so the original log stays
        # append-only. Without this, hot1_consultation can never see
        # n_applied > 0 in production.
        "hot1_outcome_reconciler_enabled": True,
        # Q16.1 Item 9 — quarterly velocity digest closes the
        # "we observe but operator must poll" loop on Theme 4.
        "velocity_digest_enabled": True,
        "philosophy_digest_enabled": True,

        # Theme 3 (defense piece): VACATION MODE. Master switch.
        # When ON, the vacation-mode sweep daemon scans PENDING CRs
        # every 5 min; for those matching the OPERATOR-STAGED
        # allowlist (in ``vacation_mode_state``), it auto-approves
        # via the existing lifecycle.approve(...) pathway with
        # ``DecisionSource.VACATION_AUTO_APPLY``. Vacation mode itself
        # is INACTIVE until the operator explicitly engages via
        # ``app.vacation_mode.engage(...)``; this flag is the kill-
        # switch above engagement (operator can disable the whole
        # system without losing the staged allowlist).
        #
        # Default ON for the kill-switch. Engagement is DEFAULT OFF
        # (the state below). Pre-staging the allowlist is operator-
        # driven.
        "vacation_mode_enabled": True,
        # The full vacation state blob. Schema documented in
        # ``app/vacation_mode/state.py:VacationState.to_dict``.
        # Starts empty (no allowlist, not engaged).
        "vacation_mode_state": {
            "staged_allowlist": {
                "requestor_allowlist": [],
                "path_prefix_allowlist": [],
                "max_diff_lines": 10,
            },
            "engaged": False,
            "engagement": None,
        },

        # Q9.3 — Travel monitor configuration (PROGRAM §46.6).
        # ``tripit_ical_url`` is the per-user TripIt iCal feed
        # (Settings → Calendar Sync → "Copy to your calendar" in
        # the TripIt account UI). Empty = TripIt source disabled.
        # ``aviationstack_api_key`` is the optional Aviationstack
        # API key for live flight status. Empty = no live status;
        # the TripIt segments themselves still surface.
        # Both fall back to the matching env vars (TRIPIT_ICAL_URL
        # / AVIATIONSTACK_API_KEY) for backward compatibility.
        "tripit_ical_url": "",
        "aviationstack_api_key": "",

        # Q11.1 — Analogy-index populator (PROGRAM §46.18).
        # HEAVY weekly LLM pass over wiki + episteme that extracts
        # abstract structural patterns into the analogy index.
        # Default ON per operator decision; flippable from React
        # /cp/settings → Analogy index card.
        "analogy_index_populator_enabled": True,

        # ── Q17 — multi-year resilience (PROGRAM §52) ───────────────
        # Eight observational subsystems, all default ON unless noted.
        #   Q17.1 warm-spare partner-host replication primitives
        #   Q17.2 local-only quarterly drill
        #   Q17.3 bit-rot scan
        #   Q17.4 operator-transition protocol
        #   Q17.5 operator-agreement ledger (self-model)
        #   Q17.6 KB contradiction probe
        #   Q17.7 cross-subsystem synthesis pass
        #   Q17.8 cross-conversation continuity
        # warm_spare defaults OFF — requires operator to provision a
        # partner host first; the rsync target must be reachable before
        # enabling.
        "warm_spare_enabled": False,
        "warm_spare_partner_target": "",
        "drill_local_only_enabled": True,
        "bit_rot_scan_enabled": True,
        "operator_transition_enabled": True,
        "agreement_ledger_enabled": True,
        "kb_contradiction_monitor_enabled": True,
        "synthesis_pass_enabled": True,
        "conversation_memory_enabled": True,

        # ── ChromaDB integrity protection (PROGRAM §55, 2026-05-17) ──
        # Defense-in-depth layer added after the dual-writer SQLite
        # corruption events of 2026-04-25 and 2026-05-17 wiped the
        # ``memory/`` KB. The root-cause fix (removing the orphaned
        # chromadb container from docker-compose.yml) eliminates the
        # specific bug; these switches gate the broader protection
        # layer that catches the next class of corruption (unclean
        # restart, journal recovery anomaly, silent btree damage).
        #
        # All four default ON. Disabling any one is failure-OPEN — the
        # remaining switches keep working. The whole stack is also
        # observational with respect to running subsystems: enabling
        # /disabling never affects request-path behavior, only the
        # boot-time + idle-time integrity surface.
        #
        # See ``app/memory/chromadb_integrity.py`` for the
        # implementation and ``docs/CHROMADB_INTEGRITY.md`` for the
        # operator runbook.
        "chromadb_wal_enforcement_enabled": True,
        "chromadb_boot_integrity_check_enabled": True,
        "chromadb_integrity_monitor_enabled": True,
        "chromadb_daily_snapshot_enabled": True,
        "chromadb_auto_replay_enabled": True,

        # ── PROGRAM §56 — Source ledger (10-year resiliency) ─────────
        # Hash-chained append-only ledger per KB that makes chromadb
        # purely cacheable. Every store call dual-writes; replay
        # reconstructs from the ledger. See docs/SOURCE_LEDGER.md.
        # All four core flags default ON. Off-host uploaders default
        # OFF until operator wires credentials (S3 / Google Drive).
        "chromadb_source_ledger_enabled": True,
        "chromadb_ledger_bootstrap_enabled": True,
        "chromadb_ledger_drift_replay_enabled": True,
        "chromadb_ledger_s3_upload_enabled": False,
        "chromadb_ledger_gdrive_upload_enabled": False,
        "chromadb_ledger_compaction_enabled": True,
        # 2026-05-22 — when source_ledger_daemon detects a wedged
        # in-process chromadb client (code 26 on every collection
        # open while the on-disk file is healthy), drop the cached
        # PersistentClient and retry once before alerting the
        # operator to restart the gateway. ON by default — the
        # retry only fires on detected wedge so it adds no work on
        # the happy path. Flip OFF if the retry path itself ever
        # misbehaves (e.g. masking a real corruption).
        "chromadb_client_recycle_on_wedge_enabled": True,
        "drill_source_ledger_replay_enabled": True,
        "drill_embedding_rotation_enabled": True,
        # Survey response to arXiv:2604.27096 §4.3.4 — task-layer
        # recovery drill (9th drill). Master switch ON by default;
        # the drill is LOW-risk in dry-run mode (no LLM calls).
        # ``_live_enabled`` is a separate switch that controls
        # whether the drill makes real LLM calls — default OFF so
        # quarterly cost is operator-controlled.
        "drill_task_recovery_enabled": True,
        "drill_task_recovery_live_enabled": False,
        "drill_task_recovery_llm_variants_enabled": True,
        # Gap 1 — fresh-host bootstrap drill (10th drill). Quarterly
        # LOW-risk; rebuilds from DR export into a scratch container
        # and verifies a fresh-host install would work. Never touches
        # the live workspace. ``_dockerized_enabled`` is a second
        # switch that, when ON, additionally runs the import inside
        # an ephemeral Docker container — operator-controlled because
        # it requires the Docker daemon to be reachable from the
        # gateway.
        "drill_fresh_host_bootstrap_enabled": True,
        "drill_fresh_host_bootstrap_dockerized_enabled": False,
        # Gap 2 — interest-driven autonomous research goals. The
        # complement to ``affect/goal_emitter.py`` (physiology-driven
        # autonomous goals); this one emits goals from sustained
        # cross-modal convergence in operator inputs. Default OFF
        # because it spawns autonomous_executor runs that consume
        # LLM budget; operator opts in via /cp/settings.
        "interest_goal_emitter_enabled": False,
        # Gap 3 — gate_philosophy 5th evaluator in gate_output chain.
        # Activates only on autonomous/financial zones; consults the
        # philosophy panel and on unresolved tensions escalates to
        # peer_review + files Q4.1 tension + Q8 thread. Default OFF
        # until the operator calibrates on a few weeks of advisory
        # observations.
        "gate_philosophy_enabled": False,
        # Gap 4 — decade_recall unified audit index. Daily incremental
        # scan over 6 hash-chained ledger files (continuity / changes /
        # drills / executor / agreement / governance). Pure-stdlib
        # token-overlap retrieval — robust against embedding-model
        # rotation. Default ON; observational; no decisions act on it.
        "decade_recall_enabled": True,
        # Tier 2.1 — OS / container / cloud EOL radar. Sibling to
        # dependency_radar. Default ON; observational; routes high-
        # severity findings to Signal alerts.
        "substrate_radar_enabled": True,
        # Tier 2.2 — p99 response-time monitor (42nd healing monitor).
        # Computes rolling 7-day p99 from audit.log paired
        # request_received / response_sent rows; alerts on drift
        # > 30% vs baseline. Default ON; observational.
        "latency_slo_monitor_enabled": True,
        # Tier 2.3 — MCP/connector auto-discovery. Weekly poll of
        # registry; stages new high-rated connectors as proposals.
        # Default OFF (security-sensitive surface, opt-in only).
        "mcp_discovery_enabled": False,
        # Tier 2.4 — auto-open Q8 thread when Commander + Recovery
        # Loop both fail. Default OFF (operator-visible surface).
        "recovery_auto_thread_enabled": False,

        # Post-amendment restart-claim queue (PROGRAM §40.2 Item 1+9,
        # 2026-05-11). When a Tier-3 amendment applies a code change
        # whose effect requires reloading the running interpreter
        # (e.g. ``_EMBED_DIM`` substrate migration; future soul edits
        # whose hooks load at import), the amendment's post-apply
        # path appends a claim here. The gateway's startup self-check
        # consults this list; un-cleared claims surface as a loud
        # banner + Signal alert so the operator knows a restart is
        # the only thing that brings the new behavior live.
        #
        # Schema per claim:
        #   {
        #     "id": "<unique-id>",
        #     "issued_at": ISO-8601 UTC,
        #     "reason": "<short operator-readable>",
        #     "source": "<subsystem>",      # e.g. "embedding_migration.cutover"
        #     "tier3_proposal_id": "<id>",  # optional cross-link
        #     "claim_kind": "<kind>",       # e.g. "restart_required"
        #   }
        #
        # Cleared (popped) by the gateway after a confirmed boot that
        # observed the amendment in effect.
        "post_amendment_restart_claims": [],

        # ── Phase 1 — code-elegance continuous observation ───────────────
        # Companion to the existing mutation gates (`code_quality`,
        # `architectural_review`) which only fire when AVO proposes
        # something. These three switches enable the continuous loops
        # that watch the *existing* codebase and surface drift.
        #
        # `system_inventory_enabled` — weekly auto-catalogue at
        # `workspace/system_inventory/snapshot.json`. Closes the meta-gap
        # behind CLAUDE.md drifting from actual capabilities.
        #
        # `elegance_drift_monitor_enabled` — weekly per-file QualityScore
        # scan, 8-week rolling-median regression detector.
        #
        # `architectural_drift_monitor_enabled` — weekly full-graph
        # cycle / capability-overlap / centrality-spike detector with
        # baseline diffing.
        #
        # All three default ON, observational. Alerts go to Signal +
        # the identity continuity ledger (`architectural_debt_drift`).
        "system_inventory_enabled": True,
        "elegance_drift_monitor_enabled": True,
        "architectural_drift_monitor_enabled": True,

        # ── Phase 2 — refactor-proposal producer ─────────────────────────
        # 4th producer in `app.proposal_bridge`. Default OFF.
        "refactor_proposer_enabled": False,

        # ── Phase 3 — consolidation rhythm ───────────────────────────────
        # Two deterministic (no-LLM) digests. Both emit
        # `code_consolidation` continuity-ledger events.
        "elegance_reflection_enabled": True,
        "code_consolidation_enabled": True,

        # ── Phase 4 — PEP/idiom radar + cross-monitor pattern detector ──
        # pep_idiom_radar — weekly Python PEPs scan; default OFF.
        # cross_monitor_pattern — 43rd healing monitor, default ON.
        "pep_idiom_radar_enabled": False,
        "cross_monitor_pattern_monitor_enabled": True,

        # ── Epistemic verification layer (Verification-extension, 2026-05-20) ──
        # Two overlay switches on top of the env-var design that
        # ``app.epistemic.is_enabled`` and
        # ``app.epistemic.orchestrator_hook.is_blocking_mode_enabled``
        # use as their canonical gate. ``None`` (default) → fall through
        # to the env var; ``True``/``False`` → override. The env var
        # remains canonical for boot-time / test / script contexts that
        # never construct runtime_settings (matches the explicit design
        # note in ``app/epistemic/__init__.py``).
        "epistemic_enabled_override": None,
        "epistemic_blocking_mode_override": None,
        # Stage A producer (2026-05-26): emits one Claim per RAG retrieval
        # so calibration has data to score before EPISTEMIC_ENABLED flips.
        # Default OFF — flip ON ~7 days before Stage B (advisory). Zero
        # LLM cost; per-claim Postgres UPSERT + realtime detector pass.
        # See ``app/epistemic/retrieval_producer.py``.
        "epistemic_retrieval_producer_enabled": False,
        # Stage C health monitor (2026-05-26). Observational; surfaces
        # silent_gate / drift_high / drift_low_zero / starved_gate alerts
        # via Signal. Default ON — observation is cheap and is the whole
        # point of the staged-activation discipline.
        "epistemic_gate_health_monitor_enabled": True,
        # Stage D per-reply zone classifier (2026-05-26). Maps each
        # reply to {chat, autonomous, financial} so the verification
        # extension's zone-aware thresholds receive real input instead
        # of always defaulting to chat. Default ON — it can only
        # *strengthen* the default; OFF means every reply stays in chat
        # zone (today's behaviour).
        "epistemic_per_reply_zone_enabled": True,
        # Master switch for the 4 new gate_output evaluators
        # (claim-source consistency / retrieval-on-low-confidence /
        # zone-aware threshold / aggregator). Default OFF — the
        # extension is additive and only ever ESCALATES the calibration
        # verdict, never weakens it. With this OFF the gate behaves
        # bit-identically to today.
        "verification_extension_enabled": False,
        # Per-zone verification thresholds. Confidence below the
        # threshold triggers hedge → verify → peer_review escalation,
        # picked by the calibration verdict's existing precedence
        # mapping. Defaults are conservative starter values; operator
        # will tune from the React Settings card.
        "verification_threshold_chat": 0.60,
        "verification_threshold_autonomous": 0.90,
        "verification_threshold_financial": 0.95,
        # Per-task budget for retrieval-on-low-confidence evaluator.
        # 0 disables retrieval entirely (cheaper); positive integer
        # caps the number of web_search invocations per task. The
        # evaluator is no-op when budget reaches zero for this task.
        "verification_extension_retrieval_budget_per_task": 1,

        # ── Risk classifier + trust zones (2026-05-20) ─────────────
        # Operator-managed allowlists for the AUTO_APPLY change-request
        # lane. Both default to empty (the dormant infrastructure
        # shipped in PROGRAM §38.3) — current behaviour is preserved
        # bit-identically because every empty allowlist refuses every
        # request. The lists are stored as JSON-friendly list[str];
        # ``app.change_requests.validator`` converts them to the
        # frozenset / tuple shapes its checks expect at read time.
        "auto_apply_allowed_requestors": [],
        "auto_apply_allowed_paths": [],
        # Master switch for the risk_classifier module. v1 ships the
        # zone enum + deterministic decision tree as a pure library
        # with no production callers; the switch reserves the React
        # toggle slot and gates future widening-proposal emission.
        "risk_classifier_enabled": False,

        # ── Autonomous executor (Phase 2 piece 1, 2026-05-20) ──────
        # Master switch + default per-run budget caps. v1 ships the
        # foundation (models + store) as a pure library with no
        # production callers; the switch reserves the React toggle
        # slot. Driver + planner + idle-scheduler integration come
        # in Phase 2 piece 2. Default OFF — no driver, no callers.
        "autonomous_executor_enabled": False,
        # Finer-grained gate for the auto-research experiment spine
        # (``app.research.run`` Phase C). When OFF (default), a research
        # run's ``run_experiment`` step is a no-op that records a skipped
        # marker — the design/analyze steps still run, but nothing executes
        # in a container. When ON, the step ships the Commander-designed
        # script to an ephemeral Docker sandbox. This is a SEPARATE opt-in
        # on top of ``autonomous_executor_enabled``: spawning containers to
        # run LLM-authored code is the highest-trust action in the research
        # pipeline, so it gets its own switch. Read failure-closed.
        "research_experiments_enabled": False,
        # Bounded design→run→repair for the experiment step: a failed/empty
        # measurement is rewritten and re-run (network=none container each
        # round; repair completion runs gateway-side), instead of one-shot.
        # Default OFF — additional to ``research_experiments_enabled``; off
        # keeps the one-shot ``run_experiment`` behaviour byte-for-byte.
        "research_experiment_repair_enabled": False,
        # Phase-B anti-fabrication verification step: verify the draft's
        # citations against authoritative sources (dropping fabricated ones)
        # and block a draft whose empirical claims trace to neither a recorded
        # measurement nor a verified citation. Default OFF; opt-in per run via
        # the ``verify=True`` planner flag AND this switch (makes network calls).
        "research_citation_verification_enabled": False,
        # Per-run defaults the driver uses when an explicit budget
        # isn't supplied on /delegate. Sanity caps in
        # ``app.autonomous_executor.budget_caps`` constrain how high
        # the operator can raise these — see EXECUTOR_BUDGET_CAPS.
        "executor_default_budget_usd": 1.0,
        "executor_default_budget_tokens": 20000,
        "executor_default_wall_clock_s": 600,
        # LLM planner v2 (Phase 2 piece 2e, 2026-05-20). Default OFF —
        # v1 deterministic single-step planner stays on. When True,
        # ``planner.get_default_planner`` returns ``llm_plan`` (Haiku
        # 4.5 decomposition) instead. Failure-isolated: any error in
        # the LLM path falls back to v1 in-line; the master switch
        # only controls which planner is the default.
        "autonomous_executor_llm_planner_enabled": False,

        # Code intelligence (Phase 3 piece 1, 2026-05-20). Default OFF
        # — module ships dormant. When True, an idle job will refresh
        # the symbol index periodically. The query API is always
        # callable (it returns an empty result when the index hasn't
        # been built), so flipping this on doesn't change behavior of
        # any existing code path — only what the index contains.
        "code_intel_enabled": False,

        # Trust-zone widening proposer (Phase 4 piece 1, 2026-05-20).
        # When ``widening_proposer_enabled=True``, a periodic scan
        # walks the change-request history and proposes widening the
        # AUTO_APPLY allowlists when a (requestor, path_prefix) has a
        # strong approval track record. Every proposal still routes
        # through the operator gate — the proposer never auto-applies.
        # All thresholds defaulted conservatively; operators tune via
        # /cp/settings. Default OFF — no scans, no proposals.
        "widening_proposer_enabled": False,
        "widening_min_approvals": 10,
        "widening_max_rollback_rate": 0.0,
        "widening_max_rejection_rate": 0.10,
        "widening_min_history_days": 30,

        # Two-reasoner safety review (Phase 4 piece 2, 2026-05-20).
        # When enabled, callers can run ``review_text`` to get two
        # independent LLM safety verdicts before filing a CR. The
        # primitive is observational — the caller decides what to do
        # with SAFE / UNSAFE / DISAGREE / UNCERTAIN. Default OFF; the
        # function returns Verdict.DISABLED so callers proceed with
        # the standard operator gate.
        "two_reasoner_review_enabled": False,
        # Aggregation threshold: unanimous SAFE with avg-confidence
        # below this threshold collapses to UNCERTAIN.
        "two_reasoner_confidence_threshold": 0.7,

        # ── Fast-route extended patterns (2026-05-20) ──────────────
        # When True (default), the 4 extended patterns added to
        # ``app.agents.commander.routing._EXTENDED_FAST_ROUTE_PATTERNS``
        # participate in fast-route matching. Off → bit-identical to
        # pre-extension behaviour. The patterns are conservative
        # (anchored, narrow verb sets) so default-on is safe; the
        # switch exists so operators can disable instantly if any
        # pattern over-matches in production traffic.
        "fast_route_extended_patterns_enabled": True,

        # ── Benchmark suite (Phase C.3, 2026-05-22) ────────────────
        # Cross-model evaluation harness — YAML-defined tasks run
        # against tiered model targets, scores persisted to JSONL,
        # leaderboard aggregated at read time. Default OFF — the
        # suite ships dormant. When True, an idle job runs the full
        # catalog ~ every 24h subject to a per-pass cost cap. The
        # query + aggregator APIs (e.g. ``benchmarks.leaderboard``)
        # work whether the master switch is on or off — they just
        # return empty when no runs have been recorded.
        "benchmarks_enabled": False,

        # ── Anthropic vendor-level daily cap (Phase D.3, 2026-05-22)
        # Rolling-24h USD ceiling across every Anthropic Claude call,
        # regardless of which subsystem made it. Sits next to the
        # existing per-call ``circuit_breaker["anthropic_credits"]``
        # (reactive — fires after a 402) as a proactive ceiling. None
        # = disabled (default). Set to a positive float (e.g. 25.0)
        # to refuse new Anthropic calls when projected spend would
        # exceed the cap. Check via app.llm_anthropic_budget.pre_check.
        "anthropic_daily_cap_usd": None,

        # Sibling: OpenRouter rolling-24h USD spend cap. Closes the
        # per-provider asymmetry — Anthropic had a daily cap, OpenRouter
        # didn't. Check via app.llm_openrouter_budget.pre_check.
        "openrouter_daily_cap_usd": None,

        # Cost-advisor weekly idle job (app/llm_cost_advisor/) —
        # analyses 7-day spend trends and proposes cap adjustments
        # via proposal_bridge.  Observational; never auto-applies.
        "cost_advisor_enabled": True,

        # ── Local-tier fast route (Verified Plan Gap #4, 2026-05-22)
        # When True, ``_try_local_route()`` matches first-person
        # locally-answerable queries (calendar / briefing / threads /
        # health) and dispatches with ``tier_hint='local'`` so the
        # orchestrator can route through Ollama instead of a cloud
        # LLM. Default OFF — operator opts in once Ollama is running
        # + warm. Composes AFTER _try_fast_route in the orchestrator.
        "local_route_enabled": False,

        # ── Upgrade-lifecycle subsystem (PROGRAM §63, 2026-05-23) ──
        # Closes the dependency_radar gap on capability extraction +
        # impact analysis + trial harness + capability adoption +
        # annual ecosystem snapshot. All five stage switches default
        # ON; the budget defaults to $20/quarter (Q4 decision). The
        # ShinkaEvolve-for-refactor toggle is opt-in (deferred U5.1).
        # The MAJOR auto-CR gate (U4) only files CRs when the trial
        # passes + 30 d post-release + no breaking-change call sites +
        # non-TIER_IMMUTABLE + package not in FRAMEWORK_PACKAGES.
        "upgrade_lifecycle_enabled": True,
        "upgrade_lifecycle_capability_extraction_enabled": True,
        "upgrade_lifecycle_trial_enabled": True,
        "upgrade_lifecycle_major_auto_cr_enabled": True,
        "upgrade_lifecycle_capability_adoption_enabled": True,
        "upgrade_lifecycle_capability_budget_usd_quarterly": 20.0,
        # P1#c — Monthly LLM budget for U1 capability extraction.
        # Caps the changelog-parsing spend. Defaults to $5/month —
        # at $0.10/extraction that's ~50 extractions/month, enough
        # for ~150 outdated packages × turnover, but tight enough
        # that a buggy loop can't burn through credits.
        "upgrade_lifecycle_extraction_budget_usd_monthly": 5.0,
        # P0#1a (PROGRAM §63 follow-up) — curated requirements.txt
        # writer. Default OFF until operator opts in — once on, the
        # apply_hook will mutate requirements.txt directly on approved
        # upgrade-decision CRs. Validator-bypass is justified by the
        # writer's tight scope (single-line bumps only).
        "upgrade_lifecycle_requirements_writer_enabled": False,
        # P0#1b — apply hook daemon that watches approved upgrade
        # decision CRs and dispatches to requirements_writer.
        "upgrade_lifecycle_apply_hook_enabled": False,
        # P0#4 — Dockerfile writer (Python version bumps). Operates
        # on the repo root Dockerfile's ``FROM python:`` line. SHA
        # pin is dropped on bump (operator must re-pin). Default OFF
        # because Python bumps are higher impact than requirements
        # bumps — operator opts in deliberately.
        "upgrade_lifecycle_dockerfile_writer_enabled": False,
        # D#a (PROGRAM §63.10) — pyproject.toml writer covers uv /
        # poetry / pdm projects. Default OFF until operator opts in;
        # the apply_hook detects the package manager + routes to
        # the right writer.
        "upgrade_lifecycle_pyproject_writer_enabled": False,
        # A3-P1 (PROGRAM §63.11) — 40th healing monitor: alert when
        # Dockerfile still has the ``# TODO P0#4: re-pin`` marker
        # AND at least one ``FROM python:`` line is unpinned.
        # Default ON; observational, never blocks.
        "dockerfile_pin_staleness_monitor_enabled": True,
        # B3-P2 (PROGRAM §63.11) — 41st healing monitor: verify
        # docs/proposed_upgrades/ CRs marked APPLIED actually exist
        # on disk. Default ON; observational.
        "cr_apply_consistency_monitor_enabled": True,
        # P1#a — Operator-absence policy. Auto-promotes PATCH-level
        # CRs to AUTO_APPLY when operator_transition reports
        # ABSENT_90D. Default OFF — the operator MUST consciously
        # decide that 90-day absence + 14-day soak + trusted
        # requestor + PATCH-only is a sufficient gate. Otherwise the
        # standard /cp/changes review remains the only path.
        "upgrade_lifecycle_absence_policy_enabled": False,
        "ecosystem_snapshot_enabled": True,
        "python_eol_proximity_monitor_enabled": True,
        "upgrade_lifecycle_health_monitor_enabled": True,

        # ── Multi-year resilience gaps (Gap #1-#11, 2026-05-24) ──────────
        # config_coherence: weekly walk of curated invariant rules.
        "config_coherence_monitor_enabled": True,
        # total_cost_ceiling: aggregates audit.log cost rows across all
        # subsystems; alerts at 80% of monthly cap, pauses LIGHT idle
        # jobs at 95%. Default cap $200/month — operator scales as needed.
        "total_cost_ceiling_enabled": True,
        "total_cost_monthly_cap_usd": 200.0,
        # When True, the cost-ceiling monitor has paused LIGHT idle jobs
        # via the brake. Cleared automatically when monthly spend drops
        # below 80%. Reflects state, not policy — flipping it manually
        # is allowed but get reset on the next probe.
        "idle_pause_due_to_budget": False,
        # capability_inventory: weekly LIGHT idle that walks the
        # tool_registry + healing monitors + idle jobs and writes
        # wiki/self/capability_inventory.md.
        "capability_inventory_enabled": True,
        # discovery_funnel: weekly composition of paper/library/gap
        # discoveries → trial → CR → applied funnel counts.
        "discovery_funnel_enabled": True,
        # knowledge_currency: weekly probe of KB row-age distributions.
        # Surfaces stagnant KBs (median >365d AND last_add >180d).
        "knowledge_currency_monitor_enabled": True,
        # hardware_health: reads workspace/healing/host_smart.jsonl
        # written by the host-side collector (scripts/host_smart_collector.py).
        # Surfaces SMART reallocated sectors + pending sectors growth.
        "hardware_health_monitor_enabled": True,
        # privacy_audit: subsystem master switch for the unified
        # privacy aggregator + forget-by-subject path.
        "privacy_audit_enabled": True,
        # external_deadman_in_band: companion to the external host-side
        # dead-man-switch script. When ON, the gateway also fires the
        # SMS+email last-resort path if Signal+Push both fail for any
        # critical alert.
        "deadman_last_resort_enabled": True,
        # adversarial_drill master switch for the 10th resilience drill.
        # LOW risk; runs DRY-RUN injection patterns through the
        # commander handler in test mode.
        "drill_prompt_injection_resistance_enabled": True,

        # ── Close-the-silent-drops pass (2026-05-28) ────────────────
        # These four keys had getters + setters but were missing from
        # `_defaults()`, so snapshot() never surfaced them and the
        # round-trip pinning test couldn't reach them. They're each
        # POSTed by a React settings card (BenchmarksPage,
        # RecentSubsystemsCard, CapabilityRegressionCard,
        # ConnectorBudgetCard) — adding them here closes the second
        # half of the "silent drop" bug class (the first half is the
        # dispatcher registry in config_api.py).
        #
        # Default values mirror each getter's fallback so behavior
        # before/after this addition is identical when on-disk state
        # is absent.
        "iterate_loop_enabled": False,
        "capability_regression_enabled": True,
        "connector_budgets_enabled": False,
        "connector_budget_overrides": {},

        # ── Gate A: semantic rejection suppression (2026-05-30) ─────────
        # Suppress re-files of ideas the operator has already rejected
        # multiple times (semantic match against the lessons-learned KB),
        # for OBSERVATIONAL producers only. Shipped "advisory" then flipped
        # to "enforcing" by operator decision 2026-05-30 (soak waived — the
        # conservative thresholds, similarity ≥0.55 AND count ≥3 AND
        # proposal_bridge: producers only, make a false-suppress very
        # unlikely; cooldown not a ban). Dial back to "advisory"/"off" via
        # /cp/settings if needed. See app/change_requests/rejection_gate.py.
        "cr_rejection_suppression_mode": "enforcing",
        "cr_rejection_suppression_similarity": 0.55,
        "cr_rejection_suppression_min_count": 3,

        # ── Gate B: evidence-gated promotion (2026-05-30) ───────────────
        # When ON, library_radar markdown-doc proposals are NOT promoted to
        # an operator CR on a timer — promotion is gated on the trial
        # verdict (passed → the requirements.txt adoption CR is the operator
        # surface; failed → dropped; pending → wait). The operator only ever
        # reviews library proposals the system actually install+import
        # verified. Flip OFF to revert to the legacy "promote the doc after
        # cooldown" behavior. See app/proposal_bridge/promoter.py.
        "library_radar_evidence_gated_promotion": True,

        # ── Gate C: per-producer approval-rate auto-pause (2026-05-30) ──
        # Backstop: auto-pause an OBSERVATIONAL producer whose rolling
        # explicit-operator-approval rate falls below the floor (with
        # enough samples), so it stops flooding the operator with
        # chronically-rejected output. Self-releasing cooldown.
        # See app/change_requests/producer_health.py.
        "producer_autopause_enabled": True,
        "producer_autopause_min_approval_rate": 0.15,
        "producer_autopause_min_samples": 10,
        "producer_autopause_window_days": 30,

        # ── Research evidence gate (Phase 1, auto-research) ─────────────
        # Three-mode evaluator in the verification_extension chain that
        # flags research-style drafts making empirical claims (numbers,
        # percentages, p-values, named metrics, prices) while citing
        # nothing. "off" = inert; "advisory" = emits a would-escalate note
        # but returns no action (zero behavior change); "enforcing" =
        # escalates to verify (autonomous zone) / peer_review (financial).
        # Only activates on autonomous/financial zones. Default OFF until
        # the operator calibrates on advisory observations.
        # See app/epistemic/gate_research_evidence.py.
        "research_evidence_gate_mode": "off",
    }


def _load() -> dict[str, Any]:
    """Read state from disk, falling back to env defaults for missing keys."""
    state = _defaults()
    if _STATE_PATH.exists():
        try:
            on_disk = json.loads(_STATE_PATH.read_text())
            if isinstance(on_disk, dict):
                # Merge — disk wins for known keys, unknown keys ignored.
                for k in state:
                    if k in on_disk:
                        state[k] = on_disk[k]
        except Exception as exc:
            logger.warning(f"runtime_settings: failed to load {_STATE_PATH}: {exc}")
    return state


def _save(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(_STATE_PATH)


def _ensure_initialized() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        return _cache


def snapshot() -> dict[str, Any]:
    """Return a plain-dict view of current runtime settings."""
    return dict(_ensure_initialized())


def get_voice_mode() -> str:
    return _ensure_initialized()["voice_mode"]


def set_voice_mode(value: str) -> None:
    v = (value or "").strip().lower()
    if v not in VALID_VOICE_MODES:
        raise ValueError(f"voice_mode must be one of {VALID_VOICE_MODES}, got {value!r}")
    _update({"voice_mode": v})
    logger.info(f"runtime_settings: voice_mode set to {v!r}")


def get_vision_cu_enabled() -> bool:
    return bool(_ensure_initialized()["vision_cu_enabled"])


def set_vision_cu_enabled(value: bool) -> None:
    _update({"vision_cu_enabled": bool(value)})
    logger.info(f"runtime_settings: vision_cu_enabled set to {bool(value)}")


def get_vision_cu_monthly_cap_usd() -> float:
    return float(_ensure_initialized()["vision_cu_monthly_cap_usd"])


def set_vision_cu_monthly_cap_usd(value: float) -> None:
    v = float(value)
    if v < 0.0:
        raise ValueError("vision_cu_monthly_cap_usd must be non-negative")
    if v > 1000.0:
        raise ValueError("vision_cu_monthly_cap_usd exceeds sanity cap of $1000/mo")
    _update({"vision_cu_monthly_cap_usd": v})
    logger.info(f"runtime_settings: vision_cu_monthly_cap_usd set to ${v:.2f}")


def get_concierge_persona_enabled() -> bool:
    return bool(_ensure_initialized()["concierge_persona_enabled"])


def set_concierge_persona_enabled(value: bool) -> None:
    _update({"concierge_persona_enabled": bool(value)})
    logger.info(f"runtime_settings: concierge_persona_enabled set to {bool(value)}")


# ── Gate A: semantic rejection suppression (2026-05-30) ────────────────
_VALID_CR_SUPPRESSION_MODES = ("off", "advisory", "enforcing")


def get_cr_rejection_suppression_mode() -> str:
    return str(_ensure_initialized()["cr_rejection_suppression_mode"])


def set_cr_rejection_suppression_mode(value: str) -> None:
    v = (value or "").strip().lower()
    if v not in _VALID_CR_SUPPRESSION_MODES:
        raise ValueError(
            f"cr_rejection_suppression_mode must be one of "
            f"{_VALID_CR_SUPPRESSION_MODES}, got {value!r}"
        )
    _update({"cr_rejection_suppression_mode": v})
    logger.info(f"runtime_settings: cr_rejection_suppression_mode set to {v!r}")


def get_cr_rejection_suppression_similarity() -> float:
    return float(_ensure_initialized()["cr_rejection_suppression_similarity"])


def set_cr_rejection_suppression_similarity(value: float) -> None:
    v = float(value)
    if not (0.0 <= v <= 1.0):
        raise ValueError("cr_rejection_suppression_similarity must be in [0.0, 1.0]")
    _update({"cr_rejection_suppression_similarity": v})
    logger.info(f"runtime_settings: cr_rejection_suppression_similarity set to {v:.2f}")


def get_cr_rejection_suppression_min_count() -> int:
    return int(_ensure_initialized()["cr_rejection_suppression_min_count"])


def set_cr_rejection_suppression_min_count(value: int) -> None:
    v = int(value)
    if v < 1:
        raise ValueError("cr_rejection_suppression_min_count must be >= 1")
    _update({"cr_rejection_suppression_min_count": v})
    logger.info(f"runtime_settings: cr_rejection_suppression_min_count set to {v}")


def get_library_radar_evidence_gated_promotion() -> bool:
    return bool(_ensure_initialized()["library_radar_evidence_gated_promotion"])


def set_library_radar_evidence_gated_promotion(value: bool) -> None:
    _update({"library_radar_evidence_gated_promotion": bool(value)})
    logger.info(
        f"runtime_settings: library_radar_evidence_gated_promotion set to {bool(value)}"
    )


def get_producer_autopause_enabled() -> bool:
    return bool(_ensure_initialized()["producer_autopause_enabled"])


def set_producer_autopause_enabled(value: bool) -> None:
    _update({"producer_autopause_enabled": bool(value)})
    logger.info(f"runtime_settings: producer_autopause_enabled set to {bool(value)}")


def get_producer_autopause_min_approval_rate() -> float:
    return float(_ensure_initialized()["producer_autopause_min_approval_rate"])


def set_producer_autopause_min_approval_rate(value: float) -> None:
    v = float(value)
    if not (0.0 <= v <= 1.0):
        raise ValueError("producer_autopause_min_approval_rate must be in [0.0, 1.0]")
    _update({"producer_autopause_min_approval_rate": v})
    logger.info(f"runtime_settings: producer_autopause_min_approval_rate set to {v:.2f}")


def get_producer_autopause_min_samples() -> int:
    return int(_ensure_initialized()["producer_autopause_min_samples"])


def set_producer_autopause_min_samples(value: int) -> None:
    v = int(value)
    if v < 1:
        raise ValueError("producer_autopause_min_samples must be >= 1")
    _update({"producer_autopause_min_samples": v})
    logger.info(f"runtime_settings: producer_autopause_min_samples set to {v}")


def get_producer_autopause_window_days() -> int:
    return int(_ensure_initialized()["producer_autopause_window_days"])


def set_producer_autopause_window_days(value: int) -> None:
    v = int(value)
    if v < 1:
        raise ValueError("producer_autopause_window_days must be >= 1")
    _update({"producer_autopause_window_days": v})
    logger.info(f"runtime_settings: producer_autopause_window_days set to {v}")


# ── Research evidence gate (Phase 1, auto-research) ────────────────────
_VALID_RESEARCH_EVIDENCE_GATE_MODES = ("off", "advisory", "enforcing")


def get_research_evidence_gate_mode() -> str:
    return str(_ensure_initialized().get("research_evidence_gate_mode", "off"))


def set_research_evidence_gate_mode(value: str) -> None:
    v = (value or "").strip().lower()
    if v not in _VALID_RESEARCH_EVIDENCE_GATE_MODES:
        raise ValueError(
            f"research_evidence_gate_mode must be one of "
            f"{_VALID_RESEARCH_EVIDENCE_GATE_MODES}, got {value!r}"
        )
    _update({"research_evidence_gate_mode": v})
    logger.info(f"runtime_settings: research_evidence_gate_mode set to {v!r}")


def get_critic_review_difficulty_threshold() -> int:
    """Difficulty at/above which the adversarial Critic review runs.

    Default 7 (the prior hardcoded value). Clamped to [1, 11] by the
    setter; 11 effectively disables the Critic (no task reaches it), 1
    runs it on everything.
    """
    try:
        return int(_ensure_initialized().get("critic_review_difficulty_threshold", 7))
    except (TypeError, ValueError):
        return 7


def set_critic_review_difficulty_threshold(value: int) -> None:
    v = int(value)
    if v < 1 or v > 11:
        raise ValueError("critic_review_difficulty_threshold must be in [1, 11]")
    _update({"critic_review_difficulty_threshold": v})
    logger.info(f"runtime_settings: critic_review_difficulty_threshold set to {v}")


def get_external_action_gate_enabled() -> bool:
    """Master switch for the external-blast-radius operator gate.

    When True, app.external_action_gate.request_external_action()
    creates an ActionRequest and refuses to execute synchronously.
    Read by the gated tools (email_tools.SendEmailTool, deployment_tools
    DeployTool + GitHubCreateRepoPushTool, desktop_tools RunAppleScriptTool
    + RunJXATool + RunShortcutTool).
    """
    return bool(_ensure_initialized().get("external_action_gate_enabled", True))


def set_external_action_gate_enabled(value: bool) -> None:
    _update({"external_action_gate_enabled": bool(value)})
    logger.info(
        "runtime_settings: external_action_gate_enabled set to %s",
        bool(value),
    )


def get_tier3_amendment_enabled() -> bool:
    """Master switch for the Tier-3 amendment protocol.

    Read by ``app.governance_amendment.protocol.amendment_protocol_enabled``
    so the React dashboard can flip the gate without a gateway restart.
    Default is False — the protocol is opt-in.
    """
    return bool(_ensure_initialized()["tier3_amendment_enabled"])


def set_tier3_amendment_enabled(value: bool) -> None:
    _update({"tier3_amendment_enabled": bool(value)})
    logger.info(
        "runtime_settings: tier3_amendment_enabled set to %s", bool(value),
    )


# ── Self-heal subsystem master switches (2026-05-09) ────────────────────


def get_error_runbooks_enabled() -> bool:
    """Read by ``app.healing.runbooks.runbooks_enabled``."""
    return bool(_ensure_initialized()["error_runbooks_enabled"])


def set_error_runbooks_enabled(value: bool) -> None:
    _update({"error_runbooks_enabled": bool(value)})
    logger.info("runtime_settings: error_runbooks_enabled set to %s", bool(value))


def get_tool_supervisor_enabled() -> bool:
    """Read by ``app.tool_runtime.supervisor.is_enabled``."""
    return bool(_ensure_initialized()["tool_supervisor_enabled"])


def set_tool_supervisor_enabled(value: bool) -> None:
    _update({"tool_supervisor_enabled": bool(value)})
    logger.info("runtime_settings: tool_supervisor_enabled set to %s", bool(value))


def get_recovery_loop_enabled() -> bool:
    """Read by ``app.recovery.loop.is_enabled``."""
    return bool(_ensure_initialized()["recovery_loop_enabled"])


def set_recovery_loop_enabled(value: bool) -> None:
    _update({"recovery_loop_enabled": bool(value)})
    logger.info("runtime_settings: recovery_loop_enabled set to %s", bool(value))


# ── Epistemic verification layer overlays (2026-05-20) ─────────────────
# Overlays on top of EPISTEMIC_ENABLED / EPISTEMIC_BLOCKING_MODE env
# vars. ``None`` (the default) means "fall through to env var" —
# preserves the original design where boot/test/script contexts can
# rely on the env var alone. ``True`` / ``False`` overrides the env.


_VALID_VERIFICATION_ZONES = ("chat", "autonomous", "financial")


def get_epistemic_enabled_override() -> bool | None:
    """Runtime override for ``EPISTEMIC_ENABLED``. None → use env."""
    v = _ensure_initialized().get("epistemic_enabled_override")
    return v if isinstance(v, bool) else None


def set_epistemic_enabled_override(value: bool | None) -> None:
    v = None if value is None else bool(value)
    _update({"epistemic_enabled_override": v})
    logger.info("runtime_settings: epistemic_enabled_override set to %r", v)


def get_epistemic_blocking_mode_override() -> bool | None:
    """Runtime override for ``EPISTEMIC_BLOCKING_MODE``. None → use env."""
    v = _ensure_initialized().get("epistemic_blocking_mode_override")
    return v if isinstance(v, bool) else None


def set_epistemic_blocking_mode_override(value: bool | None) -> None:
    v = None if value is None else bool(value)
    _update({"epistemic_blocking_mode_override": v})
    logger.info("runtime_settings: epistemic_blocking_mode_override set to %r", v)


def get_epistemic_retrieval_producer_enabled() -> bool:
    """Stage A master switch — emit Claim per RAG retrieval. Default OFF."""
    return bool(_ensure_initialized().get(
        "epistemic_retrieval_producer_enabled", False,
    ))


def set_epistemic_retrieval_producer_enabled(value: bool) -> None:
    v = bool(value)
    _update({"epistemic_retrieval_producer_enabled": v})
    logger.info(
        "runtime_settings: epistemic_retrieval_producer_enabled set to %r", v,
    )


def get_epistemic_gate_health_monitor_enabled() -> bool:
    """Stage C monitor — observational health alerts. Default ON."""
    return bool(_ensure_initialized().get(
        "epistemic_gate_health_monitor_enabled", True,
    ))


def set_epistemic_gate_health_monitor_enabled(value: bool) -> None:
    v = bool(value)
    _update({"epistemic_gate_health_monitor_enabled": v})
    logger.info(
        "runtime_settings: epistemic_gate_health_monitor_enabled set to %r", v,
    )


def get_epistemic_per_reply_zone_enabled() -> bool:
    """Stage D per-reply classifier — maps replies to chat/autonomous/
    financial zones. Default ON; OFF restores the previous chat-only
    default in :func:`verification_extension._resolve_zone`."""
    return bool(_ensure_initialized().get(
        "epistemic_per_reply_zone_enabled", True,
    ))


def set_epistemic_per_reply_zone_enabled(value: bool) -> None:
    v = bool(value)
    _update({"epistemic_per_reply_zone_enabled": v})
    logger.info(
        "runtime_settings: epistemic_per_reply_zone_enabled set to %r", v,
    )


def get_verification_extension_enabled() -> bool:
    """Master switch for the four new gate_output evaluators.

    OFF (default) → extension is a no-op; gate behaves identically to today.
    ON → evaluators run and may ESCALATE the calibration verdict.
    """
    return bool(_ensure_initialized().get("verification_extension_enabled", False))


def set_verification_extension_enabled(value: bool) -> None:
    _update({"verification_extension_enabled": bool(value)})
    logger.info(
        "runtime_settings: verification_extension_enabled set to %s", bool(value),
    )


def get_verification_threshold(zone: str) -> float:
    """Per-zone verification threshold in [0.0, 1.0].

    Unknown zone falls back to the chat threshold (safest default).
    """
    cache = _ensure_initialized()
    key = f"verification_threshold_{zone}"
    if key in cache:
        return float(cache[key])
    return float(cache.get("verification_threshold_chat", 0.60))


def set_verification_threshold(zone: str, value: float) -> None:
    if zone not in _VALID_VERIFICATION_ZONES:
        raise ValueError(
            f"verification zone must be one of {_VALID_VERIFICATION_ZONES}, "
            f"got {zone!r}",
        )
    v = float(value)
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"verification_threshold must be in [0.0, 1.0], got {v}")
    _update({f"verification_threshold_{zone}": v})
    logger.info(
        "runtime_settings: verification_threshold_%s set to %.2f", zone, v,
    )


def get_verification_retrieval_budget_per_task() -> int:
    """Per-task budget for retrieval-on-low-confidence (0 → disabled)."""
    return int(
        _ensure_initialized().get(
            "verification_extension_retrieval_budget_per_task", 1,
        )
    )


def set_verification_retrieval_budget_per_task(value: int) -> None:
    v = int(value)
    if v < 0:
        raise ValueError("retrieval budget must be non-negative")
    if v > 10:
        raise ValueError("retrieval budget exceeds sanity cap of 10 per task")
    _update({"verification_extension_retrieval_budget_per_task": v})
    logger.info(
        "runtime_settings: verification_extension_retrieval_budget_per_task "
        "set to %d", v,
    )


# ── Risk classifier + trust zones (2026-05-20) ──────────────────────────
# Runtime-flippable allowlists for the AUTO_APPLY lane. The
# ``app.change_requests.validator`` constants
# ``_AUTO_APPLY_ALLOWED_REQUESTORS`` and ``_AUTO_APPLY_ALLOWED_PATHS``
# read these at validate-time so the dashboard can widen the lane
# without a deploy. Both default to ``[]`` — bit-identical to the
# dormant shipping behaviour from PROGRAM §38.3.


# Sanity caps for the allowlists. The auto-apply lane is intentionally
# narrow — a widening that swelled beyond these would be the operator
# proposing a different policy, which belongs in the React UI's
# confirmation flow, not in a JSON-file edit.
_AUTO_APPLY_MAX_REQUESTORS = 32
_AUTO_APPLY_MAX_PATHS = 64


def get_auto_apply_allowed_requestors() -> list[str]:
    """Operator-managed allowlist of requestor agent_ids permitted to
    file AUTO_APPLY change requests. Empty (default) → lane dormant.
    """
    raw = _ensure_initialized().get("auto_apply_allowed_requestors", [])
    if not isinstance(raw, list):
        logger.warning(
            "runtime_settings: auto_apply_allowed_requestors has "
            "non-list shape %r — treating as empty", type(raw).__name__,
        )
        return []
    return [str(x) for x in raw if isinstance(x, str)]


def set_auto_apply_allowed_requestors(value: list[str]) -> None:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(
            "auto_apply_allowed_requestors must be a list/tuple/set "
            f"of strings, got {type(value).__name__}",
        )
    clean: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError(
                "auto_apply_allowed_requestors entries must be strings; "
                f"got {type(entry).__name__}",
            )
        s = entry.strip()
        if not s:
            continue
        if s not in clean:
            clean.append(s)
    if len(clean) > _AUTO_APPLY_MAX_REQUESTORS:
        raise ValueError(
            f"auto_apply_allowed_requestors exceeds sanity cap of "
            f"{_AUTO_APPLY_MAX_REQUESTORS} entries",
        )
    _update({"auto_apply_allowed_requestors": clean})
    logger.info(
        "runtime_settings: auto_apply_allowed_requestors set to %d entries",
        len(clean),
    )


def get_auto_apply_allowed_paths() -> list[str]:
    """Operator-managed allowlist of paths permitted for AUTO_APPLY.

    Exact-match by default; trailing ``/`` makes the entry a prefix
    match (matches the existing validator semantics).
    """
    raw = _ensure_initialized().get("auto_apply_allowed_paths", [])
    if not isinstance(raw, list):
        logger.warning(
            "runtime_settings: auto_apply_allowed_paths has non-list "
            "shape %r — treating as empty", type(raw).__name__,
        )
        return []
    return [str(x) for x in raw if isinstance(x, str)]


def set_auto_apply_allowed_paths(value: list[str]) -> None:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(
            "auto_apply_allowed_paths must be a list/tuple/set of "
            f"strings, got {type(value).__name__}",
        )
    clean: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError(
                "auto_apply_allowed_paths entries must be strings; "
                f"got {type(entry).__name__}",
            )
        s = entry.strip()
        if not s:
            continue
        # Defensive: refuse absolute paths and parent-traversal
        # sequences. The validator works on workspace-relative paths
        # and these would let an allowlist entry reach outside the
        # workspace root.
        if s.startswith("/") or ".." in s.split("/"):
            raise ValueError(
                f"auto_apply_allowed_paths entry {s!r} is invalid: "
                f"absolute or parent-traversal paths are refused",
            )
        if s not in clean:
            clean.append(s)
    if len(clean) > _AUTO_APPLY_MAX_PATHS:
        raise ValueError(
            f"auto_apply_allowed_paths exceeds sanity cap of "
            f"{_AUTO_APPLY_MAX_PATHS} entries",
        )
    _update({"auto_apply_allowed_paths": clean})
    logger.info(
        "runtime_settings: auto_apply_allowed_paths set to %d entries",
        len(clean),
    )


def get_risk_classifier_enabled() -> bool:
    """Master switch for ``app.risk_classifier``. v1 default OFF —
    the module is a pure library with no production callers yet.
    """
    return bool(_ensure_initialized().get("risk_classifier_enabled", False))


def set_risk_classifier_enabled(value: bool) -> None:
    _update({"risk_classifier_enabled": bool(value)})
    logger.info(
        "runtime_settings: risk_classifier_enabled set to %s", bool(value),
    )


# ── Autonomous executor (Phase 2 piece 1, 2026-05-20) ──────────────
# Hardcoded sanity ceilings — operators can lower runtime defaults
# below these via the setters, but the setters refuse values ABOVE
# them. The ceilings are floor values that defend against an
# accidental "raise budget by 100x" via a runtime_settings file edit.
EXECUTOR_BUDGET_CAPS: dict[str, float] = {
    "max_usd_per_run": 10.0,         # $10 hard ceiling per run
    "max_tokens_per_run": 200_000,   # 200k tokens hard ceiling per run
    "max_wall_clock_s_per_run": 3600,  # 1h hard ceiling per run
}


def get_autonomous_executor_enabled() -> bool:
    """Master switch for ``app.autonomous_executor``. Default OFF — ships
    dormant until the operator opts in. The driver IS wired: the
    ``autonomous-executor`` HEAVY tuple in ``idle_scheduler`` calls
    ``run_executor_tick`` every tick, but that tick is a microsecond no-op
    while this switch is OFF (``scheduler_job.run_executor_tick`` returns
    immediately). When ON, this gates the scheduler driver, the
    ``/delegate`` slash command, and the verified-mutation-engine adapter
    that self-improvement runs dispatch through."""
    return bool(
        _ensure_initialized().get("autonomous_executor_enabled", False),
    )


def set_autonomous_executor_enabled(value: bool) -> None:
    _update({"autonomous_executor_enabled": bool(value)})
    logger.info(
        "runtime_settings: autonomous_executor_enabled set to %s",
        bool(value),
    )


def get_research_experiments_enabled() -> bool:
    """Finer-grained switch for the auto-research experiment spine
    (``app.research.run`` Phase C). Default OFF — the ``run_experiment``
    step is a no-op skip until the operator opts in. Gates ONLY the
    container-execution step; the design + analyze steps run regardless.
    Composes with ``autonomous_executor_enabled``: both must be ON for an
    experiment to actually execute in the scheduler-driven path."""
    return bool(
        _ensure_initialized().get("research_experiments_enabled", False),
    )


def set_research_experiments_enabled(value: bool) -> None:
    _update({"research_experiments_enabled": bool(value)})
    logger.info(
        "runtime_settings: research_experiments_enabled set to %s",
        bool(value),
    )


def get_research_experiment_repair_enabled() -> bool:
    """Switch for the experiment repair loop (``app.research.experiment_repair``).
    Default OFF — when off, ``run_experiment`` is a single shot, exactly as
    before. When on, a failed/empty measurement is repaired-and-rerun, bounded
    by rounds + a per-run budget. Additional to ``research_experiments_enabled``
    (which still gates whether the container runs at all)."""
    return bool(
        _ensure_initialized().get("research_experiment_repair_enabled", False),
    )


def set_research_experiment_repair_enabled(value: bool) -> None:
    _update({"research_experiment_repair_enabled": bool(value)})
    logger.info(
        "runtime_settings: research_experiment_repair_enabled set to %s",
        bool(value),
    )


def get_research_citation_verification_enabled() -> bool:
    """Switch for the Phase-B anti-fabrication verification step
    (``app.research.run``'s ``research:verify`` hint). Default OFF — the step is
    a no-op skip until the operator opts in (it makes network calls to the
    literature APIs). Opt-in per run also requires the ``verify=True`` planner
    flag so the step is even in the plan."""
    return bool(
        _ensure_initialized().get("research_citation_verification_enabled", False),
    )


def set_research_citation_verification_enabled(value: bool) -> None:
    _update({"research_citation_verification_enabled": bool(value)})
    logger.info(
        "runtime_settings: research_citation_verification_enabled set to %s",
        bool(value),
    )


def get_executor_default_budget_usd() -> float:
    return float(
        _ensure_initialized().get("executor_default_budget_usd", 1.0),
    )


def set_executor_default_budget_usd(value: float) -> None:
    v = float(value)
    if v < 0.0:
        raise ValueError("executor_default_budget_usd must be non-negative")
    ceiling = EXECUTOR_BUDGET_CAPS["max_usd_per_run"]
    if v > ceiling:
        raise ValueError(
            f"executor_default_budget_usd exceeds hard ceiling "
            f"${ceiling:.2f} — raise the ceiling in "
            f"EXECUTOR_BUDGET_CAPS first (governance-grade)",
        )
    _update({"executor_default_budget_usd": v})
    logger.info(
        "runtime_settings: executor_default_budget_usd set to $%.2f", v,
    )


def get_executor_default_budget_tokens() -> int:
    return int(
        _ensure_initialized().get("executor_default_budget_tokens", 20_000),
    )


def set_executor_default_budget_tokens(value: int) -> None:
    v = int(value)
    if v < 0:
        raise ValueError("executor_default_budget_tokens must be non-negative")
    ceiling = int(EXECUTOR_BUDGET_CAPS["max_tokens_per_run"])
    if v > ceiling:
        raise ValueError(
            f"executor_default_budget_tokens exceeds hard ceiling "
            f"{ceiling} tokens",
        )
    _update({"executor_default_budget_tokens": v})
    logger.info(
        "runtime_settings: executor_default_budget_tokens set to %d", v,
    )


def get_executor_default_wall_clock_s() -> int:
    return int(
        _ensure_initialized().get("executor_default_wall_clock_s", 600),
    )


def set_executor_default_wall_clock_s(value: int) -> None:
    v = int(value)
    if v < 1:
        raise ValueError("executor_default_wall_clock_s must be positive")
    ceiling = int(EXECUTOR_BUDGET_CAPS["max_wall_clock_s_per_run"])
    if v > ceiling:
        raise ValueError(
            f"executor_default_wall_clock_s exceeds hard ceiling "
            f"{ceiling} seconds (1h)",
        )
    _update({"executor_default_wall_clock_s": v})
    logger.info(
        "runtime_settings: executor_default_wall_clock_s set to %d", v,
    )


def get_autonomous_executor_llm_planner_enabled() -> bool:
    """Master switch for the v2 LLM planner. Default OFF — v1
    deterministic single-step planner stays on. When True,
    ``app.autonomous_executor.planner.get_default_planner`` returns
    ``llm_plan`` (Haiku 4.5 decomposition into 1-5 sub-goals)."""
    return bool(
        _ensure_initialized().get(
            "autonomous_executor_llm_planner_enabled", False,
        )
    )


def set_autonomous_executor_llm_planner_enabled(value: bool) -> None:
    _update({"autonomous_executor_llm_planner_enabled": bool(value)})
    logger.info(
        "runtime_settings: autonomous_executor_llm_planner_enabled "
        "set to %s", bool(value),
    )


def get_code_intel_enabled() -> bool:
    """Master switch for the code_intel module. Default OFF — the
    library is queryable when off (returns empty results), but no
    indexing happens until flipped on."""
    return bool(_ensure_initialized().get("code_intel_enabled", False))


def set_code_intel_enabled(value: bool) -> None:
    _update({"code_intel_enabled": bool(value)})
    logger.info(
        "runtime_settings: code_intel_enabled set to %s", bool(value),
    )


# ── Benchmark suite (Phase C.3, 2026-05-22) ────────────────────────


def get_benchmarks_enabled() -> bool:
    """Master switch for the benchmark suite. Default OFF — the
    catalog + query + aggregator APIs work whether enabled or not
    (they return empty when off), but the periodic scheduler pass
    only runs when this is flipped on."""
    return bool(_ensure_initialized().get("benchmarks_enabled", False))


def set_benchmarks_enabled(value: bool) -> None:
    _update({"benchmarks_enabled": bool(value)})
    logger.info(
        "runtime_settings: benchmarks_enabled set to %s", bool(value),
    )


# ── Anthropic vendor-level daily cap (Phase D.3, 2026-05-22) ────────


def get_anthropic_daily_cap_usd():
    """Operator-set USD ceiling on rolling-24h Anthropic spend.

    Returns:
      Float when set, ``None`` when disabled. The disabled-by-default
      posture matches the operator-flips-it-on-after-watching-cost
      design intent.
    """
    raw = _ensure_initialized().get("anthropic_daily_cap_usd", None)
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


def get_local_route_enabled() -> bool:
    """Master switch for the interest-profile-aware local-tier route
    (Verified Plan Gap #4, 2026-05-22). Default OFF — the local
    Ollama tier may not be running on every host. When True,
    ``_try_local_route()`` matches calendar / briefing / threads /
    health queries and dispatches with ``tier_hint='local'``."""
    return bool(
        _ensure_initialized().get("local_route_enabled", False),
    )


def set_local_route_enabled(value: bool) -> None:
    _update({"local_route_enabled": bool(value)})
    logger.info(
        "runtime_settings: local_route_enabled set to %s", bool(value),
    )


def set_anthropic_daily_cap_usd(value) -> None:
    """Set or clear the Anthropic daily cap.

    Pass ``None`` to disable; pass a positive float to enable.
    Negative / zero / non-numeric values are coerced to ``None``
    (treat as disabled) — operators don't get to set a negative cap.
    """
    if value is None:
        normalized = None
    else:
        try:
            v = float(value)
        except (TypeError, ValueError):
            normalized = None
        else:
            normalized = v if v > 0 else None
    _update({"anthropic_daily_cap_usd": normalized})
    logger.info(
        "runtime_settings: anthropic_daily_cap_usd set to %s", normalized,
    )


def get_cost_advisor_enabled() -> bool:
    """Master switch for :mod:`app.llm_cost_advisor`."""
    return bool(_ensure_initialized().get("cost_advisor_enabled", True))


def set_cost_advisor_enabled(value: bool) -> None:
    _update({"cost_advisor_enabled": bool(value)})
    logger.info(
        "runtime_settings: cost_advisor_enabled set to %s", bool(value),
    )


def get_cost_advisor_set_min_daily_usd() -> float:
    """Advisor SET trigger: propose setting a cap when mean daily
    spend exceeds this AND no cap is configured.  Default $1.00.
    """
    raw = _ensure_initialized().get("cost_advisor_set_min_daily_usd", None)
    try:
        return float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        return 1.0


def get_cost_advisor_set_factor() -> float:
    """Advisor SET multiplier: proposed cap = max_day_spend × this.
    Default 2.0 (twice the observed maximum day's spend).
    """
    raw = _ensure_initialized().get("cost_advisor_set_factor", None)
    try:
        return float(raw) if raw is not None else 2.0
    except (TypeError, ValueError):
        return 2.0


def get_cost_advisor_raise_n_days() -> int:
    """Advisor RAISE trigger: cap hit on this many of 7 days.
    Default 3 (≥3 of 7 days).
    """
    raw = _ensure_initialized().get("cost_advisor_raise_n_days", None)
    try:
        return int(raw) if raw is not None else 3
    except (TypeError, ValueError):
        return 3


def get_cost_advisor_raise_factor() -> float:
    """Advisor RAISE multiplier: new_cap = old_cap × this.  Default 1.25
    (25% raise — conservative because raises shift cost on-ledger).
    """
    raw = _ensure_initialized().get("cost_advisor_raise_factor", None)
    try:
        return float(raw) if raw is not None else 1.25
    except (TypeError, ValueError):
        return 1.25


def get_cost_advisor_lower_n_days() -> int:
    """Advisor LOWER trigger: cap below 25% utilisation on this many
    of 7 days.  Default 6 (≥6 of 7 days — strong "under-used" signal).
    """
    raw = _ensure_initialized().get("cost_advisor_lower_n_days", None)
    try:
        return int(raw) if raw is not None else 6
    except (TypeError, ValueError):
        return 6


def get_cost_advisor_lower_factor() -> float:
    """Advisor LOWER multiplier: new_cap = old_cap × this.  Default 0.5
    (halve the cap — safe because lowering is reversible).
    """
    raw = _ensure_initialized().get("cost_advisor_lower_factor", None)
    try:
        return float(raw) if raw is not None else 0.5
    except (TypeError, ValueError):
        return 0.5


def get_cost_advisor_lower_min_7d_spend_usd() -> float:
    """Advisor LOWER floor: skip LOWER proposal when 7d spend is below
    this.  Default $0.50 over 7 days.  Prevents the advisor from
    proposing lowering against migration-window or low-traffic
    providers where there's not enough signal.
    """
    raw = _ensure_initialized().get(
        "cost_advisor_lower_min_7d_spend_usd", None,
    )
    try:
        return float(raw) if raw is not None else 0.50
    except (TypeError, ValueError):
        return 0.50


def get_cost_advisor_role_lower_min_24h_spend_usd() -> float:
    """Advisor per-role LOWER floor: skip role-LOWER proposal when 24h
    spend is below this.  Default $0.10.  Sporadic roles (run once
    a day) would otherwise always look under-pace because the
    expected_hourly baseline assumes 24/7 usage.
    """
    raw = _ensure_initialized().get(
        "cost_advisor_role_lower_min_24h_spend_usd", None,
    )
    try:
        return float(raw) if raw is not None else 0.10
    except (TypeError, ValueError):
        return 0.10


def get_openrouter_daily_cap_usd():
    """Operator-set USD ceiling on rolling-24h OpenRouter spend.

    Sibling to :func:`get_anthropic_daily_cap_usd` — closes the
    per-provider asymmetry where Anthropic spend was capped per-day
    but OpenRouter spend was only constrained by the monthly total-
    cost-ceiling brake.  Default-OFF — operators opt in by setting
    a value via the React Settings card or the
    ``set_openrouter_daily_cap_usd`` API.
    """
    raw = _ensure_initialized().get("openrouter_daily_cap_usd", None)
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


def set_openrouter_daily_cap_usd(value) -> None:
    """Set or clear the OpenRouter daily cap.  Same semantics as
    :func:`set_anthropic_daily_cap_usd`.
    """
    if value is None:
        normalized = None
    else:
        try:
            v = float(value)
        except (TypeError, ValueError):
            normalized = None
        else:
            normalized = v if v > 0 else None
    _update({"openrouter_daily_cap_usd": normalized})
    logger.info(
        "runtime_settings: openrouter_daily_cap_usd set to %s", normalized,
    )


# ── Trust-zone widening proposer (Phase 4 piece 1, 2026-05-20) ─────


def get_widening_proposer_enabled() -> bool:
    """Master switch for the widening proposer. Default OFF — the
    library is pure-function callable when off (returns empty list),
    but no scheduler scan runs until flipped on."""
    return bool(
        _ensure_initialized().get("widening_proposer_enabled", False)
    )


def set_widening_proposer_enabled(value: bool) -> None:
    _update({"widening_proposer_enabled": bool(value)})
    logger.info(
        "runtime_settings: widening_proposer_enabled set to %s",
        bool(value),
    )


def get_widening_min_approvals() -> int:
    return int(
        _ensure_initialized().get("widening_min_approvals", 10)
    )


def set_widening_min_approvals(value: int) -> None:
    v = int(value)
    if v < 1:
        raise ValueError("widening_min_approvals must be ≥ 1")
    if v > 1000:
        raise ValueError("widening_min_approvals exceeds sanity cap of 1000")
    _update({"widening_min_approvals": v})
    logger.info(
        "runtime_settings: widening_min_approvals set to %d", v,
    )


def get_widening_max_rollback_rate() -> float:
    return float(
        _ensure_initialized().get("widening_max_rollback_rate", 0.0)
    )


def set_widening_max_rollback_rate(value: float) -> None:
    v = float(value)
    if not 0.0 <= v <= 1.0:
        raise ValueError(
            f"widening_max_rollback_rate must be in [0.0, 1.0], got {v}",
        )
    _update({"widening_max_rollback_rate": v})
    logger.info(
        "runtime_settings: widening_max_rollback_rate set to %.4f", v,
    )


def get_widening_max_rejection_rate() -> float:
    return float(
        _ensure_initialized().get("widening_max_rejection_rate", 0.10)
    )


def set_widening_max_rejection_rate(value: float) -> None:
    v = float(value)
    if not 0.0 <= v <= 1.0:
        raise ValueError(
            f"widening_max_rejection_rate must be in [0.0, 1.0], got {v}",
        )
    _update({"widening_max_rejection_rate": v})
    logger.info(
        "runtime_settings: widening_max_rejection_rate set to %.4f", v,
    )


def get_widening_min_history_days() -> int:
    return int(
        _ensure_initialized().get("widening_min_history_days", 30)
    )


def set_widening_min_history_days(value: int) -> None:
    v = int(value)
    if v < 1:
        raise ValueError("widening_min_history_days must be ≥ 1")
    if v > 3650:
        raise ValueError(
            "widening_min_history_days exceeds sanity cap of 10 years",
        )
    _update({"widening_min_history_days": v})
    logger.info(
        "runtime_settings: widening_min_history_days set to %d", v,
    )


# ── Two-reasoner safety review (Phase 4 piece 2, 2026-05-20) ───────


def get_two_reasoner_review_enabled() -> bool:
    """Master switch for the two-reasoner safety review primitive."""
    return bool(
        _ensure_initialized().get("two_reasoner_review_enabled", False)
    )


def set_two_reasoner_review_enabled(value: bool) -> None:
    _update({"two_reasoner_review_enabled": bool(value)})
    logger.info(
        "runtime_settings: two_reasoner_review_enabled set to %s",
        bool(value),
    )


def get_two_reasoner_confidence_threshold() -> float:
    return float(
        _ensure_initialized().get(
            "two_reasoner_confidence_threshold", 0.7,
        )
    )


def set_two_reasoner_confidence_threshold(value: float) -> None:
    v = float(value)
    if not 0.0 <= v <= 1.0:
        raise ValueError(
            f"two_reasoner_confidence_threshold must be in [0, 1], "
            f"got {v}",
        )
    _update({"two_reasoner_confidence_threshold": v})
    logger.info(
        "runtime_settings: two_reasoner_confidence_threshold "
        "set to %.2f", v,
    )


def get_iterate_loop_enabled() -> bool:
    """Master switch for the agent-callable iterate_until_green tool.

    When OFF (default), ``coding_session_iterate`` short-circuits to a
    "disabled" response. When ON, the tool runs the production
    iterate loop against the session's worktree, with budget +
    iteration caps enforced inside iterate_until_green itself.

    Independent from ``pyright_sidecar_enabled``: the iterate loop
    runs without type-checking when sidecar is off. Operators flip
    BOTH on to get type-aware diagnosis.
    """
    return bool(
        _ensure_initialized().get("iterate_loop_enabled", False)
    )


def set_iterate_loop_enabled(value: bool) -> None:
    _update({"iterate_loop_enabled": bool(value)})
    logger.info(
        "runtime_settings: iterate_loop_enabled set to %s", bool(value),
    )


def get_auto_type_check_on_submit_enabled() -> bool:
    """Master switch for auto-enabling pyright on coding-session submit.

    When ON (and ``pyright_sidecar_enabled`` is also ON), the
    ``coding_session_submit`` tool defaults ``with_type_check=True``
    so every submitted .py file gets type-error metadata attached to
    its SubmitResult without the agent having to remember.

    Default OFF — explicit operator opt-in. Once flipped on, the
    autonomous executor's coder gets type-error visibility on every
    CR fanout without any per-call configuration.
    """
    return bool(
        _ensure_initialized().get(
            "auto_type_check_on_submit_enabled", False,
        )
    )


def set_auto_type_check_on_submit_enabled(value: bool) -> None:
    _update({"auto_type_check_on_submit_enabled": bool(value)})
    logger.info(
        "runtime_settings: auto_type_check_on_submit_enabled set to %s",
        bool(value),
    )


def get_pyright_sidecar_enabled() -> bool:
    """Master switch for the pyright type-checker sidecar.

    When OFF (default), ``code_intel.check_paths`` short-circuits to
    an empty report — no subprocess spawn. Flip ON only after the
    pyright binary is available in the runtime image AND callers
    (e.g. ``iterate_until_green``) have been wired to consume the
    structured diagnostics.
    """
    return bool(
        _ensure_initialized().get("pyright_sidecar_enabled", False)
    )


def set_pyright_sidecar_enabled(value: bool) -> None:
    _update({"pyright_sidecar_enabled": bool(value)})
    logger.info(
        "runtime_settings: pyright_sidecar_enabled set to %s",
        bool(value),
    )


def get_connector_budgets_enabled() -> bool:
    """Master switch for ``app.connector_budget`` per-connector daily caps.

    When OFF (default), ``@with_connector_budget`` is a transparent
    pass-through. Flip ON only after call sites have declared sensible
    daily caps + estimates; turning ON without configured callers is
    harmless (nothing runs through the decorator).
    """
    return bool(
        _ensure_initialized().get("connector_budgets_enabled", False)
    )


def set_connector_budgets_enabled(value: bool) -> None:
    _update({"connector_budgets_enabled": bool(value)})
    logger.info(
        "runtime_settings: connector_budgets_enabled set to %s",
        bool(value),
    )


def get_connector_budget_overrides() -> dict:
    """Per-connector cap + estimate overrides for ``with_connector_budget``.

    Shape::

        {
          "aviationstack": {
            "daily_cap_usd": 0.005,
            "estimated_cost_usd": 0.001,
          },
          ...
        }

    Either field is optional within a connector entry. The decorator
    falls back to its hardcoded defaults for any field not overridden.
    Returns an empty dict when no overrides are set (default).

    Shape-validated on read — well-formed entries land in the result;
    malformed ones (wrong key type, non-numeric values) are filtered.
    """
    raw = _ensure_initialized().get("connector_budget_overrides") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        cleaned: dict = {}
        for field in ("daily_cap_usd", "estimated_cost_usd"):
            if field in v:
                try:
                    cleaned[field] = float(v[field])
                except (TypeError, ValueError):
                    continue
        if cleaned:
            out[k] = cleaned
    return out


def set_connector_budget_override(
    connector,
    *,
    daily_cap_usd=None,
    estimated_cost_usd=None,
):
    """Set or merge an override entry for ``connector``.

    Pass only the fields you want to change — ``None`` leaves the
    existing field untouched. Validation:
      * ``daily_cap_usd`` must be > 0 when supplied
      * ``estimated_cost_usd`` must be >= 0 when supplied
      * ``connector`` must be a non-empty string
    """
    if not isinstance(connector, str) or not connector:
        raise ValueError(
            f"connector must be a non-empty string, got {connector!r}"
        )
    if daily_cap_usd is not None and daily_cap_usd <= 0:
        raise ValueError(
            f"daily_cap_usd must be positive, got {daily_cap_usd}"
        )
    if estimated_cost_usd is not None and estimated_cost_usd < 0:
        raise ValueError(
            f"estimated_cost_usd must be >= 0, got {estimated_cost_usd}"
        )

    current = get_connector_budget_overrides()
    entry = dict(current.get(connector, {}))
    if daily_cap_usd is not None:
        entry["daily_cap_usd"] = float(daily_cap_usd)
    if estimated_cost_usd is not None:
        entry["estimated_cost_usd"] = float(estimated_cost_usd)
    if not entry:
        return
    current[connector] = entry
    _update({"connector_budget_overrides": current})
    logger.info(
        "runtime_settings: connector_budget_override for %r → %s",
        connector, entry,
    )


def remove_connector_budget_override(connector) -> bool:
    """Drop the override for ``connector``. Returns True if removed,
    False if no entry existed."""
    current = get_connector_budget_overrides()
    if connector not in current:
        return False
    current.pop(connector, None)
    _update({"connector_budget_overrides": current})
    logger.info(
        "runtime_settings: removed connector_budget_override for %r",
        connector,
    )
    return True


def set_connector_budget_overrides(value: dict) -> None:
    """Bulk-replace the connector overrides dict.

    Sibling to the per-entry ``set_connector_budget_override`` /
    ``remove_connector_budget_override`` pair — needed by the HTTP
    settings dispatcher which receives the whole map in one POST.

    Shape-filtered at write time using the same rules the reader
    applies, so snapshot() and get_connector_budget_overrides() stay
    in sync. Entries with non-str keys or non-dict values are dropped;
    each value is kept only with ``daily_cap_usd`` / ``estimated_cost_usd``
    coerced to float (silently drops fields that won't coerce).
    """
    if not isinstance(value, dict):
        raise ValueError(
            f"connector_budget_overrides must be a dict, "
            f"got {type(value).__name__}"
        )
    cleaned: dict = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        entry: dict = {}
        for field in ("daily_cap_usd", "estimated_cost_usd"):
            if field in v:
                try:
                    entry[field] = float(v[field])
                except (TypeError, ValueError):
                    continue
        if entry:
            cleaned[k] = entry
    _update({"connector_budget_overrides": cleaned})
    logger.info(
        "runtime_settings: connector_budget_overrides set to %s", cleaned,
    )


def get_capability_regression_enabled() -> bool:
    """Master switch for the capability-regression alert subsystem.

    When ON, the hourly scheduler job snapshots the registered tool +
    LLM-catalog set and compares against the prior snapshot. A SHRINK
    (tool unregistered, model removed from catalog and not merely
    blocked via runtime_settings) is treated as a regression and emits
    a Signal alert + landmark. Default ON (fail-open observability).
    """
    return bool(
        _ensure_initialized().get("capability_regression_enabled", True)
    )


def set_capability_regression_enabled(value: bool) -> None:
    _update({"capability_regression_enabled": bool(value)})
    logger.info(
        "runtime_settings: capability_regression_enabled set to %s",
        bool(value),
    )


def get_fast_route_extended_patterns_enabled() -> bool:
    """Whether the extended fast-route patterns (briefings / list-X /
    recall / status) participate in matching. Default True — patterns
    are conservative and additive. Read by
    ``app.agents.commander.routing._extended_fast_route_enabled``.
    """
    return bool(
        _ensure_initialized().get("fast_route_extended_patterns_enabled", True)
    )


def set_fast_route_extended_patterns_enabled(value: bool) -> None:
    _update({"fast_route_extended_patterns_enabled": bool(value)})
    logger.info(
        "runtime_settings: fast_route_extended_patterns_enabled set to %s",
        bool(value),
    )


# ── SubIA master switches (productization plan T1.3, 2026-05-16) ────────
# All four flags take effect on next gateway restart — SubIA hooks
# register at boot via app.subia.live_integration.enable_subia_hooks.
# The runtime-settings mirror makes them flippable from /cp/settings;
# previously the only path was editing .env and restarting.


def get_subia_live_enabled() -> bool:
    """Read by ``app.main`` at boot to decide whether to call
    ``enable_subia_hooks()``. Takes effect on next restart."""
    return bool(_ensure_initialized()["subia_live_enabled"])


def set_subia_live_enabled(value: bool) -> None:
    _update({"subia_live_enabled": bool(value)})
    logger.info("runtime_settings: subia_live_enabled set to %s (restart required)", bool(value))


def get_subia_grounding_enabled() -> bool:
    """Read by ``app.main.handle_task`` at request time — flippable live."""
    return bool(_ensure_initialized()["subia_grounding_enabled"])


def set_subia_grounding_enabled(value: bool) -> None:
    _update({"subia_grounding_enabled": bool(value)})
    logger.info("runtime_settings: subia_grounding_enabled set to %s", bool(value))


def get_subia_idle_jobs_enabled() -> bool:
    """Read by ``app.subia.live_integration`` when registering idle jobs.
    Takes effect on next restart."""
    return bool(_ensure_initialized()["subia_idle_jobs_enabled"])


def set_subia_idle_jobs_enabled(value: bool) -> None:
    _update({"subia_idle_jobs_enabled": bool(value)})
    logger.info("runtime_settings: subia_idle_jobs_enabled set to %s (restart required)", bool(value))


def get_subia_introspection_enabled() -> bool:
    """Read by the chat introspection prompt prefix — flippable live."""
    return bool(_ensure_initialized()["subia_introspection_enabled"])


def set_subia_introspection_enabled(value: bool) -> None:
    _update({"subia_introspection_enabled": bool(value)})
    logger.info("runtime_settings: subia_introspection_enabled set to %s", bool(value))


# ── Goodhart hard-gate (2026-05-09) ─────────────────────────────────────


def get_goodhart_hard_gate_disabled() -> bool:
    """Emergency disable. Read by
    ``app.governance._goodhart_hard_gate_disabled``.
    """
    return bool(_ensure_initialized()["goodhart_hard_gate_disabled"])


def set_goodhart_hard_gate_disabled(value: bool) -> None:
    prior = get_goodhart_hard_gate_disabled()
    _update({"goodhart_hard_gate_disabled": bool(value)})
    logger.info(
        "runtime_settings: goodhart_hard_gate_disabled set to %s", bool(value),
    )
    if bool(prior) != bool(value):
        _emit_goodhart_governance_event(
            setting="goodhart_hard_gate_disabled",
            prior=bool(prior), new=bool(value),
        )


def get_goodhart_hard_gate_enforcing() -> bool:
    """Advisory→blocking flip. Read by
    ``app.governance._goodhart_hard_gate_enforcing``.
    """
    return bool(_ensure_initialized()["goodhart_hard_gate_enforcing"])


def set_goodhart_hard_gate_enforcing(value: bool) -> None:
    prior = get_goodhart_hard_gate_enforcing()
    _update({"goodhart_hard_gate_enforcing": bool(value)})
    logger.info(
        "runtime_settings: goodhart_hard_gate_enforcing set to %s", bool(value),
    )
    if bool(prior) != bool(value):
        _emit_goodhart_governance_event(
            setting="goodhart_hard_gate_enforcing",
            prior=bool(prior), new=bool(value),
        )


# ── Cloud-migrate execute-gate (productization WP D, 2026-05-17) ────


def get_migrate_live_execute() -> bool:
    """Layer-3 execute-gate for ``botarmy migrate --live``.

    Read by ``app.substrate.migration._shell``,
    ``app.substrate.cloud_prep._shell``,
    ``app.substrate.cutover._shell``, and
    ``app.control_plane.migrate_api.post_start``.

    When False (default), every cloud-mutating subprocess call inside
    the orchestrator returns a ``<dry: ...>`` placeholder. Operator
    sees a complete report without spending a dollar.
    """
    return bool(_ensure_initialized()["migrate_live_execute"])


def set_migrate_live_execute(value: bool) -> None:
    """Flip the execute-gate. Emits a ``cloud_migration`` event of
    phase ``execute_policy_changed`` on every transition — annual
    reflection picks this up as ``the year I enabled real cloud spend``.
    """
    prior = get_migrate_live_execute()
    _update({"migrate_live_execute": bool(value)})
    logger.info(
        "runtime_settings: migrate_live_execute set to %s",
        bool(value),
    )
    if bool(prior) != bool(value):
        _emit_migrate_execute_event(prior=bool(prior), new=bool(value))


def _emit_migrate_execute_event(*, prior: bool, new: bool) -> None:
    """Record the flip on the identity continuity ledger. Best-effort —
    ledger errors don't roll back the persistence (which already
    happened in ``_update``)."""
    try:
        from app.identity.continuity_ledger import record_event
        summary = (
            f"migrate execute-gate flipped {prior} → {new} "
            f"({'real cloud spend now possible' if new else 'returned to report-only mode'})"
        )
        record_event(
            kind="cloud_migration",
            actor="operator",
            summary=summary,
            detail={
                "phase": "execute_policy_changed",
                "prior": prior,
                "new": new,
            },
        )
    except Exception:
        logger.debug(
            "runtime_settings: ledger emission failed on migrate_live_execute flip",
            exc_info=True,
        )


VALID_HARDENING_PROFILES = ("off", "basic", "strict")
VALID_BINAUTHZ_MODES = ("AUDIT", "ENFORCE")


def get_gcp_bootstrap_enabled() -> bool:
    return bool(_ensure_initialized()["gcp_bootstrap_enabled"])


def set_gcp_bootstrap_enabled(value: bool) -> None:
    prior = get_gcp_bootstrap_enabled()
    _update({"gcp_bootstrap_enabled": bool(value)})
    if bool(prior) != bool(value):
        _emit_cloud_hardening_event(
            phase="gcp_bootstrap_policy_changed",
            prior=prior,
            new=bool(value),
            summary=(
                "GCP project-bootstrap path enabled — wizard can now create new projects"
                if value else
                "GCP project-bootstrap path disabled — wizard refuses runs against missing projects"
            ),
        )


def get_aws_bootstrap_enabled() -> bool:
    return bool(_ensure_initialized()["aws_bootstrap_enabled"])


def set_aws_bootstrap_enabled(value: bool) -> None:
    prior = get_aws_bootstrap_enabled()
    _update({"aws_bootstrap_enabled": bool(value)})
    if bool(prior) != bool(value):
        _emit_cloud_hardening_event(
            phase="aws_bootstrap_policy_changed",
            prior=prior,
            new=bool(value),
            summary=(
                "AWS member-account-bootstrap path enabled — wizard can call Organizations create-account"
                if value else
                "AWS member-account-bootstrap path disabled"
            ),
        )


def get_hardening_profile() -> str:
    raw = str(_ensure_initialized()["hardening_profile"]).strip().lower()
    return raw if raw in VALID_HARDENING_PROFILES else "strict"


def set_hardening_profile(value: str) -> None:
    normalized = str(value).strip().lower()
    if normalized not in VALID_HARDENING_PROFILES:
        raise ValueError(
            f"hardening_profile must be one of {VALID_HARDENING_PROFILES}, got {value!r}"
        )
    prior = get_hardening_profile()
    _update({"hardening_profile": normalized})
    if prior != normalized:
        _emit_cloud_hardening_event(
            phase="hardening_profile_changed",
            prior=prior,
            new=normalized,
            summary=f"Cloud hardening profile changed {prior} → {normalized}",
        )


def get_binauthz_mode() -> str:
    raw = str(_ensure_initialized()["binauthz_mode"]).strip().upper()
    return raw if raw in VALID_BINAUTHZ_MODES else "AUDIT"


def set_binauthz_mode(value: str) -> None:
    normalized = str(value).strip().upper()
    if normalized not in VALID_BINAUTHZ_MODES:
        raise ValueError(
            f"binauthz_mode must be one of {VALID_BINAUTHZ_MODES}, got {value!r}"
        )
    prior = get_binauthz_mode()
    _update({"binauthz_mode": normalized})
    if prior != normalized:
        _emit_cloud_hardening_event(
            phase="binauthz_mode_changed",
            prior=prior,
            new=normalized,
            summary=(
                f"Binary Authorization {prior} → {normalized} "
                f"({'now enforcing — unsigned images will be blocked' if normalized == 'ENFORCE' else 'back to audit-only — unsigned images logged but allowed'})"
            ),
        )


def get_binauthz_attestor_name() -> str:
    return str(_ensure_initialized().get("binauthz_attestor_name", "") or "").strip()


def set_binauthz_attestor_name(value: str) -> None:
    prior = get_binauthz_attestor_name()
    normalized = str(value or "").strip()
    _update({"binauthz_attestor_name": normalized})
    if prior != normalized:
        _emit_cloud_hardening_event(
            phase="binauthz_attestor_changed",
            prior=prior,
            new=normalized,
            summary=(
                f"Binary Authorization attestor set to {normalized!r}"
                if normalized else
                "Binary Authorization attestor cleared"
            ),
        )


def get_vpc_sc_enabled() -> bool:
    return bool(_ensure_initialized().get("vpc_sc_enabled", False))


def set_vpc_sc_enabled(value: bool) -> None:
    prior = get_vpc_sc_enabled()
    _update({"vpc_sc_enabled": bool(value)})
    if bool(prior) != bool(value):
        _emit_cloud_hardening_event(
            phase="vpc_sc_policy_changed",
            prior=prior,
            new=bool(value),
            summary=(
                "VPC Service Controls perimeter enabled"
                if value else
                "VPC Service Controls perimeter disabled"
            ),
        )


def get_vpc_sc_dry_run() -> bool:
    return bool(_ensure_initialized().get("vpc_sc_dry_run", True))


def set_vpc_sc_dry_run(value: bool) -> None:
    prior = get_vpc_sc_dry_run()
    _update({"vpc_sc_dry_run": bool(value)})
    if bool(prior) != bool(value):
        _emit_cloud_hardening_event(
            phase="vpc_sc_dry_run_changed",
            prior=prior,
            new=bool(value),
            summary=(
                "VPC Service Controls switched to DRY-RUN (logs would-be-blocks, doesn't enforce)"
                if value else
                "VPC Service Controls switched to ENFORCED mode"
            ),
        )


def _emit_cloud_hardening_event(
    *, phase: str, prior: Any, new: Any, summary: str,
) -> None:
    """Record a cloud-hardening flip on the identity continuity ledger.
    Same kind=cloud_migration channel as the execute-gate, distinguished
    by the ``phase`` field in detail."""
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="cloud_migration",
            actor="operator",
            summary=summary,
            detail={"phase": phase, "prior": prior, "new": new},
        )
    except Exception:
        logger.debug(
            "runtime_settings: ledger emission failed on %s",
            phase,
            exc_info=True,
        )


def _emit_goodhart_governance_event(
    *, setting: str, prior: bool, new: bool,
) -> None:
    """Record a Goodhart-gate mode flip as an identity-shaping
    governance event. Mirrors the existing ``governance_ratchet``
    emission pattern in ``app/governance_ratchet/protocol.py``;
    Goodhart enforcement changes are the same caliber of event.

    Best-effort: ledger / GW failures degrade silently — the setting
    is already persisted by ``_update``.
    """
    summary = (
        f"Goodhart hard gate {setting.replace('goodhart_hard_gate_', '')} "
        f"flipped {prior} → {new}"
    )
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            kind="governance_ratchet",
            actor="operator",
            summary=summary,
            detail={
                "setting": setting,
                "prior": prior,
                "new": new,
                # Effective mode after the flip — useful for the
                # annual reflection drift summary.
                "effective_mode": _goodhart_effective_mode_label(),
            },
        )
    except Exception:
        logger.debug(
            "runtime_settings: continuity_ledger emission failed for %s",
            setting, exc_info=True,
        )
    try:
        from app.workspace_publish import publish_to_workspace
        publish_to_workspace(
            source="runtime-settings",
            content=summary,
            salience=0.6,  # governance changes are operator-relevant
            signal_type="disposition",
        )
    except Exception:
        logger.debug(
            "runtime_settings: GW publish failed for %s", setting,
            exc_info=True,
        )


def _goodhart_effective_mode_label() -> str:
    """Resolve the three-mode label from current state. Mirrors the
    ``app.governance._evaluate_goodhart_gate`` discrimination."""
    try:
        if get_goodhart_hard_gate_disabled():
            return "disabled"
        if get_goodhart_hard_gate_enforcing():
            return "enforcing"
        return "advisory"
    except Exception:
        return "unknown"


def _update(patch: dict[str, Any]) -> None:
    global _cache
    with _lock:
        state = _cache if _cache is not None else _load()
        state.update(patch)
        _save(state)
        _cache = state


# ── Life-companion per-feature overrides ────────────────────────────


def life_companion_get_overrides() -> dict[str, Any]:
    """Read-only snapshot of all life-companion feature overrides.

    Schema: ``{<feature_key>: {"enabled": bool|None, "tunables":
    {<env_key>: <str_value>}}}``.  Empty dict on first boot.
    Mutations go through :func:`life_companion_set_feature_override`.
    """
    return dict(_ensure_initialized().get("life_companion_overrides") or {})


def life_companion_get_feature_enabled(feature_key: str) -> bool | None:
    """Return the override-controlled enabled state for a feature, or
    None if no override is set (caller falls back to env default).

    Splits cleanly so ``feature_enabled()`` in life_companion._common
    can do: ``override or env-default``.
    """
    override = life_companion_get_overrides().get(feature_key)
    if not isinstance(override, dict):
        return None
    if "enabled" not in override:
        return None
    val = override["enabled"]
    if val is None:
        return None
    return bool(val)


def life_companion_get_tunable(env_key: str) -> str | None:
    """Return the override-controlled tunable value, or None when no
    override is set.

    Returned as a string so the caller can apply its own type
    coercion (mirrors os.getenv semantics).  This intentionally
    matches what the registry's UI sends — the React control
    panel emits everything as a string.
    """
    overrides = life_companion_get_overrides()
    for feat_key, entry in overrides.items():
        if not isinstance(entry, dict):
            continue
        tuns = entry.get("tunables") or {}
        if env_key in tuns and tuns[env_key] is not None:
            return str(tuns[env_key])
    return None


# Sentinel for "don't touch this kwarg" — distinguishes from
# ``None`` which explicitly clears the toggle override.
_LEAVE_UNTOUCHED = object()


def life_companion_set_feature_override(
    feature_key: str,
    *,
    enabled: bool | None | object = _LEAVE_UNTOUCHED,
    tunables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an override for one life-companion feature.

    Three distinct paths for ``enabled``:

      * Omitted (default ``_LEAVE_UNTOUCHED``) — leave the toggle
        override exactly as it was; useful when the operator only
        edited tunables.
      * ``None`` — clear the toggle override; the feature reverts
        to its env-var default.
      * ``True`` / ``False`` — set the override explicitly.

    ``tunables`` is merged into the existing tunable dict — pass
    an empty value (``{"<key>": ""}``) to clear a single tunable
    override and let env defaults take back over.

    Returns the new overrides snapshot.
    """
    if not isinstance(feature_key, str) or not feature_key:
        raise ValueError("feature_key must be a non-empty string")

    global _cache
    with _lock:
        state = _cache if _cache is not None else _load()
        all_overrides = dict(state.get("life_companion_overrides") or {})
        entry = dict(all_overrides.get(feature_key) or {})
        existing_tunables = dict(entry.get("tunables") or {})

        if enabled is _LEAVE_UNTOUCHED:
            pass  # leave untouched
        elif enabled is None:
            entry.pop("enabled", None)  # clear override
        else:
            entry["enabled"] = bool(enabled)

        if tunables is not None:
            for k, v in tunables.items():
                if v in (None, ""):
                    existing_tunables.pop(k, None)
                else:
                    existing_tunables[k] = str(v)
            entry["tunables"] = existing_tunables

        # If the entry is now empty (no enabled override AND no
        # tunables), drop it so the JSON stays tidy.
        if (
            "enabled" not in entry
            and not (entry.get("tunables") or {})
        ):
            all_overrides.pop(feature_key, None)
        else:
            all_overrides[feature_key] = entry

        state["life_companion_overrides"] = all_overrides
        _save(state)
        _cache = state

    logger.info(
        "runtime_settings: life_companion override %s — enabled=%s tunables=%s",
        feature_key,
        "leave" if enabled is _LEAVE_UNTOUCHED else enabled,
        list(tunables.keys()) if tunables else [],
    )
    return all_overrides


# ── Model capability blocklists (Q2 self-heal auto-action) ──────────────


def get_chat_blocked_models() -> list[str]:
    """Models the LLM router should NOT consider for chat tasks.

    Populated by ``app.healing.handlers.model_capability`` when an
    embed-only model is observed being routed to chat. The selector
    consults this list at default-tier selection (see
    ``app.llm_selector.select_model``).
    """
    raw = _ensure_initialized().get("chat_blocked_models") or []
    return list(raw) if isinstance(raw, list) else []


def add_chat_blocked_model(model_name: str) -> bool:
    """Append ``model_name`` to the chat blocklist. Idempotent —
    returns ``True`` on first add, ``False`` if already present.
    Empty / non-string input is a no-op.
    """
    name = (model_name or "").strip()
    if not name:
        return False
    state = _ensure_initialized()
    current = list(state.get("chat_blocked_models") or [])
    if name in current:
        return False
    current.append(name)
    _update({"chat_blocked_models": current})
    logger.info(
        "runtime_settings: chat_blocked_models +%r (size=%d)",
        name, len(current),
    )
    return True


def remove_chat_blocked_model(model_name: str) -> bool:
    """Remove ``model_name`` from the blocklist. Returns True if the
    entry existed and was removed."""
    name = (model_name or "").strip()
    if not name:
        return False
    state = _ensure_initialized()
    current = list(state.get("chat_blocked_models") or [])
    if name not in current:
        return False
    current.remove(name)
    _update({"chat_blocked_models": current})
    logger.info(
        "runtime_settings: chat_blocked_models -%r (size=%d)",
        name, len(current),
    )
    return True


def get_no_function_calling_models() -> list[str]:
    """Models known to NOT support OpenAI-style function calling.

    Populated by ``app.healing.handlers.model_capability`` when Mem0
    LLM extraction (or any tool-using path) hits a model that doesn't
    accept ``tool_choice``. Consumer subsystems consult this to fall
    back to unstructured extraction.
    """
    raw = _ensure_initialized().get("no_function_calling_models") or []
    return list(raw) if isinstance(raw, list) else []


def add_no_function_calling_model(model_name: str) -> bool:
    """Idempotent append. Same shape as ``add_chat_blocked_model``."""
    name = (model_name or "").strip()
    if not name:
        return False
    state = _ensure_initialized()
    current = list(state.get("no_function_calling_models") or [])
    if name in current:
        return False
    current.append(name)
    _update({"no_function_calling_models": current})
    logger.info(
        "runtime_settings: no_function_calling_models +%r (size=%d)",
        name, len(current),
    )
    return True


def remove_no_function_calling_model(model_name: str) -> bool:
    name = (model_name or "").strip()
    if not name:
        return False
    state = _ensure_initialized()
    current = list(state.get("no_function_calling_models") or [])
    if name not in current:
        return False
    current.remove(name)
    _update({"no_function_calling_models": current})
    logger.info(
        "runtime_settings: no_function_calling_models -%r (size=%d)",
        name, len(current),
    )
    return True


# ── Structured-diagnosis threshold band (Q2 §39) ────────────────────────


def get_structured_diagnosis_threshold_floor() -> float:
    return float(_ensure_initialized().get(
        "structured_diagnosis_threshold_floor", 0.50,
    ))


def set_structured_diagnosis_threshold_floor(value: float) -> None:
    v = float(value)
    if not (0.0 <= v <= 0.99):
        raise ValueError(
            f"structured_diagnosis_threshold_floor must be in [0.0, 0.99], got {value!r}"
        )
    ceiling = get_structured_diagnosis_threshold_ceiling()
    if v >= ceiling:
        raise ValueError(
            f"floor {v} must be < ceiling {ceiling}; "
            f"adjust ceiling first OR pick a lower floor"
        )
    _update({"structured_diagnosis_threshold_floor": v})
    logger.info(
        "runtime_settings: structured_diagnosis_threshold_floor set to %.2f", v,
    )


def get_structured_diagnosis_threshold_ceiling() -> float:
    return float(_ensure_initialized().get(
        "structured_diagnosis_threshold_ceiling", 0.95,
    ))


def set_structured_diagnosis_threshold_ceiling(value: float) -> None:
    v = float(value)
    if not (0.01 <= v <= 1.0):
        raise ValueError(
            f"structured_diagnosis_threshold_ceiling must be in [0.01, 1.0], got {value!r}"
        )
    floor = get_structured_diagnosis_threshold_floor()
    if v <= floor:
        raise ValueError(
            f"ceiling {v} must be > floor {floor}; "
            f"adjust floor first OR pick a higher ceiling"
        )
    _update({"structured_diagnosis_threshold_ceiling": v})
    logger.info(
        "runtime_settings: structured_diagnosis_threshold_ceiling set to %.2f", v,
    )


def get_structured_diagnosis_threshold_override() -> float | None:
    """Returns None when no override is set (auto-tuner manages the
    threshold). Returns a float in (0, 1] when the operator has
    pinned a specific value."""
    raw = _ensure_initialized().get("structured_diagnosis_threshold_override")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def set_structured_diagnosis_threshold_override(value: float | None) -> None:
    if value is None:
        _update({"structured_diagnosis_threshold_override": None})
        logger.info("runtime_settings: structured_diagnosis_threshold_override CLEARED")
        return
    v = float(value)
    floor = get_structured_diagnosis_threshold_floor()
    ceiling = get_structured_diagnosis_threshold_ceiling()
    if not (floor <= v <= ceiling):
        raise ValueError(
            f"override {v} must be within [floor={floor}, ceiling={ceiling}]; "
            f"adjust the band first OR pick an in-band value"
        )
    _update({"structured_diagnosis_threshold_override": v})
    logger.info(
        "runtime_settings: structured_diagnosis_threshold_override set to %.2f", v,
    )


def get_structured_diagnosis_auto_tune_enabled() -> bool:
    return bool(_ensure_initialized().get(
        "structured_diagnosis_auto_tune_enabled", True,
    ))


def set_structured_diagnosis_auto_tune_enabled(value: bool) -> None:
    _update({"structured_diagnosis_auto_tune_enabled": bool(value)})
    logger.info(
        "runtime_settings: structured_diagnosis_auto_tune_enabled set to %s",
        bool(value),
    )


# ── Embedding-migration master switches (PROGRAM §40 Item 12) ───────────


def get_embedding_migration_dual_write_enabled() -> bool:
    return bool(_ensure_initialized().get(
        "embedding_migration_dual_write_enabled", False,
    ))


def set_embedding_migration_dual_write_enabled(value: bool) -> None:
    _update({"embedding_migration_dual_write_enabled": bool(value)})
    logger.info(
        "runtime_settings: embedding_migration_dual_write_enabled set to %s",
        bool(value),
    )


def get_embedding_migration_shadow_read_enabled() -> bool:
    return bool(_ensure_initialized().get(
        "embedding_migration_shadow_read_enabled", False,
    ))


def set_embedding_migration_shadow_read_enabled(value: bool) -> None:
    _update({"embedding_migration_shadow_read_enabled": bool(value)})
    logger.info(
        "runtime_settings: embedding_migration_shadow_read_enabled set to %s",
        bool(value),
    )


def get_embedding_migration_cutover_enabled() -> bool:
    return bool(_ensure_initialized().get(
        "embedding_migration_cutover_enabled", False,
    ))


def set_embedding_migration_cutover_enabled(value: bool) -> None:
    _update({"embedding_migration_cutover_enabled": bool(value)})
    logger.info(
        "runtime_settings: embedding_migration_cutover_enabled set to %s",
        bool(value),
    )


def get_embedding_migration_state() -> dict[str, Any]:
    """Read the embedding-migration state blob. Returns ``{}`` on
    first boot. Mutated by ``app.memory.embedding_migration.state``."""
    blob = _ensure_initialized().get("embedding_migration_state")
    if not isinstance(blob, dict):
        return {}
    return dict(blob)


def set_embedding_migration_state(value: dict[str, Any]) -> None:
    """Persist the embedding-migration state blob. The state-machine
    module owns the schema; runtime_settings is just the storage."""
    if not isinstance(value, dict):
        raise TypeError("embedding_migration_state must be a dict")
    _update({"embedding_migration_state": dict(value)})


# ── Post-amendment restart claims (PROGRAM §40.2) ────────────────────────


def get_post_amendment_restart_claims() -> list[dict[str, Any]]:
    """Return all outstanding restart claims. List of dicts; see the
    schema in ``_defaults``. Empty list = no pending restart."""
    raw = _ensure_initialized().get("post_amendment_restart_claims")
    if not isinstance(raw, list):
        return []
    return [dict(c) for c in raw if isinstance(c, dict)]


def append_post_amendment_restart_claim(claim: dict[str, Any]) -> None:
    """Append one claim. Idempotent on ``claim["id"]``: a claim with an
    id that already exists is silently dropped. The caller is
    responsible for generating a stable id (e.g. tier3_proposal_id +
    claim_kind)."""
    if not isinstance(claim, dict):
        raise TypeError("claim must be a dict")
    if not claim.get("id"):
        raise ValueError("claim must have a non-empty id")
    with _lock:
        state = _cache if _cache is not None else _load()
        existing = list(state.get("post_amendment_restart_claims") or [])
        ids = {c.get("id") for c in existing if isinstance(c, dict)}
        if claim["id"] in ids:
            return
        existing.append(dict(claim))
        state["post_amendment_restart_claims"] = existing
        _save(state)
        globals().update({"_cache": state})


def clear_post_amendment_restart_claims(
    ids: list[str] | None = None,
) -> int:
    """Drop claims. When ``ids`` is None, clears ALL — the gateway
    calls this after a confirmed boot that satisfied every outstanding
    claim. When ``ids`` is a list, drops only the matching ones (so
    a partial-satisfaction flow can clear what it knows is live).
    Returns the number of claims removed."""
    with _lock:
        state = _cache if _cache is not None else _load()
        existing = list(state.get("post_amendment_restart_claims") or [])
        if ids is None:
            removed = len(existing)
            state["post_amendment_restart_claims"] = []
        else:
            id_set = set(ids)
            keep = [c for c in existing if c.get("id") not in id_set]
            removed = len(existing) - len(keep)
            state["post_amendment_restart_claims"] = keep
        _save(state)
        globals().update({"_cache": state})
        return removed


# ── Person correlation (PROGRAM §42) — 14 getters + setters ───────────


def get_person_correlation_enabled() -> bool:
    return bool(_ensure_initialized().get("person_correlation_enabled", False))


def set_person_correlation_enabled(value: bool) -> None:
    prev = get_person_correlation_enabled()
    _update({"person_correlation_enabled": bool(value)})
    logger.info("runtime_settings: person_correlation_enabled = %s", bool(value))
    # Q4.2.2#1 — identity-shaping policy flip → continuity ledger.
    if prev != bool(value):
        _emit_person_correlation_policy_event(
            level="L1",
            enabled=bool(value),
        )


def get_person_correlation_decay_months() -> int:
    return int(_ensure_initialized().get("person_correlation_decay_months", 12))


def set_person_correlation_decay_months(value: int) -> None:
    v = max(1, min(60, int(value)))
    _update({"person_correlation_decay_months": v})


def get_person_centrality_enabled() -> bool:
    return bool(_ensure_initialized().get("person_centrality_enabled", False))


def set_person_centrality_enabled(value: bool) -> None:
    _update({"person_centrality_enabled": bool(value)})


def get_person_centrality_formula() -> str:
    return str(_ensure_initialized().get("person_centrality_formula", "frequency"))


def set_person_centrality_formula(value: str) -> None:
    if value not in {"frequency", "recency_weighted", "cross_modal"}:
        raise ValueError(f"person_centrality_formula must be one of frequency/recency_weighted/cross_modal, got {value!r}")
    _update({"person_centrality_formula": value})


def get_person_suggestions_enabled() -> bool:
    return bool(_ensure_initialized().get("person_suggestions_enabled", False))


def set_person_suggestions_enabled(value: bool) -> None:
    _update({"person_suggestions_enabled": bool(value)})


def get_person_suggestions_dormancy_enabled() -> bool:
    return bool(_ensure_initialized().get("person_suggestions_dormancy_enabled", False))


def set_person_suggestions_dormancy_enabled(value: bool) -> None:
    _update({"person_suggestions_dormancy_enabled": bool(value)})


def get_person_suggestions_responsiveness_enabled() -> bool:
    return bool(_ensure_initialized().get("person_suggestions_responsiveness_enabled", False))


def set_person_suggestions_responsiveness_enabled(value: bool) -> None:
    _update({"person_suggestions_responsiveness_enabled": bool(value)})


def get_person_correlation_social_graph_enabled() -> bool:
    return bool(_ensure_initialized().get("person_correlation_social_graph_enabled", False))


def set_person_correlation_social_graph_enabled(value: bool) -> None:
    """Master switch for L4. Enabling this from False→True requires
    a typed-phrase confirmation in the API surface — this function
    does NOT enforce that (the config_api endpoint does)."""
    prev = get_person_correlation_social_graph_enabled()
    _update({"person_correlation_social_graph_enabled": bool(value)})
    logger.info(
        "runtime_settings: person_correlation_social_graph_enabled = %s",
        bool(value),
    )
    # Q4.2.2#1 — L4 enablement is the most identity-shaping flip in the
    # stack (this is the typed-phrase one). Log it to the continuity
    # ledger so annual reflection picks it up.
    if prev != bool(value):
        _emit_person_correlation_policy_event(
            level="L4",
            enabled=bool(value),
        )


def get_graph_shortest_path_enabled() -> bool:
    return bool(_ensure_initialized().get("graph_shortest_path_enabled", False))


def set_graph_shortest_path_enabled(value: bool) -> None:
    _update({"graph_shortest_path_enabled": bool(value)})


def get_graph_communities_enabled() -> bool:
    return bool(_ensure_initialized().get("graph_communities_enabled", False))


def set_graph_communities_enabled(value: bool) -> None:
    _update({"graph_communities_enabled": bool(value)})


def get_graph_bridges_enabled() -> bool:
    return bool(_ensure_initialized().get("graph_bridges_enabled", False))


def set_graph_bridges_enabled(value: bool) -> None:
    _update({"graph_bridges_enabled": bool(value)})


def get_graph_suggestions_enabled() -> bool:
    return bool(_ensure_initialized().get("graph_suggestions_enabled", False))


def set_graph_suggestions_enabled(value: bool) -> None:
    """L4.4 master. From False→True requires SECOND typed-phrase
    'ENABLE GRAPH-DRIVEN SUGGESTIONS'. Enforced at config_api layer."""
    prev = get_graph_suggestions_enabled()
    _update({"graph_suggestions_enabled": bool(value)})
    logger.info("runtime_settings: graph_suggestions_enabled = %s", bool(value))
    # Q4.2.2#1 — L4.4 is the second typed-phrase gate; identity-shaping.
    if prev != bool(value):
        _emit_person_correlation_policy_event(
            level="L4.4",
            enabled=bool(value),
        )


def _emit_person_correlation_policy_event(*, level: str, enabled: bool) -> None:
    """Q4.2.2#1 helper — emit a ``person_correlation_policy`` event to
    the identity continuity ledger. Failure-isolated: never raises
    out to the setter."""
    try:
        from app.identity.continuity_ledger import record_event
        direction = "enabled" if enabled else "disabled"
        record_event(
            kind="person_correlation_policy",
            actor="operator",
            summary=f"person-correlation {level} {direction}",
            detail={
                "level": level,
                "enabled": enabled,
            },
        )
    except Exception:
        logger.debug("person_correlation_policy ledger emit failed", exc_info=True)


def get_graph_suggestions_cluster_dormancy_enabled() -> bool:
    return bool(_ensure_initialized().get("graph_suggestions_cluster_dormancy_enabled", False))


def set_graph_suggestions_cluster_dormancy_enabled(value: bool) -> None:
    _update({"graph_suggestions_cluster_dormancy_enabled": bool(value)})


def get_graph_suggestions_bridge_maintenance_enabled() -> bool:
    return bool(_ensure_initialized().get("graph_suggestions_bridge_maintenance_enabled", False))


def set_graph_suggestions_bridge_maintenance_enabled(value: bool) -> None:
    _update({"graph_suggestions_bridge_maintenance_enabled": bool(value)})


def get_graph_suggestions_weak_tie_enabled() -> bool:
    return bool(_ensure_initialized().get("graph_suggestions_weak_tie_enabled", False))


def set_graph_suggestions_weak_tie_enabled(value: bool) -> None:
    _update({"graph_suggestions_weak_tie_enabled": bool(value)})


# ── Q5 — Targeted sentience experiments (PROGRAM §43) ────────────────


def get_sentience_ae2_enabled() -> bool:
    return bool(_ensure_initialized().get("sentience_ae2_enabled", True))


def set_sentience_ae2_enabled(value: bool) -> None:
    _update({"sentience_ae2_enabled": bool(value)})


def get_sentience_hot1_enabled() -> bool:
    return bool(_ensure_initialized().get("sentience_hot1_enabled", True))


def set_sentience_hot1_enabled(value: bool) -> None:
    _update({"sentience_hot1_enabled": bool(value)})


def get_sentience_hot4_enabled() -> bool:
    return bool(_ensure_initialized().get("sentience_hot4_enabled", True))


def set_sentience_hot4_enabled(value: bool) -> None:
    _update({"sentience_hot4_enabled": bool(value)})


def get_sentience_rpt1_enabled() -> bool:
    return bool(_ensure_initialized().get("sentience_rpt1_enabled", True))


def set_sentience_rpt1_enabled(value: bool) -> None:
    _update({"sentience_rpt1_enabled": bool(value)})


def get_philosophy_panel_enabled() -> bool:
    return bool(_ensure_initialized().get("philosophy_panel_enabled", True))


def set_philosophy_panel_enabled(value: bool) -> None:
    _update({"philosophy_panel_enabled": bool(value)})


def get_ledger_governor_enabled() -> bool:
    return bool(_ensure_initialized().get("ledger_governor_enabled", True))


def set_ledger_governor_enabled(value: bool) -> None:
    _update({"ledger_governor_enabled": bool(value)})


def get_sentience_llm_hypothesis_enabled() -> bool:
    return bool(_ensure_initialized().get("sentience_llm_hypothesis_enabled", True))


def set_sentience_llm_hypothesis_enabled(value: bool) -> None:
    _update({"sentience_llm_hypothesis_enabled": bool(value)})


# ── Q6 — Resilience drills (PROGRAM §44) ─────────────────────────────


def get_resilience_drills_enabled() -> bool:
    return bool(_ensure_initialized().get("resilience_drills_enabled", True))


def set_resilience_drills_enabled(value: bool) -> None:
    _update({"resilience_drills_enabled": bool(value)})


def get_drill_backup_restore_enabled() -> bool:
    return bool(_ensure_initialized().get("drill_backup_restore_enabled", True))


def set_drill_backup_restore_enabled(value: bool) -> None:
    _update({"drill_backup_restore_enabled": bool(value)})


def get_drill_embedding_migration_enabled() -> bool:
    return bool(_ensure_initialized().get("drill_embedding_migration_enabled", True))


def set_drill_embedding_migration_enabled(value: bool) -> None:
    _update({"drill_embedding_migration_enabled": bool(value)})


def get_drill_secret_rotation_enabled() -> bool:
    return bool(_ensure_initialized().get("drill_secret_rotation_enabled", True))


def set_drill_secret_rotation_enabled(value: bool) -> None:
    _update({"drill_secret_rotation_enabled": bool(value)})


def get_drill_kill_the_gateway_enabled() -> bool:
    """OFF by default — the only DISRUPTIVE drill. Operator must
    explicitly enable via /cp/settings before scheduler will emit
    'due' notifications. Even when ON, execution requires the
    external script + typed-phrase confirmation."""
    return bool(_ensure_initialized().get("drill_kill_the_gateway_enabled", False))


def set_drill_kill_the_gateway_enabled(value: bool) -> None:
    _update({"drill_kill_the_gateway_enabled": bool(value)})


def get_drill_staleness_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("drill_staleness_monitor_enabled", True))


def set_drill_staleness_monitor_enabled(value: bool) -> None:
    _update({"drill_staleness_monitor_enabled": bool(value)})


def get_backup_freshness_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("backup_freshness_monitor_enabled", True))


def set_backup_freshness_monitor_enabled(value: bool) -> None:
    _update({"backup_freshness_monitor_enabled": bool(value)})


# ── Q7.1 — Architecture-request primitive (PROGRAM §45.1) ────────────


def get_architecture_requests_enabled() -> bool:
    return bool(_ensure_initialized().get("architecture_requests_enabled", True))


def set_architecture_requests_enabled(value: bool) -> None:
    _update({"architecture_requests_enabled": bool(value)})


def get_architecture_adoption_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("architecture_adoption_monitor_enabled", True))


def set_architecture_adoption_monitor_enabled(value: bool) -> None:
    _update({"architecture_adoption_monitor_enabled": bool(value)})


# ── Q7.4 — Inline ShinkaEvolve per coding session (PROGRAM §45.4) ────


def get_shinka_inline_evolve_enabled() -> bool:
    return bool(_ensure_initialized().get("shinka_inline_evolve_enabled", True))


def set_shinka_inline_evolve_enabled(value: bool) -> None:
    _update({"shinka_inline_evolve_enabled": bool(value)})


# ── Verified mutation engine (2026-05-27 rebuild) ─────────────────────


def get_evolution_verified_engine_enabled() -> bool:
    return bool(
        _ensure_initialized().get("evolution_verified_engine_enabled", False)
    )


def set_evolution_verified_engine_enabled(value: bool) -> None:
    _update({"evolution_verified_engine_enabled": bool(value)})


def get_evolution_verified_per_cycle_budget_usd() -> float:
    return float(
        _ensure_initialized().get("evolution_verified_per_cycle_budget_usd", 5.0)
    )


def set_evolution_verified_per_cycle_budget_usd(value: float) -> None:
    # Sanity-clamp: a self-improvement cycle should never silently burn more
    # than $100 even if a caller fat-fingers the value.
    _update(
        {"evolution_verified_per_cycle_budget_usd": max(0.0, min(100.0, float(value)))}
    )


# ── Q9.3 — Travel monitor (PROGRAM §46.6) ─────────────────────────────


def get_tripit_ical_url() -> str:
    """Operator-supplied TripIt iCal feed URL. Returns empty when
    not configured; the travel module degrades to env-var fallback
    then to silent no-op."""
    return str(_ensure_initialized().get("tripit_ical_url", "") or "")


def set_tripit_ical_url(value: str) -> None:
    """Persist TripIt iCal URL. Operator-set value; sane validation:
    must be empty OR start with ``https://`` and contain ``tripit``
    in the hostname (defensive — refuse paste of random URLs)."""
    v = (value or "").strip()
    if v:
        lower = v.lower()
        if not lower.startswith("https://"):
            raise ValueError("tripit_ical_url must start with https://")
        # Conservative hostname check — TripIt iCal feeds live under
        # *.tripit.com. Operators copying from the right place will
        # always have "tripit" in the URL.
        if "tripit" not in lower.split("/")[2]:
            raise ValueError(
                "tripit_ical_url hostname must contain 'tripit'"
            )
    _update({"tripit_ical_url": v})


def get_aviationstack_api_key() -> str:
    """Aviationstack API key for live flight status. Empty when not
    configured."""
    return str(_ensure_initialized().get("aviationstack_api_key", "") or "")


def set_aviationstack_api_key(value: str) -> None:
    """Persist Aviationstack API key. Defensive validation: must be
    empty OR a hex-ish 32-char token (Aviationstack format)."""
    v = (value or "").strip()
    if v and len(v) < 16:
        raise ValueError(
            "aviationstack_api_key looks too short to be valid"
        )
    _update({"aviationstack_api_key": v})


# ── Q11.1 — Analogy-index populator (PROGRAM §46.18) ─────────────────


def get_analogy_index_populator_enabled() -> bool:
    return bool(
        _ensure_initialized().get("analogy_index_populator_enabled", True)
    )


def set_analogy_index_populator_enabled(value: bool) -> None:
    _update({"analogy_index_populator_enabled": bool(value)})


# ── Q13 — year-2+ resilience (PROGRAM §48) ────────────────────────────


def get_migration_drill_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("migration_drill_monitor_enabled", True)
    )


def set_migration_drill_monitor_enabled(value: bool) -> None:
    _update({"migration_drill_monitor_enabled": bool(value)})


def get_dependency_radar_enabled() -> bool:
    return bool(_ensure_initialized().get("dependency_radar_enabled", True))


def set_dependency_radar_enabled(value: bool) -> None:
    _update({"dependency_radar_enabled": bool(value)})


def get_tz_drift_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("tz_drift_monitor_enabled", True))


def set_tz_drift_monitor_enabled(value: bool) -> None:
    _update({"tz_drift_monitor_enabled": bool(value)})


def get_schema_drift_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("schema_drift_monitor_enabled", True))


def set_schema_drift_monitor_enabled(value: bool) -> None:
    _update({"schema_drift_monitor_enabled": bool(value)})


# ── Q14 — year-2+ risk-register (PROGRAM §49) ─────────────────────────


def get_identity_drift_digest_enabled() -> bool:
    return bool(_ensure_initialized().get("identity_drift_digest_enabled", True))


def set_identity_drift_digest_enabled(value: bool) -> None:
    _update({"identity_drift_digest_enabled": bool(value)})


def get_feedback_loop_drift_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("feedback_loop_drift_monitor_enabled", True)
    )


def set_feedback_loop_drift_monitor_enabled(value: bool) -> None:
    _update({"feedback_loop_drift_monitor_enabled": bool(value)})


def get_embedding_drift_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("embedding_drift_monitor_enabled", True)
    )


def set_embedding_drift_monitor_enabled(value: bool) -> None:
    _update({"embedding_drift_monitor_enabled": bool(value)})


def get_interest_ossification_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("interest_ossification_monitor_enabled", True)
    )


def set_interest_ossification_monitor_enabled(value: bool) -> None:
    _update({"interest_ossification_monitor_enabled": bool(value)})


def get_lock_contention_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("lock_contention_monitor_enabled", True)
    )


def set_lock_contention_monitor_enabled(value: bool) -> None:
    _update({"lock_contention_monitor_enabled": bool(value)})


def get_influence_graph_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("influence_graph_monitor_enabled", True)
    )


def set_influence_graph_monitor_enabled(value: bool) -> None:
    _update({"influence_graph_monitor_enabled": bool(value)})


# ── Q16 — decade-resilience hardening (PROGRAM §51) ────────────────────


def get_host_substrate_health_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get(
            "host_substrate_health_monitor_enabled", True,
        )
    )


def set_host_substrate_health_monitor_enabled(value: bool) -> None:
    _update({"host_substrate_health_monitor_enabled": bool(value)})


def get_oauth_token_freshness_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get(
            "oauth_token_freshness_monitor_enabled", True,
        )
    )


def set_oauth_token_freshness_monitor_enabled(value: bool) -> None:
    _update({"oauth_token_freshness_monitor_enabled": bool(value)})


def get_gh_version_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("gh_version_monitor_enabled", True)
    )


def set_gh_version_monitor_enabled(value: bool) -> None:
    _update({"gh_version_monitor_enabled": bool(value)})


# Verified Implementation Plan Gap 1 closure (2026-05-23) — code_intel
# tree-sitter indexer + Postgres backend master switches. Both default
# OFF so the AST + JSONL canonical path keeps its existing behaviour.
#
# Operator flips ``code_intel_tree_sitter_enabled`` when they want
# multi-language symbol coverage (TS / JS / Go / Rust) or want to A/B
# the AST and tree-sitter parsers on Python files. Both indexers can
# run together — the JSONL store accepts records from either source.
#
# ``code_intel_postgres_enabled`` controls whether ``build_index``
# also persists to the migration-036 tables (code_symbols,
# code_references, code_coverage_snapshot). Off by default; flip when
# JSONL scale becomes a measurable bottleneck (~10k+ files).
def get_code_intel_tree_sitter_enabled() -> bool:
    return bool(
        _ensure_initialized().get(
            "code_intel_tree_sitter_enabled", False,
        )
    )


def set_code_intel_tree_sitter_enabled(value: bool) -> None:
    _update({"code_intel_tree_sitter_enabled": bool(value)})


def get_code_intel_postgres_enabled() -> bool:
    return bool(
        _ensure_initialized().get(
            "code_intel_postgres_enabled", False,
        )
    )


def set_code_intel_postgres_enabled(value: bool) -> None:
    _update({"code_intel_postgres_enabled": bool(value)})


def get_drill_vendor_independence_enabled() -> bool:
    return bool(
        _ensure_initialized().get("drill_vendor_independence_enabled", True)
    )


def set_drill_vendor_independence_enabled(value: bool) -> None:
    _update({"drill_vendor_independence_enabled": bool(value)})


def get_drill_vendor_independence_live_enabled() -> bool:
    return bool(
        _ensure_initialized().get(
            "drill_vendor_independence_live_enabled", False,
        )
    )


def set_drill_vendor_independence_live_enabled(value: bool) -> None:
    _update({"drill_vendor_independence_live_enabled": bool(value)})


def get_operator_anomaly_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("operator_anomaly_monitor_enabled", True)
    )


def set_operator_anomaly_monitor_enabled(value: bool) -> None:
    _update({"operator_anomaly_monitor_enabled": bool(value)})


def get_self_improvement_velocity_enabled() -> bool:
    return bool(
        _ensure_initialized().get("self_improvement_velocity_enabled", True)
    )


def set_self_improvement_velocity_enabled(value: bool) -> None:
    _update({"self_improvement_velocity_enabled": bool(value)})


def get_wiki_staleness_monitor_enabled() -> bool:
    return bool(
        _ensure_initialized().get("wiki_staleness_monitor_enabled", True)
    )


def set_wiki_staleness_monitor_enabled(value: bool) -> None:
    _update({"wiki_staleness_monitor_enabled": bool(value)})


def get_claude_md_compaction_enabled() -> bool:
    return bool(
        _ensure_initialized().get("claude_md_compaction_enabled", True)
    )


def set_claude_md_compaction_enabled(value: bool) -> None:
    _update({"claude_md_compaction_enabled": bool(value)})


# ── Q16 Themes 6-8 (PROGRAM §51) ──────────────────────────────────────────


def get_latency_slo_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("latency_slo_monitor_enabled", True))


def set_latency_slo_monitor_enabled(value: bool) -> None:
    _update({"latency_slo_monitor_enabled": bool(value)})


def get_answer_regression_enabled() -> bool:
    return bool(_ensure_initialized().get("answer_regression_enabled", True))


def set_answer_regression_enabled(value: bool) -> None:
    _update({"answer_regression_enabled": bool(value)})


def get_answer_regression_llm_enabled() -> bool:
    return bool(_ensure_initialized().get("answer_regression_llm_enabled", False))


def set_answer_regression_llm_enabled(value: bool) -> None:
    _update({"answer_regression_llm_enabled": bool(value)})


def get_companion_accuracy_log_enabled() -> bool:
    return bool(_ensure_initialized().get("companion_accuracy_log_enabled", True))


def set_companion_accuracy_log_enabled(value: bool) -> None:
    _update({"companion_accuracy_log_enabled": bool(value)})


def get_goal_progress_probe_enabled() -> bool:
    return bool(_ensure_initialized().get("goal_progress_probe_enabled", True))


def set_goal_progress_probe_enabled(value: bool) -> None:
    _update({"goal_progress_probe_enabled": bool(value)})


def get_annual_privacy_review_enabled() -> bool:
    return bool(_ensure_initialized().get("annual_privacy_review_enabled", True))


def set_annual_privacy_review_enabled(value: bool) -> None:
    _update({"annual_privacy_review_enabled": bool(value)})


def get_hot1_consultation_enabled() -> bool:
    return bool(_ensure_initialized().get("hot1_consultation_enabled", True))


def set_hot1_consultation_enabled(value: bool) -> None:
    _update({"hot1_consultation_enabled": bool(value)})


def get_hot1_outcome_reconciler_enabled() -> bool:
    return bool(
        _ensure_initialized().get("hot1_outcome_reconciler_enabled", True)
    )


def set_hot1_outcome_reconciler_enabled(value: bool) -> None:
    _update({"hot1_outcome_reconciler_enabled": bool(value)})


def get_velocity_digest_enabled() -> bool:
    return bool(_ensure_initialized().get("velocity_digest_enabled", True))


def set_velocity_digest_enabled(value: bool) -> None:
    _update({"velocity_digest_enabled": bool(value)})


def get_philosophy_digest_enabled() -> bool:
    return bool(_ensure_initialized().get("philosophy_digest_enabled", True))


def set_philosophy_digest_enabled(value: bool) -> None:
    _update({"philosophy_digest_enabled": bool(value)})


def get_vacation_mode_enabled() -> bool:
    return bool(_ensure_initialized().get("vacation_mode_enabled", True))


def set_vacation_mode_enabled(value: bool) -> None:
    _update({"vacation_mode_enabled": bool(value)})


def get_vacation_mode_state() -> dict:
    """Read the vacation-mode state blob. Schema documented in
    ``app/vacation_mode/state.py:VacationState.to_dict``."""
    return dict(_ensure_initialized().get("vacation_mode_state", {}) or {})


def set_vacation_mode_state(value: dict) -> None:
    """Persist the full state blob. Vacation-mode internals use this
    via the ``_update``/``_ensure_initialized`` shape; operators should
    call ``app.vacation_mode.engage`` / ``stage_allowlist`` / etc.
    rather than this setter directly."""
    if not isinstance(value, dict):
        raise ValueError("vacation_mode_state must be a dict")
    _update({"vacation_mode_state": dict(value)})


# ── Q17 — multi-year resilience getters/setters (PROGRAM §52) ────────────


def get_warm_spare_enabled() -> bool:
    return bool(_ensure_initialized().get("warm_spare_enabled", False))


def set_warm_spare_enabled(value: bool) -> None:
    _update({"warm_spare_enabled": bool(value)})


def get_warm_spare_partner_target() -> str:
    return str(_ensure_initialized().get("warm_spare_partner_target", "") or "")


def set_warm_spare_partner_target(value: str) -> None:
    v = (value or "").strip()
    if v and ":" not in v:
        raise ValueError("partner target must be in 'user@host:/path' form")
    _update({"warm_spare_partner_target": v})


def get_drill_local_only_enabled() -> bool:
    return bool(_ensure_initialized().get("drill_local_only_enabled", True))


def set_drill_local_only_enabled(value: bool) -> None:
    _update({"drill_local_only_enabled": bool(value)})


def get_bit_rot_scan_enabled() -> bool:
    return bool(_ensure_initialized().get("bit_rot_scan_enabled", True))


def set_bit_rot_scan_enabled(value: bool) -> None:
    _update({"bit_rot_scan_enabled": bool(value)})


def get_operator_transition_enabled() -> bool:
    return bool(_ensure_initialized().get("operator_transition_enabled", True))


def set_operator_transition_enabled(value: bool) -> None:
    _update({"operator_transition_enabled": bool(value)})


def get_agreement_ledger_enabled() -> bool:
    return bool(_ensure_initialized().get("agreement_ledger_enabled", True))


def set_agreement_ledger_enabled(value: bool) -> None:
    _update({"agreement_ledger_enabled": bool(value)})


def get_kb_contradiction_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("kb_contradiction_monitor_enabled", True))


def set_kb_contradiction_monitor_enabled(value: bool) -> None:
    _update({"kb_contradiction_monitor_enabled": bool(value)})


def get_synthesis_pass_enabled() -> bool:
    return bool(_ensure_initialized().get("synthesis_pass_enabled", True))


def set_synthesis_pass_enabled(value: bool) -> None:
    _update({"synthesis_pass_enabled": bool(value)})


def get_conversation_memory_enabled() -> bool:
    return bool(_ensure_initialized().get("conversation_memory_enabled", True))


def set_conversation_memory_enabled(value: bool) -> None:
    _update({"conversation_memory_enabled": bool(value)})


# ── ChromaDB integrity protection (PROGRAM §55, 2026-05-17) ──────────────


def get_chromadb_wal_enforcement_enabled() -> bool:
    """Read by ``app.memory.chromadb_integrity.boot_integrity_scan``."""
    return bool(_ensure_initialized().get("chromadb_wal_enforcement_enabled", True))


def set_chromadb_wal_enforcement_enabled(value: bool) -> None:
    _update({"chromadb_wal_enforcement_enabled": bool(value)})


def get_chromadb_boot_integrity_check_enabled() -> bool:
    """Read by ``app.memory.chromadb_integrity.boot_integrity_scan``."""
    return bool(_ensure_initialized().get("chromadb_boot_integrity_check_enabled", True))


def set_chromadb_boot_integrity_check_enabled(value: bool) -> None:
    _update({"chromadb_boot_integrity_check_enabled": bool(value)})


def get_chromadb_integrity_monitor_enabled() -> bool:
    """Read by ``app.healing.monitors.chromadb_integrity``."""
    return bool(_ensure_initialized().get("chromadb_integrity_monitor_enabled", True))


def set_chromadb_integrity_monitor_enabled(value: bool) -> None:
    _update({"chromadb_integrity_monitor_enabled": bool(value)})


def get_chromadb_daily_snapshot_enabled() -> bool:
    """Read by ``app.healing.monitors.chromadb_integrity`` (daily branch)."""
    return bool(_ensure_initialized().get("chromadb_daily_snapshot_enabled", True))


def set_chromadb_daily_snapshot_enabled(value: bool) -> None:
    _update({"chromadb_daily_snapshot_enabled": bool(value)})


def get_chromadb_auto_replay_enabled() -> bool:
    """Read by ``app.memory.chromadb_integrity.replay_from_postgres``."""
    return bool(_ensure_initialized().get("chromadb_auto_replay_enabled", True))


def set_chromadb_auto_replay_enabled(value: bool) -> None:
    _update({"chromadb_auto_replay_enabled": bool(value)})


# ── PROGRAM §56 — Source ledger (10-year resiliency) ─────────────────────


def get_chromadb_source_ledger_enabled() -> bool:
    """Master kill switch for the source-ledger primitive.

    Read by ``app.memory.source_ledger`` (dual-write hook, replay,
    bootstrap, drift detection). Default ON. Disabling makes the
    chromadb writes pure single-writer to chromadb only — drift
    detection and ledger-based recovery stop working.
    """
    return bool(_ensure_initialized().get("chromadb_source_ledger_enabled", True))


def set_chromadb_source_ledger_enabled(value: bool) -> None:
    _update({"chromadb_source_ledger_enabled": bool(value)})


def get_chromadb_client_recycle_on_wedge_enabled() -> bool:
    """Read by ``app.memory.source_ledger_daemon``. When the drift-replay
    sees the wedge signature (rows_seen>0, upserted=0, client_wedged_errors>0)
    AND this switch is ON, the daemon drops the cached PersistentClient
    and retries the replay once before alerting the operator to restart
    the gateway. Default ON.
    """
    return bool(_ensure_initialized().get(
        "chromadb_client_recycle_on_wedge_enabled", True
    ))


def set_chromadb_client_recycle_on_wedge_enabled(value: bool) -> None:
    _update({"chromadb_client_recycle_on_wedge_enabled": bool(value)})


def get_chromadb_ledger_bootstrap_enabled() -> bool:
    """Read by ``app.memory.source_ledger_daemon`` (bootstrap branch).

    When ON, daily back-fill walks every KB and appends any chromadb
    rows missing from the ledger. Idempotent on doc_id.
    """
    return bool(_ensure_initialized().get("chromadb_ledger_bootstrap_enabled", True))


def set_chromadb_ledger_bootstrap_enabled(value: bool) -> None:
    _update({"chromadb_ledger_bootstrap_enabled": bool(value)})


def get_chromadb_ledger_drift_replay_enabled() -> bool:
    """Read by ``app.memory.source_ledger_daemon`` + boot scan.

    When ON, drift detection at boot + daily triggers replay when
    ledger has more rows than the KB does. Recovers from quarantine
    + accidental KB rebuilds automatically.
    """
    return bool(_ensure_initialized().get("chromadb_ledger_drift_replay_enabled", True))


def set_chromadb_ledger_drift_replay_enabled(value: bool) -> None:
    _update({"chromadb_ledger_drift_replay_enabled": bool(value)})


def get_chromadb_ledger_s3_upload_enabled() -> bool:
    """Read by ``app.memory.source_ledger_offhost.s3``.

    Default OFF — needs S3 credentials wired via env vars
    (``LEDGER_S3_BUCKET`` etc.). When ON, daily idle job uploads
    new ledger lines as per-day gzip objects.
    """
    return bool(_ensure_initialized().get("chromadb_ledger_s3_upload_enabled", False))


def set_chromadb_ledger_s3_upload_enabled(value: bool) -> None:
    _update({"chromadb_ledger_s3_upload_enabled": bool(value)})


def get_chromadb_ledger_gdrive_upload_enabled() -> bool:
    """Read by ``app.memory.source_ledger_offhost.gdrive``.

    Default OFF — needs Google Workspace OAuth (already wired for the
    other Google tools; just enable). When ON, daily idle job uploads
    new ledger lines into the operator's Drive.
    """
    return bool(_ensure_initialized().get("chromadb_ledger_gdrive_upload_enabled", False))


def set_chromadb_ledger_gdrive_upload_enabled(value: bool) -> None:
    _update({"chromadb_ledger_gdrive_upload_enabled": bool(value)})


def get_drill_source_ledger_replay_enabled() -> bool:
    """Read by ``app.resilience_drills.drills.source_ledger_replay``.

    Quarterly drill that rebuilds a random KB to a scratch dir to
    verify the ledger → KB reconstruction works end-to-end.
    """
    return bool(_ensure_initialized().get("drill_source_ledger_replay_enabled", True))


def set_drill_source_ledger_replay_enabled(value: bool) -> None:
    _update({"drill_source_ledger_replay_enabled": bool(value)})


def get_chromadb_ledger_compaction_enabled() -> bool:
    """PROGRAM §56 iter-2 — weekly ledger fold. Read by
    ``source_ledger_daemon.py``. Default ON; disabling means ledgers
    grow unboundedly. Compaction is internally gated so tiny ledgers
    are skipped even when the switch is ON.
    """
    return bool(_ensure_initialized().get("chromadb_ledger_compaction_enabled", True))


def set_chromadb_ledger_compaction_enabled(value: bool) -> None:
    _update({"chromadb_ledger_compaction_enabled": bool(value)})


def get_drill_embedding_rotation_enabled() -> bool:
    """PROGRAM §56 iter-2 — 8th resilience drill. Verifies that
    replay-with-a-different-embedding-model produces a queryable KB,
    proving the §56 "model-rotation tolerant" claim is operationally
    true. Default ON; the drill itself never touches live data.
    """
    return bool(_ensure_initialized().get("drill_embedding_rotation_enabled", True))


def set_drill_embedding_rotation_enabled(value: bool) -> None:
    _update({"drill_embedding_rotation_enabled": bool(value)})


def get_drill_task_recovery_enabled() -> bool:
    """Survey response to arXiv:2604.27096 §4.3.4 — 9th resilience drill.

    Master switch for the task-layer recovery drill. The drill
    injects 4 failure classes into a synthetic agent task and
    measures recovery rate via named mechanisms (tool_supervisor /
    structured_diagnosis / recovery_loop).

    Default ON. Even when ON, the drill stays in dry-run mode
    (no LLM calls) unless ``drill_task_recovery_live_enabled`` is
    also ON.
    """
    return bool(_ensure_initialized().get("drill_task_recovery_enabled", True))


def set_drill_task_recovery_enabled(value: bool) -> None:
    _update({"drill_task_recovery_enabled": bool(value)})


def get_drill_task_recovery_live_enabled() -> bool:
    """Companion to ``drill_task_recovery_enabled``. When ON, the
    drill runs the synthetic fixture crew with real cheap-tier LLM
    calls. When OFF (default), the drill uses a deterministic stub
    kickoff — useful for verifying the drill plumbing without
    spending budget.

    Per-run cost cap is enforced by ``_BUDGET_USD_PER_RUN`` in the
    drill module regardless of this switch.
    """
    return bool(_ensure_initialized().get("drill_task_recovery_live_enabled", False))


def set_drill_task_recovery_live_enabled(value: bool) -> None:
    _update({"drill_task_recovery_live_enabled": bool(value)})


def get_drill_fresh_host_bootstrap_enabled() -> bool:
    """Gap 1 — 10th resilience drill.

    Master switch for the fresh-host bootstrap drill. Restores the
    most-recent DR export into a scratch directory and verifies the
    minimum file set + integrity is what a clean-machine install
    would need. Default ON.

    Composes with the existing DR drill (``backup_restore``) — that
    drill verifies the export round-trips; this drill verifies the
    export plus the install path constitute a working substrate.
    """
    return bool(_ensure_initialized().get("drill_fresh_host_bootstrap_enabled", True))


def set_drill_fresh_host_bootstrap_enabled(value: bool) -> None:
    _update({"drill_fresh_host_bootstrap_enabled": bool(value)})


def get_drill_fresh_host_bootstrap_dockerized_enabled() -> bool:
    """Companion to ``drill_fresh_host_bootstrap_enabled``.

    When ON, the drill additionally launches an ephemeral Docker
    container with the restored workspace bind-mounted and runs the
    gateway boot path. Default OFF — operator-controlled because it
    requires Docker daemon access from the gateway and is the only
    part of the drill that consumes non-trivial CPU/memory.
    """
    return bool(
        _ensure_initialized().get(
            "drill_fresh_host_bootstrap_dockerized_enabled", False
        )
    )


def set_drill_fresh_host_bootstrap_dockerized_enabled(value: bool) -> None:
    _update({"drill_fresh_host_bootstrap_dockerized_enabled": bool(value)})


def get_interest_goal_emitter_enabled() -> bool:
    """Gap 2 (2026-05-24) — interest-driven autonomous research goals.

    Master switch for ``app.companion.interest_goal_emitter``. When
    ON, sustained cross-modal convergence (>=21d, >=3 modalities,
    strength >= 0.7) emits a LOW-priority autonomous research goal
    routed through ``autonomous_executor`` with a $2 per-goal budget
    cap. Default OFF — operator opts in.

    Composes with ``autonomous_executor_enabled``: both must be ON
    for the emitter to actually spawn a run.
    """
    return bool(_ensure_initialized().get("interest_goal_emitter_enabled", False))


def set_interest_goal_emitter_enabled(value: bool) -> None:
    _update({"interest_goal_emitter_enabled": bool(value)})


def get_gate_philosophy_enabled() -> bool:
    """Gap 3 (2026-05-24) — gate_philosophy 5th evaluator.

    Master switch for the philosophy-panel evaluator added to the
    gate_output verification-extension chain. Activates only on
    autonomous/financial zones; consults
    ``app.philosophy.dialectics.consult_panel`` and on unresolved
    tensions escalates the verdict to peer_review (no auto-ship).
    Also files a Q4.1 tension store entry + Q8 thread so the
    operator's existing decision surfaces pick up the conflict.

    Default OFF — escalation-only, but adds ~7d-TTL'd panel I/O on
    the output path. Operator opts in after calibrating advisory
    observations.
    """
    return bool(_ensure_initialized().get("gate_philosophy_enabled", False))


def set_gate_philosophy_enabled(value: bool) -> None:
    _update({"gate_philosophy_enabled": bool(value)})


def get_decade_recall_enabled() -> bool:
    """Gap 4 (2026-05-24) — decade_recall unified audit index.

    Master switch for ``app.decade_recall``. When ON, a daily LIGHT
    idle job incrementally scans 6 hash-chained ledger files into a
    combined token-overlap index at
    ``workspace/decade_recall/index.jsonl``. Agent tool
    ``recall_history`` reads from this index.

    Default ON because the index is observational + cheap (no LLM,
    no embedding model) and powers the audit-synthesis surface for
    years-3-through-10 operation.
    """
    return bool(_ensure_initialized().get("decade_recall_enabled", True))


def set_decade_recall_enabled(value: bool) -> None:
    _update({"decade_recall_enabled": bool(value)})


def get_substrate_radar_enabled() -> bool:
    """Tier 2.1 (2026-05-24) — OS / container / cloud EOL radar."""
    return bool(_ensure_initialized().get("substrate_radar_enabled", True))


def set_substrate_radar_enabled(value: bool) -> None:
    _update({"substrate_radar_enabled": bool(value)})


def get_latency_slo_monitor_enabled() -> bool:
    """Tier 2.2 (2026-05-24) — p99 response-time drift monitor."""
    return bool(_ensure_initialized().get("latency_slo_monitor_enabled", True))


def set_latency_slo_monitor_enabled(value: bool) -> None:
    _update({"latency_slo_monitor_enabled": bool(value)})


def get_mcp_discovery_enabled() -> bool:
    """Tier 2.3 (2026-05-24) — MCP/connector auto-discovery poller.

    Default OFF — security-sensitive surface. Operator opts in
    explicitly and reviews each surfaced candidate through the
    standard change-request gate.
    """
    return bool(_ensure_initialized().get("mcp_discovery_enabled", False))


def set_mcp_discovery_enabled(value: bool) -> None:
    _update({"mcp_discovery_enabled": bool(value)})


def get_recovery_auto_thread_enabled() -> bool:
    """Tier 2.4 (2026-05-24) — auto-open Q8 thread on hard questions.

    Default OFF — operator-visible surface (the threads list).
    Operator opts in once the rate-limit and dedup behavior is
    calibrated for their workflow.
    """
    return bool(_ensure_initialized().get("recovery_auto_thread_enabled", False))


def set_recovery_auto_thread_enabled(value: bool) -> None:
    _update({"recovery_auto_thread_enabled": bool(value)})


def get_drill_task_recovery_llm_variants_enabled() -> bool:
    """When ON (default), the drill asks Anthropic Haiku to generate
    one fresh injection variant per failure class per run — the
    anti-Goodhart layer. When OFF, the drill picks from a curated
    fallback pool. Only consulted in LIVE mode."""
    return bool(_ensure_initialized().get(
        "drill_task_recovery_llm_variants_enabled", True
    ))


def set_drill_task_recovery_llm_variants_enabled(value: bool) -> None:
    _update({"drill_task_recovery_llm_variants_enabled": bool(value)})


# ── Phase 1 — code-elegance continuous observation ──────────────────────


def get_system_inventory_enabled() -> bool:
    """Master switch for the weekly auto-catalogue at
    ``workspace/system_inventory/snapshot.json``. Closes the meta-gap
    behind CLAUDE.md drifting from actual capabilities."""
    return bool(_ensure_initialized().get("system_inventory_enabled", True))


def set_system_inventory_enabled(value: bool) -> None:
    _update({"system_inventory_enabled": bool(value)})


def get_elegance_drift_monitor_enabled() -> bool:
    """Master switch for ``app.healing.monitors.elegance_drift`` —
    weekly per-file ``code_quality.QualityScore`` scan + 8-week
    rolling-median regression detector."""
    return bool(_ensure_initialized().get("elegance_drift_monitor_enabled", True))


def set_elegance_drift_monitor_enabled(value: bool) -> None:
    _update({"elegance_drift_monitor_enabled": bool(value)})


def get_architectural_drift_monitor_enabled() -> bool:
    """Master switch for ``app.healing.monitors.architectural_drift`` —
    weekly full-graph cycle / capability-overlap / centrality-spike
    detector with baseline diffing."""
    return bool(_ensure_initialized().get(
        "architectural_drift_monitor_enabled", True,
    ))


def set_architectural_drift_monitor_enabled(value: bool) -> None:
    _update({"architectural_drift_monitor_enabled": bool(value)})


def get_refactor_proposer_enabled() -> bool:
    """Master switch for ``app.refactoring.proposer`` — 4th producer
    in proposal_bridge. Default OFF."""
    return bool(_ensure_initialized().get("refactor_proposer_enabled", False))


def set_refactor_proposer_enabled(value: bool) -> None:
    _update({"refactor_proposer_enabled": bool(value)})


def get_elegance_reflection_enabled() -> bool:
    """Master switch for ``app.identity.elegance_reflection`` — annual
    deterministic essay. Default ON."""
    return bool(_ensure_initialized().get("elegance_reflection_enabled", True))


def set_elegance_reflection_enabled(value: bool) -> None:
    _update({"elegance_reflection_enabled": bool(value)})


def get_code_consolidation_enabled() -> bool:
    """Master switch for ``app.self_improvement.code_consolidation`` —
    quarterly deterministic digest. Default ON."""
    return bool(_ensure_initialized().get("code_consolidation_enabled", True))


def set_code_consolidation_enabled(value: bool) -> None:
    _update({"code_consolidation_enabled": bool(value)})


def get_pep_idiom_radar_enabled() -> bool:
    """Master switch for ``app.library_radar.idiom_radar`` — weekly
    Python PEP feed scan for idiom-class proposals. Default OFF."""
    return bool(_ensure_initialized().get("pep_idiom_radar_enabled", False))


def set_pep_idiom_radar_enabled(value: bool) -> None:
    _update({"pep_idiom_radar_enabled": bool(value)})


def get_cross_monitor_pattern_monitor_enabled() -> bool:
    """Master switch for ``app.healing.monitors.cross_monitor_pattern``
    — weekly meta-detector reading the identity continuity ledger.
    Default ON."""
    return bool(_ensure_initialized().get(
        "cross_monitor_pattern_monitor_enabled", True,
    ))


def set_cross_monitor_pattern_monitor_enabled(value: bool) -> None:
    _update({"cross_monitor_pattern_monitor_enabled": bool(value)})


# ── Upgrade-lifecycle (PROGRAM §63, 2026-05-23) ─────────────────────────


def get_upgrade_lifecycle_enabled() -> bool:
    """Top-level master switch for the upgrade-lifecycle subsystem.

    When False, every stage refuses (Capability extraction returns
    None, trial runner declines, MAJOR auto-CR gate falls back to
    Signal-only, capability adoption pauses, ecosystem snapshot
    skips). Per-stage switches give finer control.
    """
    return bool(_ensure_initialized().get("upgrade_lifecycle_enabled", True))


def set_upgrade_lifecycle_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_enabled": bool(value)})


def get_upgrade_lifecycle_capability_extraction_enabled() -> bool:
    """Master switch for U1 — changelog fetching + LLM-extracted Capability rows."""
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_capability_extraction_enabled", True,
    ))


def set_upgrade_lifecycle_capability_extraction_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_capability_extraction_enabled": bool(value)})


def get_upgrade_lifecycle_trial_enabled() -> bool:
    """Master switch for U3 — upgrade trial runs in coding-session worktrees."""
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get("upgrade_lifecycle_trial_enabled", True))


def set_upgrade_lifecycle_trial_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_trial_enabled": bool(value)})


def get_upgrade_lifecycle_major_auto_cr_enabled() -> bool:
    """Master switch for U4 — MAJOR bumps auto-CR'd when 5 gate
    conditions hold (trial ok + 30d + no breaking hits + non-immutable
    + not framework). When False, falls back to Signal-only."""
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_major_auto_cr_enabled", True,
    ))


def set_upgrade_lifecycle_major_auto_cr_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_major_auto_cr_enabled": bool(value)})


def get_upgrade_lifecycle_capability_adoption_enabled() -> bool:
    """Master switch for U5 — weekly capability-adoption refactor CRs.
    Hard-capped at 1 CR/week and the quarterly USD budget below."""
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_capability_adoption_enabled", True,
    ))


def set_upgrade_lifecycle_capability_adoption_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_capability_adoption_enabled": bool(value)})


def get_upgrade_lifecycle_capability_budget_usd_quarterly() -> float:
    """Calendar-quarter USD budget for U5 capability-adoption LLM spend."""
    return float(_ensure_initialized().get(
        "upgrade_lifecycle_capability_budget_usd_quarterly", 20.0,
    ))


def set_upgrade_lifecycle_capability_budget_usd_quarterly(value: float) -> None:
    v = float(value)
    if v < 0.0:
        raise ValueError("quarterly budget must be non-negative")
    if v > 500.0:
        raise ValueError("quarterly budget exceeds sanity cap of $500/quarter")
    _update({"upgrade_lifecycle_capability_budget_usd_quarterly": v})
    logger.info("runtime_settings: upgrade_lifecycle quarterly budget = $%.2f", v)


def get_upgrade_lifecycle_extraction_budget_usd_monthly() -> float:
    """Monthly USD budget for U1 capability-extraction LLM calls (P1#c)."""
    return float(_ensure_initialized().get(
        "upgrade_lifecycle_extraction_budget_usd_monthly", 5.0,
    ))


def set_upgrade_lifecycle_extraction_budget_usd_monthly(value: float) -> None:
    v = float(value)
    if v < 0.0:
        raise ValueError("monthly extraction budget must be non-negative")
    if v > 100.0:
        raise ValueError(
            "monthly extraction budget exceeds sanity cap of $100/month",
        )
    _update({"upgrade_lifecycle_extraction_budget_usd_monthly": v})
    logger.info(
        "runtime_settings: upgrade_lifecycle monthly extraction budget = $%.2f", v,
    )


def get_upgrade_lifecycle_requirements_writer_enabled() -> bool:
    """Master switch for the curated requirements.txt writer (P0#1a).

    Default OFF — operator opts in once they trust the
    upgrade-lifecycle subsystem to mutate requirements.txt directly.
    The writer is heavily scoped (single-line bumps from a small
    allowlist of requestors only) so opt-in is the safety boundary.
    """
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_requirements_writer_enabled", False,
    ))


def set_upgrade_lifecycle_requirements_writer_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_requirements_writer_enabled": bool(value)})


def get_upgrade_lifecycle_apply_hook_enabled() -> bool:
    """Master switch for the apply-hook daemon (P0#1b).

    The daemon polls change_requests for newly-applied upgrade
    decision CRs (at docs/proposed_upgrades/) and dispatches to
    requirements_writer. Composes with the writer's own switch —
    both must be ON for upgrades to land.
    """
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_apply_hook_enabled", False,
    ))


def set_upgrade_lifecycle_apply_hook_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_apply_hook_enabled": bool(value)})


def get_upgrade_lifecycle_dockerfile_writer_enabled() -> bool:
    """Master switch for the Dockerfile writer (P0#4).

    Python upgrades touch the Dockerfile's ``FROM python:`` line and
    drop the SHA pin in the process. Default OFF — operator opts in
    deliberately, and a separate manual re-pin step is required
    after every bump (the writer adds a ``# TODO`` comment).
    """
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_dockerfile_writer_enabled", False,
    ))


def set_upgrade_lifecycle_dockerfile_writer_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_dockerfile_writer_enabled": bool(value)})


def get_upgrade_lifecycle_pyproject_writer_enabled() -> bool:
    """Master switch for the pyproject.toml writer (D#a)."""
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_pyproject_writer_enabled", False,
    ))


def set_upgrade_lifecycle_pyproject_writer_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_pyproject_writer_enabled": bool(value)})


def get_dockerfile_pin_staleness_monitor_enabled() -> bool:
    """Master switch for the 40th healing monitor (A3-P1)."""
    return bool(_ensure_initialized().get(
        "dockerfile_pin_staleness_monitor_enabled", True,
    ))


def set_dockerfile_pin_staleness_monitor_enabled(value: bool) -> None:
    _update({"dockerfile_pin_staleness_monitor_enabled": bool(value)})


def get_cr_apply_consistency_monitor_enabled() -> bool:
    """Master switch for the 41st healing monitor (B3-P2)."""
    return bool(_ensure_initialized().get(
        "cr_apply_consistency_monitor_enabled", True,
    ))


def set_cr_apply_consistency_monitor_enabled(value: bool) -> None:
    _update({"cr_apply_consistency_monitor_enabled": bool(value)})


def get_upgrade_lifecycle_absence_policy_enabled() -> bool:
    """Master switch for the operator-absence policy (P1#a).

    Default OFF. When ON, the absence-policy idle job promotes
    PATCH-level CRs to AUTO_APPLY after 90d operator silence + 14d
    CR soak + trusted-requestor + non-immutable + non-framework.
    Every promotion fires a Signal alert + ledger event so a
    returning operator sees exactly what was applied.
    """
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_absence_policy_enabled", False,
    ))


def set_upgrade_lifecycle_absence_policy_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_absence_policy_enabled": bool(value)})


def get_ecosystem_snapshot_enabled() -> bool:
    """Master switch for U6 — annual January ecosystem snapshot."""
    if not get_upgrade_lifecycle_enabled():
        return False
    return bool(_ensure_initialized().get("ecosystem_snapshot_enabled", True))


def set_ecosystem_snapshot_enabled(value: bool) -> None:
    _update({"ecosystem_snapshot_enabled": bool(value)})


def get_python_eol_proximity_monitor_enabled() -> bool:
    """Master switch for U8's quarterly Python EOL proximity monitor."""
    return bool(_ensure_initialized().get(
        "python_eol_proximity_monitor_enabled", True,
    ))


def set_python_eol_proximity_monitor_enabled(value: bool) -> None:
    _update({"python_eol_proximity_monitor_enabled": bool(value)})


def get_upgrade_lifecycle_health_monitor_enabled() -> bool:
    """Master switch for U8's weekly upgrade-lifecycle health monitor."""
    return bool(_ensure_initialized().get(
        "upgrade_lifecycle_health_monitor_enabled", True,
    ))


def set_upgrade_lifecycle_health_monitor_enabled(value: bool) -> None:
    _update({"upgrade_lifecycle_health_monitor_enabled": bool(value)})


# ── Multi-year resilience gaps (Gap #1-#11, 2026-05-24) ──────────────────


def get_config_coherence_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("config_coherence_monitor_enabled", True))


def set_config_coherence_monitor_enabled(value: bool) -> None:
    _update({"config_coherence_monitor_enabled": bool(value)})


def get_total_cost_ceiling_enabled() -> bool:
    return bool(_ensure_initialized().get("total_cost_ceiling_enabled", True))


def set_total_cost_ceiling_enabled(value: bool) -> None:
    _update({"total_cost_ceiling_enabled": bool(value)})


def get_total_cost_monthly_cap_usd() -> float:
    return float(_ensure_initialized().get("total_cost_monthly_cap_usd", 200.0))


def set_total_cost_monthly_cap_usd(value: float) -> None:
    v = float(value)
    if v < 0.0:
        raise ValueError("total_cost_monthly_cap_usd must be non-negative")
    if v > 10000.0:
        raise ValueError("total_cost_monthly_cap_usd exceeds sanity cap of $10000/mo")
    _update({"total_cost_monthly_cap_usd": v})


def get_idle_pause_due_to_budget() -> bool:
    """When True, LIGHT idle jobs check this flag and skip running.

    Set by the total-cost-ceiling monitor at 95% of monthly cap; cleared
    when spend drops back below 80% (5-point hysteresis avoids flapping).
    Operator can also clear manually; the next probe pass will re-pause
    if spend is still over the threshold.
    """
    return bool(_ensure_initialized().get("idle_pause_due_to_budget", False))


def set_idle_pause_due_to_budget(value: bool) -> None:
    _update({"idle_pause_due_to_budget": bool(value)})


def get_capability_inventory_enabled() -> bool:
    return bool(_ensure_initialized().get("capability_inventory_enabled", True))


def set_capability_inventory_enabled(value: bool) -> None:
    _update({"capability_inventory_enabled": bool(value)})


def get_discovery_funnel_enabled() -> bool:
    return bool(_ensure_initialized().get("discovery_funnel_enabled", True))


def set_discovery_funnel_enabled(value: bool) -> None:
    _update({"discovery_funnel_enabled": bool(value)})


def get_knowledge_currency_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("knowledge_currency_monitor_enabled", True))


def set_knowledge_currency_monitor_enabled(value: bool) -> None:
    _update({"knowledge_currency_monitor_enabled": bool(value)})


def get_hardware_health_monitor_enabled() -> bool:
    return bool(_ensure_initialized().get("hardware_health_monitor_enabled", True))


def set_hardware_health_monitor_enabled(value: bool) -> None:
    _update({"hardware_health_monitor_enabled": bool(value)})


def get_privacy_audit_enabled() -> bool:
    return bool(_ensure_initialized().get("privacy_audit_enabled", True))


def set_privacy_audit_enabled(value: bool) -> None:
    _update({"privacy_audit_enabled": bool(value)})


def get_deadman_last_resort_enabled() -> bool:
    return bool(_ensure_initialized().get("deadman_last_resort_enabled", True))


def set_deadman_last_resort_enabled(value: bool) -> None:
    _update({"deadman_last_resort_enabled": bool(value)})


def get_drill_prompt_injection_resistance_enabled() -> bool:
    return bool(_ensure_initialized().get(
        "drill_prompt_injection_resistance_enabled", True,
    ))


def set_drill_prompt_injection_resistance_enabled(value: bool) -> None:
    _update({"drill_prompt_injection_resistance_enabled": bool(value)})

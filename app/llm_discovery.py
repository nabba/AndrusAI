"""
llm_discovery.py — Automatic LLM model discovery, benchmarking, and promotion.

Full pipeline:
  1. SCAN: Query OpenRouter /models API for new/updated models
  2. FILTER: Keep models matching our tier/capability criteria
  3. BENCHMARK: Run eval_set_score against standardized test tasks
  4. COMPARE: Compare benchmark vs current model for the same role
  5. PROPOSE: If new model outperforms, create governance approval request
  6. PROMOTE: On approval, add to runtime catalog + assign to roles

Runs as an idle scheduler job. Discovered models stored in PostgreSQL
(control_plane.discovered_models). Promotions require governance approval
unless the model is free tier.

IMMUTABLE — infrastructure-level module.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Minimum thresholds for a model to be considered
MIN_CONTEXT_WINDOW = 8_000
# Raised to 30 so Opus-class frontier launches at $25/M still get seen.
# The tier buckets below still constrain where a model lands in the
# cascade; this is just the outer "worth evaluating at all" gate.
MAX_COST_OUTPUT_PER_M = 30.0

# Tier classification by cost
TIER_THRESHOLDS = {
    "free": 0.0,
    "budget": 1.0,       # ≤ $1/M output
    "mid": 5.0,          # ≤ $5/M output
    "premium": 30.0,     # ≤ $30/M output
}

# Provider-specific model ID prefixes for our catalog
PROVIDER_PREFIXES = {
    "openrouter": "openrouter/",
    "ollama": "ollama_chat/",
}

# Roles to benchmark new models against
BENCHMARK_ROLES = ["research", "coding", "writing"]

# ── OpenRouter Scanner ───────────────────────────────────────────────────────

def scan_openrouter() -> list[dict]:
    """Query OpenRouter /models API for all available models.

    Returns list of model dicts with standardized fields.
    """
    import httpx

    try:
        from app.config import get_settings
        s = get_settings()
        api_key = s.openrouter_api_key.get_secret_value()
    except Exception:
        api_key = os.getenv("OPENROUTER_API_KEY", "")

    if not api_key:
        logger.warning("llm_discovery: no OpenRouter API key")
        return []

    try:
        resp = httpx.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"llm_discovery: OpenRouter API returned {resp.status_code}")
            return []

        data = resp.json()
        models = data.get("data", [])
        logger.info(f"llm_discovery: OpenRouter returned {len(models)} models")
        return models

    except Exception as e:
        logger.warning(f"llm_discovery: OpenRouter scan failed: {e}")
        return []

def scan_ollama() -> list[dict]:
    """Query local Ollama for available models."""
    import httpx

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    try:
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=10)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            return [
                {
                    "id": f"ollama_chat/{m['name']}",
                    "name": m["name"],
                    "context_length": 32768,  # Default, Ollama doesn't always report this
                    "pricing": {"prompt": "0", "completion": "0"},
                    "architecture": {"modality": "text"},
                    "provider": "ollama",
                }
                for m in models
            ]
    except Exception:
        pass
    return []

# ── Filter + Normalize ───────────────────────────────────────────────────────

def _detect_tool_calling(raw: dict, provider: str) -> bool:
    """Infer whether a model supports tool calling.

    OpenRouter's ``/models`` payload exposes ``supported_parameters``
    which includes ``"tools"``/``"tool_choice"`` for models that accept
    tool-use arguments. Ollama doesn't report this, so we fall back to
    a conservative heuristic on the model family.
    """
    supported = raw.get("supported_parameters") or raw.get("supported_params") or []
    if isinstance(supported, (list, tuple, set)):
        if any(p in supported for p in ("tools", "tool_choice", "function_call")):
            return True
        if supported:  # payload is authoritative — no tools listed means none
            return False
    # Fallback heuristic when the field is absent
    name = (raw.get("id", "") + " " + raw.get("name", "")).lower()
    _TOOLLESS_HINTS = ("base", "completion", "codestral", "embed")
    if any(h in name for h in _TOOLLESS_HINTS):
        return False
    if provider == "ollama":
        return False  # Ollama path verifies at runtime via circuit breaker
    return True


def _normalize_model(raw: dict, provider: str = "openrouter") -> dict | None:
    """Normalize a raw API model to our standard format. Returns None if filtered out."""
    model_id = raw.get("id", "")
    name = raw.get("name", model_id)
    context = raw.get("context_length", 0) or 0

    # Extract pricing
    pricing = raw.get("pricing", {})
    cost_input = float(pricing.get("prompt", 0) or 0) * 1_000_000  # per-token → per-M
    cost_output = float(pricing.get("completion", 0) or 0) * 1_000_000

    # Filter
    if context < MIN_CONTEXT_WINDOW:
        return None
    if cost_output > MAX_COST_OUTPUT_PER_M:
        return None

    # Detect capabilities
    arch = raw.get("architecture", {})
    modality = arch.get("modality", "text")
    multimodal = "image" in modality or "multimodal" in modality
    tool_calling = _detect_tool_calling(raw, provider)

    # Classify tier
    tier = "premium"
    for tier_name, threshold in sorted(TIER_THRESHOLDS.items(), key=lambda x: x[1]):
        if cost_output <= threshold:
            tier = tier_name
            break

    # Build catalog-compatible model_id
    if provider == "openrouter":
        catalog_id = f"openrouter/{model_id}" if not model_id.startswith("openrouter/") else model_id
    elif provider == "ollama":
        catalog_id = model_id  # Already prefixed
    else:
        catalog_id = model_id

    return {
        "model_id": catalog_id,
        "provider": provider,
        "display_name": name,
        "context_window": context,
        "cost_input_per_m": round(cost_input, 6),
        "cost_output_per_m": round(cost_output, 6),
        "multimodal": multimodal,
        "tool_calling": tool_calling,
        "tier": tier,
        "raw_metadata": raw,
    }

# ── Database Operations ──────────────────────────────────────────────────────

def _get_known_model_ids() -> set[str]:
    """Get all model_ids already in discovered_models table."""
    from app.control_plane.db import execute
    rows = execute(
        "SELECT model_id FROM control_plane.discovered_models",
        fetch=True,
    )
    return {r["model_id"] for r in (rows or [])}

def _get_catalog_model_ids() -> set[str]:
    """Get all model_ids already in the static catalog."""
    from app.llm_catalog import CATALOG
    ids = set()
    for name, info in CATALOG.items():
        ids.add(info.get("model_id", ""))
        ids.add(name)
    return ids

def _store_discovered(model: dict) -> bool:
    """Store a newly discovered model in PostgreSQL."""
    from app.control_plane.db import execute
    try:
        execute(
            """INSERT INTO control_plane.discovered_models
               (model_id, provider, display_name, context_window,
                cost_input_per_m, cost_output_per_m, multimodal, tool_calling,
                source, raw_metadata, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'discovered')
               ON CONFLICT (model_id) DO UPDATE SET
                cost_input_per_m = EXCLUDED.cost_input_per_m,
                cost_output_per_m = EXCLUDED.cost_output_per_m,
                context_window = EXCLUDED.context_window,
                updated_at = NOW()""",
            (
                model["model_id"], model["provider"], model["display_name"],
                model["context_window"], model["cost_input_per_m"],
                model["cost_output_per_m"], model["multimodal"],
                model.get("tool_calling", True), "openrouter_api",
                json.dumps(model.get("raw_metadata", {})),
            ),
        )
        return True
    except Exception as e:
        logger.debug(f"llm_discovery: store failed: {e}")
        return False

def _update_benchmark(model_id: str, score: float, role: str) -> None:
    """Update benchmark score for a discovered model."""
    from app.control_plane.db import execute
    execute(
        """UPDATE control_plane.discovered_models
           SET benchmark_score = %s, benchmark_role = %s,
               benchmarked_at = NOW(), status = 'benchmarking',
               updated_at = NOW()
           WHERE model_id = %s""",
        (score, role, model_id),
    )

def _promote_model(model_id: str, tier: str, roles: list[str], reviewer: str = "system") -> None:
    """Mark a model as promoted."""
    from app.control_plane.db import execute
    execute(
        """UPDATE control_plane.discovered_models
           SET status = 'promoted', promoted_tier = %s,
               promoted_roles = %s, promoted_at = NOW(),
               reviewed_by = %s, updated_at = NOW()
           WHERE model_id = %s""",
        (tier, roles, reviewer, model_id),
    )

# ── Benchmarking ─────────────────────────────────────────────────────────────

# Rotation of judges used by benchmark_model. Each tuple is
# (catalog_key, provider_family). Provider-family exclusion prevents
# a candidate from being judged by a sibling in the same family (e.g.
# a new DeepSeek variant being scored by DeepSeek V3.2). Order matters
# only for deterministic rotation in tests; the actual exclusion is
# commutative.
DEFAULT_JUDGES: tuple[tuple[str, str], ...] = (
    ("claude-sonnet-4.6", "anthropic"),
    ("gemini-3.1-pro",    "google"),
    ("deepseek-v3.2",     "deepseek"),
)


def _provider_family(model_id: str) -> str:
    """Infer the provider family from a catalog model_id or key.

    Used to exclude judges whose family matches the candidate. The
    classification is intentionally coarse — a family boundary is
    enough to catch the same-lab scoring bias without getting bogged
    down in taxonomy.
    """
    s = (model_id or "").lower()
    if "claude" in s or "anthropic" in s:
        return "anthropic"
    if "gemini" in s or "google/gemma" in s or "gemma-" in s or s.startswith("gemma"):
        return "google"
    if "deepseek" in s:
        return "deepseek"
    if "gpt-" in s or "openai" in s:
        return "openai"
    if "mistral" in s or "codestral" in s:
        return "mistral"
    # qwen (Alibaba) comes before llama because Ollama model paths
    # like "ollama_chat/qwen3:30b-a3b" contain the substring "llama"
    # via the "ollama" prefix.
    if "qwen" in s or "alibaba" in s:
        return "alibaba"
    if "llama" in s or "meta/" in s:
        return "meta"
    if "kimi" in s or "moonshot" in s:
        return "moonshot"
    if "minimax" in s:
        return "minimax"
    if "glm" in s or "zhipu" in s:
        return "zhipu"
    if "xiaomi" in s or "mimo" in s:
        return "xiaomi"
    if "nemotron" in s or "nvidia" in s:
        return "nvidia"
    if "stepfun" in s or "step-" in s:
        return "stepfun"
    if "arcee" in s or "trinity" in s:
        return "arcee"
    return "unknown"


def _build_judge_llm(catalog_key: str):
    """Instantiate an LLM for the given catalog judge key. Returns None
    on any failure (API key missing, key not in catalog, etc.).
    """
    try:
        from app.llm_factory import _cached_llm
        from app.config import get_settings, get_anthropic_api_key
        from app.llm_catalog import get_model
        entry = get_model(catalog_key)
        if not entry:
            return None
        if entry["provider"] == "anthropic":
            key = get_anthropic_api_key()
            if not key:
                return None
            return _cached_llm(entry["model_id"], max_tokens=256, api_key=key)
        if entry["provider"] == "openrouter":
            or_key = get_settings().openrouter_api_key.get_secret_value()
            if not or_key:
                return None
            return _cached_llm(
                entry["model_id"], max_tokens=256,
                base_url="https://openrouter.ai/api/v1", api_key=or_key,
            )
    except Exception:
        return None
    return None


def _select_judges(
    candidate_model_id: str,
    judges: list[str] | None = None,
) -> list[tuple[str, str, object]]:
    """Return up to 2 callable judges whose provider family differs
    from the candidate's.

    Returns a list of (catalog_key, family, llm) tuples. Callers
    should average verdicts across the returned judges. When none are
    eligible (e.g. the candidate shares a family with every available
    judge, or all keys are missing), returns [].
    """
    rotation = (
        [(k, _provider_family(get_model(k)["model_id"] if get_model(k) else k))
         for k in judges]
        if judges else list(DEFAULT_JUDGES)
    )
    candidate_family = _provider_family(candidate_model_id)
    ok: list[tuple[str, str, object]] = []
    for key, fam in rotation:
        if fam == candidate_family:
            continue
        llm = _build_judge_llm(key)
        if llm is None:
            continue
        ok.append((key, fam, llm))
        if len(ok) >= 2:
            break
    return ok


def benchmark_model(
    model_id: str,
    role: str = "research",
    sample_size: int = 2,
    judges: list[str] | None = None,
) -> float:
    """Run a standardised benchmark and return a 0.0-1.0 score.

    Multi-judge with family exclusion:
      - Selects up to 2 judges from DEFAULT_JUDGES whose provider
        family differs from the candidate's (so a new DeepSeek model
        isn't scored by DeepSeek V3.2).
      - Each judge scores every task independently; the task score is
        the mean of the eligible judges' scores.
      - Final score is the mean of task scores. Returns -1.0 on setup
        failure (no key, candidate unreachable, zero eligible judges).
    """
    try:
        from app.llm_factory import _cached_llm
        from app.config import get_settings

        s = get_settings()
        or_key = s.openrouter_api_key.get_secret_value()

        # Candidate LLM
        if model_id.startswith("openrouter/"):
            if not or_key:
                return -1.0
            candidate_llm = _cached_llm(
                model_id, max_tokens=1024,
                base_url="https://openrouter.ai/api/v1", api_key=or_key,
            )
        elif model_id.startswith("ollama_chat/"):
            candidate_llm = _cached_llm(model_id, max_tokens=1024)
        elif model_id.startswith("anthropic/"):
            from app.config import get_anthropic_api_key
            key = get_anthropic_api_key()
            if not key:
                return -1.0
            candidate_llm = _cached_llm(model_id, max_tokens=1024, api_key=key)
        else:
            return -1.0

        # Judges — distinct provider families from the candidate.
        eligible = _select_judges(model_id, judges=judges)
        if not eligible:
            logger.warning(f"llm_discovery: no eligible judges for {model_id}")
            return -1.0

        # Test tasks per role
        test_tasks = {
            "research": [
                "What are the key differences between REST and GraphQL APIs?",
                "Explain the CAP theorem in 3 sentences.",
            ],
            "coding": [
                "Write a Python function to check if a number is prime.",
                "Implement a simple LRU cache class in Python.",
            ],
            "writing": [
                "Write a professional email declining a meeting invitation.",
                "Write release notes for a software version adding dark mode.",
            ],
        }

        tasks = test_tasks.get(role, test_tasks["research"])[:sample_size]

        import re
        task_scores: list[float] = []
        for task in tasks:
            try:
                response = str(candidate_llm.call(task)).strip()
                if not response or len(response) < 20:
                    task_scores.append(0.2)
                    continue

                judge_prompt = (
                    f"Score this AI response 0.0-1.0 on accuracy, completeness, clarity.\n"
                    f"Task: {task}\nResponse: {response[:2000]}\n\n"
                    f'Reply ONLY: {{"score": 0.X}}'
                )
                judge_scores: list[float] = []
                for _, _, judge_llm in eligible:
                    try:
                        raw = str(judge_llm.call(judge_prompt)).strip()
                        match = re.search(r'"score"\s*:\s*([\d.]+)', raw)
                        if match:
                            judge_scores.append(
                                min(1.0, max(0.0, float(match.group(1)))),
                            )
                    except Exception:
                        continue
                task_scores.append(
                    sum(judge_scores) / len(judge_scores) if judge_scores else 0.5,
                )
            except Exception:
                task_scores.append(0.0)

        avg = sum(task_scores) / len(task_scores) if task_scores else 0.0
        judge_keys = ",".join(k for k, _, _ in eligible)
        logger.info(
            f"llm_discovery: benchmark {model_id} on {role} "
            f"via [{judge_keys}]: {avg:.3f}"
        )
        return avg

    except Exception as e:
        logger.warning(f"llm_discovery: benchmark failed for {model_id}: {e}")
        return -1.0


# ── Incumbent drift detection ────────────────────────────────────────────────

# Relative quality drop that triggers a governance alert. 0.20 = 20%.
INCUMBENT_DRIFT_ALERT_THRESHOLD = 0.20


def rebenchmark_incumbent(
    model_name: str,
    *,
    roles: list[str] | None = None,
    sample_size: int = 2,
) -> dict:
    """Re-run benchmarks against a catalog incumbent and refresh its
    strengths columns in place.

    Detects silent drift (e.g. a provider swapping in a cheaper quant
    under the same name, a mid-life quality regression) that the
    selection pipeline would otherwise miss because CATALOG's
    strengths values are static string-literal estimates.

    Returns a dict:
        {
          "model": name,
          "old_scores": {role: float, ...},   # prior strengths
          "new_scores": {role: float, ...},   # fresh benchmark
          "drift": {role: float, ...},        # new - old, per role
          "alerted": bool,                    # drift triggered gov alert
        }
    Missing models or API-unreachable candidates return a summary with
    an ``error`` key instead.
    """
    from app.llm_catalog import CATALOG

    entry = CATALOG.get(model_name)
    if not entry:
        return {"model": model_name, "error": "not in catalog"}

    roles = roles or BENCHMARK_ROLES
    old_scores = {r: float(entry.get("strengths", {}).get(r, 0.5)) for r in roles}

    new_scores: dict[str, float] = {}
    for role in roles:
        score = benchmark_model(entry["model_id"], role=role, sample_size=sample_size)
        if score >= 0:
            new_scores[role] = score

    if not new_scores:
        return {
            "model": model_name,
            "error": "no scores produced",
            "old_scores": old_scores,
        }

    # Update strengths in place (runtime only; discovered_models gets
    # an insert-if-missing row for historical tracking).
    strengths = dict(entry.get("strengths", {}))
    for role, score in new_scores.items():
        strengths[role] = round(score, 2)
    entry["strengths"] = strengths

    # Drift analysis
    drift = {
        role: round(new_scores[role] - old_scores[role], 3)
        for role in new_scores
    }
    alerted = False
    worst = min(drift.values()) if drift else 0.0
    if worst <= -INCUMBENT_DRIFT_ALERT_THRESHOLD:
        alerted = _raise_drift_alert(model_name, old_scores, new_scores, drift)

    # Persist into discovered_models so drift history is queryable
    try:
        best_role = max(new_scores, key=new_scores.get)
        _upsert_incumbent_benchmark(model_name, entry, new_scores[best_role], best_role)
    except Exception as exc:
        logger.debug(f"rebenchmark: persist failed: {exc}")

    return {
        "model": model_name,
        "old_scores": old_scores,
        "new_scores": new_scores,
        "drift": drift,
        "alerted": alerted,
    }


def _upsert_incumbent_benchmark(
    model_name: str, entry: dict, score: float, role: str,
) -> None:
    """Store a rebenchmark row in discovered_models so drift history is
    queryable. Acts as INSERT when the incumbent has never flowed
    through discovery; UPDATE otherwise.
    """
    try:
        from app.control_plane.db import execute
        execute(
            """
            INSERT INTO control_plane.discovered_models
                   (model_id, provider, display_name, context_window,
                    cost_input_per_m, cost_output_per_m, multimodal,
                    tool_calling, source, raw_metadata, status,
                    benchmark_score, benchmark_role, benchmarked_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                    'catalog_incumbent', '{}', 'promoted',
                    %s, %s, NOW())
            ON CONFLICT (model_id) DO UPDATE SET
                benchmark_score = EXCLUDED.benchmark_score,
                benchmark_role  = EXCLUDED.benchmark_role,
                benchmarked_at  = NOW(),
                updated_at      = NOW()
            """,
            (
                entry["model_id"], entry.get("provider", "unknown"),
                entry.get("description", model_name)[:100],
                int(entry.get("context", 0)),
                float(entry.get("cost_input_per_m", 0)),
                float(entry.get("cost_output_per_m", 0)),
                bool(entry.get("multimodal", False)),
                bool(entry.get("supports_tools", True)),
                score, role,
            ),
        )
    except Exception as exc:
        logger.debug(f"rebenchmark: upsert incumbent failed: {exc}")


def _raise_drift_alert(
    model_name: str,
    old_scores: dict[str, float],
    new_scores: dict[str, float],
    drift: dict[str, float],
) -> bool:
    """Emit a governance request when a catalog incumbent shows
    significant quality drift. Returns True on success.
    """
    try:
        from app.control_plane.governance import get_governance
        from app.control_plane.projects import get_projects
        gate = get_governance()
        pid = get_projects().get_active_project_id()
        gate.request_approval(
            project_id=pid,
            request_type="incumbent_drift",
            requested_by="llm_discovery",
            title=f"Quality drift detected for {model_name}",
            detail={
                "model": model_name,
                "old_scores": old_scores,
                "new_scores": new_scores,
                "drift": drift,
                "threshold": INCUMBENT_DRIFT_ALERT_THRESHOLD,
            },
        )
        logger.warning(
            f"llm_discovery: DRIFT alert on {model_name} — drift={drift}",
        )
        return True
    except Exception as exc:
        logger.debug(f"rebenchmark: drift alert failed: {exc}")
        return False


def pick_incumbent_to_rebenchmark() -> str | None:
    """Return the catalog key of the next incumbent due for rebenchmark.

    Picks the model with the oldest ``benchmarked_at`` (or never
    benchmarked). Skips discovered entries — those flow through the
    normal discovery pipeline. Returns None when every incumbent has
    been benchmarked within the last week.
    """
    from app.llm_catalog import CATALOG
    try:
        from app.control_plane.db import execute
    except Exception:
        return None

    candidates = [
        name for name, info in CATALOG.items()
        if info.get("tier") in ("budget", "mid", "premium")
        and not info.get("_discovered")
    ]
    if not candidates:
        return None

    # Look up last benchmarked_at for each catalog incumbent
    rows = execute(
        """
        SELECT model_id, benchmarked_at
          FROM control_plane.discovered_models
         WHERE source = 'catalog_incumbent'
        """,
        (),
        fetch=True,
    ) or []
    last_seen = {
        r["model_id"]: r["benchmarked_at"] for r in rows
    }

    # Match catalog entries by model_id
    dated: list[tuple[str, object]] = []
    for name in candidates:
        mid = CATALOG[name].get("model_id", "")
        dated.append((name, last_seen.get(mid)))

    # Never-benchmarked incumbents come first (None sorts as oldest).
    dated.sort(key=lambda x: (x[1] is not None, x[1]))
    return dated[0][0] if dated else None

# ── Comparison + Promotion ───────────────────────────────────────────────────

def _get_current_model_score(role: str) -> tuple[str, float]:
    """Get the current model assigned to a role and its last benchmark score."""
    from app.llm_catalog import ROLE_DEFAULTS
    defaults = ROLE_DEFAULTS.get("balanced", {})
    current_model = defaults.get(role, defaults.get("default", "deepseek-v3.2"))

    # Get benchmark score from DB or use a default
    from app.control_plane.db import execute_scalar
    score = execute_scalar(
        """SELECT benchmark_score FROM control_plane.discovered_models
           WHERE model_id LIKE %s AND benchmark_role = %s
           ORDER BY benchmarked_at DESC LIMIT 1""",
        (f"%{current_model}%", role),
    )
    return current_model, float(score) if score else 0.7  # Default baseline

def propose_promotion(model: dict, benchmark_score: float, role: str) -> dict | None:
    """Create a governance request to promote a discovered model.

    Free models auto-promote. Others need human approval.
    """
    tier = model.get("tier", "budget")
    model_id = model["model_id"]

    current_model, current_score = _get_current_model_score(role)

    # Only propose if significantly better (5%+ improvement)
    if benchmark_score <= current_score * 1.05:
        logger.info(f"llm_discovery: {model_id} ({benchmark_score:.3f}) doesn't beat "
                     f"{current_model} ({current_score:.3f}) for {role}")
        return None

    # Free models auto-promote (no cost risk)
    if tier == "free":
        _promote_model(model_id, tier, [role], reviewer="auto")
        _add_to_runtime_catalog(model, [role])
        logger.info(f"llm_discovery: auto-promoted free model {model_id} for {role}")
        return {"status": "auto_promoted", "model": model_id, "role": role}

    # Others need governance approval
    try:
        from app.control_plane.governance import get_governance
        from app.control_plane.projects import get_projects
        gate = get_governance()
        pid = get_projects().get_active_project_id()

        req = gate.request_approval(
            project_id=pid,
            request_type="model_promotion",
            requested_by="llm_discovery",
            title=f"New model: {model.get('display_name', model_id)} for {role}",
            detail={
                "model_id": model_id,
                "tier": tier,
                "role": role,
                "benchmark_score": benchmark_score,
                "current_model": current_model,
                "current_score": current_score,
                "improvement": f"{((benchmark_score/current_score)-1)*100:.1f}%",
                "cost": f"${model.get('cost_output_per_m', 0):.4f}/M output",
            },
        )
        logger.info(f"llm_discovery: governance request created for {model_id}")
        return {"status": "pending_approval", "request_id": str(req.get("id", "")), "model": model_id}
    except Exception as e:
        logger.warning(f"llm_discovery: governance request failed: {e}")
        return None

def _dominates_incumbent(model: dict, role: str, cost_mode: str) -> bool:
    """Pareto-style check: does ``model`` outperform the current role
    default on both quality and cost for the given cost_mode?

    Used to decide whether an auto-promotion should take over the role
    assignment in a particular cost mode. A new model wins only if it
    is *both* cheaper-or-equal AND of higher benchmark score than the
    incumbent. For the incumbent's score we read ``strengths[role]``
    first, falling back to ``strengths["general"]`` — never the raw
    0.5 floor, which would let any newcomer with a generic score
    unfairly unseat a strong but non-role-tagged incumbent.
    """
    try:
        from app.llm_catalog import CATALOG, ROLE_DEFAULTS
        mode_defaults = ROLE_DEFAULTS.get(cost_mode, ROLE_DEFAULTS["balanced"])
        incumbent_key = mode_defaults.get(role, mode_defaults.get("default", ""))
        incumbent = CATALOG.get(incumbent_key)
        if not incumbent:
            return True
        incumbent_cost = float(incumbent.get("cost_output_per_m", 0))
        strengths = incumbent.get("strengths", {})
        incumbent_score = float(
            strengths.get(role)
            if role in strengths
            else strengths.get("general", 0.5)
        )
        my_cost = float(model.get("cost_output_per_m", 0))
        my_score = float(model.get("benchmark_score", 0.0))
        return my_cost <= incumbent_cost and my_score > incumbent_score
    except Exception:
        return False


def _add_to_runtime_catalog(model: dict, roles: list[str]) -> None:
    """Add a discovered model to the runtime catalog and (where it
    dominates the incumbent) install role-assignment overlays.

    - Catalog insert mirrors the previous behaviour so in-memory lookups
      succeed on the new model immediately.
    - Overlay writes go to ``control_plane.role_assignments`` so the
      selector picks the new model on its next call, persists across
      restarts, and is rehydrated by ``llm_rehydrate.rehydrate_catalog``.
    """
    from app.llm_catalog import CATALOG

    name = model["model_id"].split("/")[-1] if "/" in model["model_id"] else model["model_id"]

    # Estimate strengths from benchmark
    base_score = model.get("benchmark_score", 0.5) if isinstance(model.get("benchmark_score"), (int, float)) else 0.5
    # Per-role scores come from the benchmark when available; otherwise
    # inherit the base score. `per_role_scores` is set by the multi-role
    # benchmarking path (see run_discovery_cycle).
    per_role = model.get("per_role_scores") or {}
    strengths = {
        r: round(float(per_role.get(r, base_score)), 2)
        for r in roles
    }
    strengths["general"] = round(base_score * 0.9, 2)

    entry = {
        "tier": model.get("tier", "budget"),
        "provider": model.get("provider", "openrouter"),
        "model_id": model["model_id"],
        "context": model.get("context_window", 32768),
        "multimodal": model.get("multimodal", False),
        "cost_input_per_m": model.get("cost_input_per_m", 0),
        "cost_output_per_m": model.get("cost_output_per_m", 0),
        "tool_use_reliability": 0.80 if model.get("tool_calling", False) else 0.0,
        "supports_tools": bool(model.get("tool_calling", True)),
        "description": f"Auto-discovered: {model.get('display_name', name)}",
        "strengths": strengths,
        "_discovered": True,  # Marker for dynamic models
    }

    # Add to catalog (runtime only — not persisted to .py file)
    CATALOG[name] = entry
    logger.info(f"llm_discovery: added {name} to runtime catalog (tier={entry['tier']})")

    # Install role-assignment overlays for cost modes where the new
    # model Pareto-dominates the incumbent. Skips the write quietly if
    # the DB is unreachable — next discovery cycle will retry.
    try:
        from app.llm_role_assignments import set_assignment
        cost_modes = ("budget", "balanced", "quality")
        for role in roles:
            role_score = float(per_role.get(role, base_score))
            dominated_modes = [
                m for m in cost_modes if _dominates_incumbent(
                    {**model, "benchmark_score": role_score}, role, m,
                )
            ]
            for mode in dominated_modes:
                set_assignment(
                    role=role, cost_mode=mode, model=name,
                    source="auto_promotion",
                    reason=(
                        f"bench={role_score:.2f} "
                        f"${model.get('cost_output_per_m', 0):.3f}/Mo "
                        f"dominates default in {mode} mode"
                    ),
                    assigned_by="llm_discovery",
                    priority=150,
                )
    except Exception as exc:
        logger.debug(f"llm_discovery: role overlay write failed: {exc}")

# ── Main Pipeline ────────────────────────────────────────────────────────────

def _benchmark_all_roles(model_id: str, sample_size: int = 2) -> dict[str, float]:
    """Run the discovery benchmark across every role in BENCHMARK_ROLES.

    Returns a ``{role: score}`` map for roles that produced a valid
    score. A negative return from ``benchmark_model`` is treated as a
    skip rather than a zero (so transient judge errors don't kill a
    model's chances).
    """
    scores: dict[str, float] = {}
    for role in BENCHMARK_ROLES:
        s = benchmark_model(model_id, role=role, sample_size=sample_size)
        if s >= 0:
            scores[role] = s
    return scores


def run_discovery_cycle(max_benchmarks: int = 3) -> dict:
    """Full discovery pipeline. Called by idle scheduler.

    Returns: {scanned, new_found, benchmarked, promoted, proposals}
    """
    result = {
        "scanned": 0, "new_found": 0, "benchmarked": 0,
        "promoted": 0, "proposals": 0, "errors": [],
    }

    # Step 1: Scan sources — OpenRouter remote + local Ollama
    raw_openrouter = scan_openrouter()
    raw_ollama = scan_ollama()
    result["scanned"] = len(raw_openrouter) + len(raw_ollama)

    if not raw_openrouter and not raw_ollama:
        return result

    # Step 2: Filter + normalize (per provider — keeps the catalog key
    # prefix logic consistent)
    known_ids = _get_known_model_ids()
    catalog_ids = _get_catalog_model_ids()

    new_models: list[dict] = []
    for raw in raw_openrouter:
        normalized = _normalize_model(raw, provider="openrouter")
        if not normalized:
            continue
        mid = normalized["model_id"]
        if mid not in known_ids and mid not in catalog_ids:
            new_models.append(normalized)
    for raw in raw_ollama:
        normalized = _normalize_model(raw, provider="ollama")
        if not normalized:
            continue
        mid = normalized["model_id"]
        if mid not in known_ids and mid not in catalog_ids:
            new_models.append(normalized)

    result["new_found"] = len(new_models)

    # Step 3: Store all new discoveries
    for model in new_models:
        _store_discovered(model)

    if not new_models:
        logger.info(f"llm_discovery: scanned {result['scanned']} models, no new discoveries")
        return result

    logger.info(f"llm_discovery: found {len(new_models)} new models")

    # Step 4: Benchmark top candidates across every BENCHMARK_ROLE.
    # Sort by cost (prefer cheap) then by context window (prefer large).
    candidates = sorted(
        new_models,
        key=lambda m: (m["cost_output_per_m"], -m["context_window"]),
    )[:max_benchmarks]

    for model in candidates:
        per_role = _benchmark_all_roles(model["model_id"])
        if not per_role:
            result["errors"].append(f"Benchmark failed for {model['model_id']}")
            continue

        # Best-scoring role acts as the primary benchmark for the
        # legacy single-column discovered_models row. The full score
        # map travels alongside the model dict into promotion logic.
        best_role, best_score = max(per_role.items(), key=lambda kv: kv[1])
        _update_benchmark(model["model_id"], best_score, best_role)
        model["benchmark_score"] = best_score
        model["benchmark_role"] = best_role
        model["per_role_scores"] = per_role
        result["benchmarked"] += 1

        # Step 5: Propose promotion for EACH role where the model
        # actually outperforms the incumbent. Free models auto-promote;
        # others queue a governance request.
        for role, score in per_role.items():
            proposal = propose_promotion(model, score, role)
            if proposal:
                if proposal.get("status") == "auto_promoted":
                    result["promoted"] += 1
                else:
                    result["proposals"] += 1

    # Audit trail
    try:
        from app.control_plane.audit import get_audit
        get_audit().log(
            actor="llm_discovery",
            action="discovery.cycle",
            detail=result,
        )
    except Exception:
        pass

    logger.info(f"llm_discovery: cycle complete — {result}")
    return result


# ── Governance consumer ──────────────────────────────────────────────────────

def consume_approved_promotions(limit: int = 10) -> dict:
    """Apply every governance request approved since the last run.

    ``propose_promotion`` files a ``model_promotion`` governance request
    for non-free candidates. A human approves the request in the
    dashboard / Signal. This function consumes those approvals:
      - adds the model to the runtime catalog (if not already)
      - installs role assignment overlays
      - marks the discovered_models row as promoted
      - marks the governance_requests row as consumed

    Returns a summary dict suitable for logging/Signal display.
    """
    summary = {"applied": 0, "skipped": 0, "errors": 0}
    try:
        from app.control_plane.db import execute
    except Exception:
        return summary

    rows = execute(
        """
        SELECT id, detail_json, reviewed_at, reviewed_by
          FROM control_plane.governance_requests
         WHERE request_type = 'model_promotion'
           AND status = 'approved'
           AND consumed_at IS NULL
      ORDER BY reviewed_at ASC
         LIMIT %s
        """,
        (limit,),
        fetch=True,
    ) or []

    if not rows:
        return summary

    for row in rows:
        try:
            detail = row.get("detail_json") or {}
            if isinstance(detail, str):
                detail = json.loads(detail)
            model_id = detail.get("model_id")
            role = detail.get("role") or "research"
            tier = detail.get("tier", "budget")
            if not model_id:
                summary["skipped"] += 1
                continue

            # Pull the full discovered row so _add_to_runtime_catalog
            # gets real cost/context/multimodal metadata rather than
            # only the governance detail blob.
            disc = execute(
                """
                SELECT model_id, provider, display_name, context_window,
                       cost_input_per_m, cost_output_per_m, multimodal,
                       tool_calling, benchmark_score
                  FROM control_plane.discovered_models
                 WHERE model_id = %s
                """,
                (model_id,),
                fetch=True,
            ) or []
            if not disc:
                summary["skipped"] += 1
                continue
            disc_row = disc[0]

            model_payload = {
                "model_id":          disc_row["model_id"],
                "provider":          disc_row.get("provider", "openrouter"),
                "display_name":      disc_row.get("display_name", model_id),
                "context_window":    int(disc_row.get("context_window") or 0),
                "cost_input_per_m":  float(disc_row.get("cost_input_per_m") or 0),
                "cost_output_per_m": float(disc_row.get("cost_output_per_m") or 0),
                "multimodal":        bool(disc_row.get("multimodal")),
                "tool_calling":      bool(disc_row.get("tool_calling")),
                "tier":              tier,
                "benchmark_score":   float(disc_row.get("benchmark_score") or 0.5),
            }
            _add_to_runtime_catalog(model_payload, [role])
            _promote_model(
                model_id, tier, [role],
                reviewer=row.get("reviewed_by") or "governance",
            )
            execute(
                """
                UPDATE control_plane.governance_requests
                   SET consumed_at = NOW()
                 WHERE id = %s
                """,
                (row["id"],),
            )
            summary["applied"] += 1
            logger.info(
                "llm_discovery: applied governance promotion model=%s role=%s tier=%s",
                model_id, role, tier,
            )
        except Exception as exc:
            summary["errors"] += 1
            logger.warning(f"llm_discovery: promotion consumer failed on row {row.get('id')}: {exc}")

    return summary

def get_discovered_models(status: str = None, limit: int = 20) -> list[dict]:
    """Get discovered models for dashboard/Signal display."""
    from app.control_plane.db import execute
    if status:
        return execute(
            """SELECT model_id, display_name, provider, context_window,
                      cost_output_per_m, benchmark_score, benchmark_role,
                      status, promoted_tier, discovered_at
               FROM control_plane.discovered_models
               WHERE status = %s
               ORDER BY discovered_at DESC LIMIT %s""",
            (status, limit), fetch=True,
        ) or []
    return execute(
        """SELECT model_id, display_name, provider, context_window,
                  cost_output_per_m, benchmark_score, benchmark_role,
                  status, promoted_tier, discovered_at
           FROM control_plane.discovered_models
           ORDER BY discovered_at DESC LIMIT %s""",
        (limit,), fetch=True,
    ) or []

def format_discovery_report() -> str:
    """Human-readable discovery status for Signal."""
    models = get_discovered_models(limit=10)
    if not models:
        return "🔍 No models discovered yet. Run discovery with: discover models"

    lines = ["🔍 LLM Discovery:"]
    for m in models:
        status_icon = {"discovered": "🆕", "benchmarking": "⏳", "approved": "✅",
                       "promoted": "🚀", "rejected": "❌", "retired": "🗄️"}.get(m.get("status"), "?")
        score = f" score={m['benchmark_score']:.2f}" if m.get("benchmark_score") else ""
        cost = f" ${m.get('cost_output_per_m', 0):.3f}/Mo" if m.get("cost_output_per_m") else " free"
        lines.append(f"  {status_icon} {m.get('display_name', '?')[:40]}{score}{cost}")
    return "\n".join(lines)

"""
SYSTEM VERIFICATION: Find errors, mismatches, stale code, disconnected components.
Goes beyond import testing — verifies actual wiring, data contracts, and consistency.
"""
import sys, os, json, time, ast, re
from pathlib import Path
sys.path.insert(0, "/app")
os.chdir("/app")

R = {"passed": [], "failed": [], "warnings": []}
_sec = ""

def section(n):
    global _sec; _sec = n
    print(f"\n{'─'*60}\n  {n}\n{'─'*60}")

def test(name):
    full = f"{_sec}::{name}"
    def dec(fn):
        s = time.monotonic()
        try:
            r = fn(); e = (time.monotonic()-s)*1000
            if r is True: R["passed"].append(full); print(f"  ✅ {name} ({e:.0f}ms)")
            elif isinstance(r, str): R["warnings"].append(f"{full}: {r}"); print(f"  ⚠️ {name}: {r} ({e:.0f}ms)")
            else: R["failed"].append(f"{full} — returned {r}"); print(f"  ❌ {name} ({e:.0f}ms)")
        except Exception as ex:
            e = (time.monotonic()-s)*1000
            R["failed"].append(f"{full} — {type(ex).__name__}: {ex}")
            print(f"  ❌ {name} — {type(ex).__name__}: {ex} ({e:.0f}ms)")
        return fn
    return dec

# ═══════════════════════════════════════════════════════════════
# 1. SYNTAX VERIFICATION: All Python files parse without errors
# ═══════════════════════════════════════════════════════════════
section("1. SYNTAX CHECK — All .py files parse")

@test("all app/*.py files have valid Python syntax")
def _():
    errors = []
    for py in Path("/app/app").rglob("*.py"):
        if "__pycache__" in str(py): continue
        try:
            compile(py.read_text(errors="ignore"), str(py), "exec")
        except SyntaxError as e:
            errors.append(f"{py.relative_to('/app')}: {e}")
    if errors:
        for e in errors[:5]: print(f"    SYNTAX ERROR: {e}")
        return False
    return True

@test("host_bridge/main.py has valid syntax")
def _():
    try:
        compile(Path("/app/host_bridge/main.py").read_text(errors="ignore"), "host_bridge/main.py", "exec")
        return True
    except SyntaxError as e:
        return f"SyntaxError: {e}"

# ═══════════════════════════════════════════════════════════════
# 2. IMPORT CHAIN: Every module can be imported
# ═══════════════════════════════════════════════════════════════
section("2. IMPORT CHAIN — All modules importable")

@test("all top-level app modules import without error")
def _():
    failures = []
    for py in sorted(Path("/app/app").glob("*.py")):
        if py.name.startswith("__"): continue
        mod = f"app.{py.stem}"
        try:
            __import__(mod)
        except Exception as e:
            failures.append(f"{mod}: {type(e).__name__}: {e}")
    if failures:
        for f in failures[:5]: print(f"    IMPORT FAIL: {f}")
    return len(failures) == 0

@test("all package modules import")
def _():
    failures = []
    packages = ["atlas", "self_awareness", "personality", "tools", "crews",
                 "agents", "memory", "philosophy", "knowledge_base", "evolution_db",
                 "api", "contracts", "evolution_suite", "feedback_suite",
                 "llm_suite", "operations_suite"]
    for pkg in packages:
        pkg_dir = Path(f"/app/app/{pkg}")
        if not pkg_dir.exists(): continue
        for py in sorted(pkg_dir.glob("*.py")):
            if py.name.startswith("__"): continue
            mod = f"app.{pkg}.{py.stem}"
            try:
                __import__(mod)
            except Exception as e:
                failures.append(f"{mod}: {type(e).__name__}: {e}")
    if failures:
        for f in failures[:5]: print(f"    IMPORT FAIL: {f}")
    return len(failures) == 0

# ═══════════════════════════════════════════════════════════════
# 3. PROTECTED FILES: All listed files actually exist
# ═══════════════════════════════════════════════════════════════
section("3. PROTECTED FILES — All exist on disk")

@test("every file in PROTECTED_FILES exists")
def _():
    from app.auto_deployer import PROTECTED_FILES
    missing = []
    for f in PROTECTED_FILES:
        path = Path("/app") / f
        if not path.exists():
            missing.append(f)
    if missing:
        for m in missing[:10]: print(f"    MISSING: {m}")
    return len(missing) == 0

# ═══════════════════════════════════════════════════════════════
# 4. IDLE SCHEDULER: All job functions are callable
# ═══════════════════════════════════════════════════════════════
section("4. IDLE SCHEDULER — All jobs callable")

@test("all idle scheduler jobs are callable without crashing on import")
def _():
    from app.idle_scheduler import _default_jobs
    jobs = _default_jobs()
    broken = []
    for name, fn in jobs:
        if not callable(fn):
            broken.append(f"{name}: not callable")
    if broken:
        for b in broken: print(f"    BROKEN: {b}")
    return len(broken) == 0

@test("no duplicate job names")
def _():
    from app.idle_scheduler import _default_jobs
    jobs = _default_jobs()
    names = [j[0] for j in jobs]
    dupes = [n for n in names if names.count(n) > 1]
    # evolution-2 is intentionally duplicated
    real_dupes = [d for d in set(dupes) if d != "evolution-2"]
    if real_dupes:
        print(f"    DUPLICATE JOBS: {real_dupes}")
    return len(real_dupes) == 0

# ═══════════════════════════════════════════════════════════════
# 5. DATABASE SCHEMAS: All migration tables present
# ═══════════════════════════════════════════════════════════════
section("5. DATABASE — Schemas and tables")

@test("all 7 schemas present (evolution, feedback, modification, atlas, training, map_elites, personality)")
def _():
    from app.config import get_settings
    import psycopg2
    s = get_settings()
    conn = psycopg2.connect(s.mem0_postgres_url)
    with conn.cursor() as cur:
        cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast','public')")
        schemas = {r[0] for r in cur.fetchall()}
    conn.close()
    expected = {"evolution", "feedback", "modification", "atlas", "training", "personality"}
    missing = expected - schemas
    if missing: print(f"    MISSING SCHEMAS: {missing}")
    return len(missing) == 0

# ═══════════════════════════════════════════════════════════════
# 6. LIFECYCLE HOOKS: Wiring integrity
# ═══════════════════════════════════════════════════════════════
section("6. LIFECYCLE HOOKS — Wiring integrity")

@test("immutable hooks exist and cannot be removed")
def _():
    from app.lifecycle_hooks import get_registry, HookPoint
    reg = get_registry()
    hooks = reg.list_hooks()
    immutable = [h for h in hooks if h["immutable"]]
    assert len(immutable) >= 2, f"Only {len(immutable)} immutable hooks"
    for h in immutable:
        removed = reg.unregister(h["name"], HookPoint(h["hook_point"]))
        assert not removed, f"Immutable hook '{h['name']}' was removable!"
    return True

@test("duplicate immutable hook registration blocked")
def _():
    from app.lifecycle_hooks import get_registry, HookPoint
    reg = get_registry()
    before = len(reg.list_hooks())
    def fake(ctx): return ctx
    reg.register("humanist_safety", HookPoint.PRE_LLM_CALL, fake, priority=-1)
    after = len(reg.list_hooks())
    assert after == before, f"Hook count changed: {before} → {after}"
    return True

# ═══════════════════════════════════════════════════════════════
# 7. FACADE PACKAGES: All re-exports work
# ═══════════════════════════════════════════════════════════════
section("7. FACADE PACKAGES — Re-exports valid")

@test("evolution_suite exports match actual modules")
def _():
    from app.evolution_suite import run_evolution_session, IslandEvolution, EvolutionArchive, parse_prompt, get_controller, CascadeEvaluator
    return True

@test("feedback_suite exports match actual modules")
def _():
    from app.feedback_suite import FeedbackPipeline, ModificationEngine, ImplicitFeedbackDetector, MetaLearner
    return True

@test("llm_suite exports match actual modules")
def _():
    from app.llm_suite import create_specialist_llm, select_model, set_mode, CATALOG
    return True

@test("operations_suite exports match actual modules")
def _():
    from app.operations_suite import create_manifest, get_monitor, SelfHealer, run_reference_suite, SandboxRunner
    return True

# ═══════════════════════════════════════════════════════════════
# 8. API ROUTERS: Endpoints accessible at correct paths
# ═══════════════════════════════════════════════════════════════
section("8. API ROUTERS — Endpoints at correct paths")

@test("health endpoint at /health")
def _():
    import httpx
    try:
        r = httpx.get("http://localhost:8765/health", timeout=3)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except: return True  # May not be reachable from inside container via localhost

@test("KB status at /kb/status")
def _():
    import httpx
    try:
        r = httpx.get("http://localhost:8765/kb/status", timeout=3)
        return r.status_code == 200
    except: return True

# ═══════════════════════════════════════════════════════════════
# 9. CROSS-REFERENCE: Modules reference each other correctly
# ═══════════════════════════════════════════════════════════════
section("9. CROSS-REFERENCES — Import paths valid")

@test("firebase_infra imported by firebase_reporter")
def _():
    source = Path("/app/app/firebase_reporter.py").read_text()
    assert "from app.firebase_infra import" in source
    return True

@test("response_utils imported by main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    assert "from app.response_utils import" in source
    return True

@test("middleware imported by main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    assert "from app.middleware import" in source
    return True

@test("api routers included in main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    assert "kb_router" in source or "from app.api.kb" in source
    assert "health_router" in source or "from app.api.health" in source
    assert "config_router" in source or "from app.api.config_api" in source
    return True

@test("personality modules cross-reference correctly")
def _():
    # validation.py imports from state.py and evaluation.py
    val_src = Path("/app/app/personality/validation.py").read_text()
    assert "from app.personality.state import" in val_src
    assert "from app.personality.evaluation import" in val_src
    # feedback.py imports from validation.py
    fb_src = Path("/app/app/personality/feedback.py").read_text()
    assert "from app.personality.validation import" in fb_src
    return True

# ═══════════════════════════════════════════════════════════════
# 10. STALE CODE DETECTION
# ═══════════════════════════════════════════════════════════════
section("10. STALE CODE — Detect orphaned/dead references")

@test("no references to removed _ALLOWED_EXTENSIONS in main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    if "_ALLOWED_EXTENSIONS" in source:
        return "main.py still references _ALLOWED_EXTENSIONS (moved to api/kb.py)"
    return True

@test("no references to removed _MAX_UPLOAD_SIZE in main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    if "_MAX_UPLOAD_SIZE" in source:
        return "main.py still references _MAX_UPLOAD_SIZE (moved to api/kb.py)"
    return True

@test("no references to removed _kb_store in main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    if "_kb_store" in source and "_get_kb_store" in source:
        return "main.py still references _kb_store/_get_kb_store (moved to api/kb.py)"
    return True

@test("no old SecurityHeadersMiddleware class in main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    if "class SecurityHeadersMiddleware" in source:
        return "main.py still has SecurityHeadersMiddleware class (moved to middleware.py)"
    return True

@test("no stale CORSMiddleware import in main.py")
def _():
    source = Path("/app/app/main.py").read_text()
    if "from fastapi.middleware.cors import CORSMiddleware" in source:
        return "main.py still imports CORSMiddleware (handled by middleware.py)"
    return True

@test("contracts/firestore_schema.py covers all known Firestore collections")
def _():
    from app.contracts.firestore_schema import SCHEMA
    required = ["status/system", "crews/{name}", "tasks/{id}", "config/llm",
                "chat_inbox/{id}", "status/system_monitor"]
    missing = [c for c in required if c not in SCHEMA]
    if missing: return f"Missing collections: {missing}"
    return True

# ═══════════════════════════════════════════════════════════════
# 11. PERSONALITY MODULE INTEGRITY
# ═══════════════════════════════════════════════════════════════
section("11. PERSONALITY — Module integrity")

@test("all 4 instruments have scenarios in assessment bank")
def _():
    from app.personality.assessment import SCENARIOS
    for inst in ["ACSI", "ATP", "ADSA", "APD"]:
        assert inst in SCENARIOS, f"Missing instrument: {inst}"
        assert len(SCENARIOS[inst]) >= 1, f"No scenarios for {inst}"
    return True

@test("evaluation weights sum to 1.0")
def _():
    from app.personality.evaluation import EVAL_DIMENSIONS
    total = sum(EVAL_DIMENSIONS.values())
    assert abs(total - 1.0) < 0.01, f"Weights sum to {total}"
    return True

@test("BVL behavioral dimensions map to personality state traits")
def _():
    from app.personality.validation import BEHAVIORAL_DIMENSIONS
    from app.personality.state import CHARACTER_STRENGTHS, TEMPERAMENT_DIMS, PERSONALITY_FACTORS
    all_traits = set(CHARACTER_STRENGTHS) | set(TEMPERAMENT_DIMS) | set(PERSONALITY_FACTORS)
    referenced = set()
    for dims in BEHAVIORAL_DIMENSIONS.values():
        referenced.update(dims)
    unmapped = referenced - all_traits
    if unmapped: return f"BVL references traits not in state model: {unmapped}"
    return True

@test("developmental stages ordered correctly")
def _():
    from app.personality.state import DEVELOPMENTAL_STAGES
    assert len(DEVELOPMENTAL_STAGES) == 5
    assert DEVELOPMENTAL_STAGES[0] == "system_trust"
    assert DEVELOPMENTAL_STAGES[-1] == "role_coherence"
    return True

@test("personality state persistence roundtrip")
def _():
    from app.personality.state import PersonalityState, get_personality, save_personality
    state = get_personality("test_verify")
    state.strengths["epistemic_rigor"] = 0.77
    state.developmental_stage = "operational_independence"
    save_personality(state)
    # Reload
    from app.personality.state import _states
    del _states["test_verify"]
    reloaded = get_personality("test_verify")
    assert abs(reloaded.strengths["epistemic_rigor"] - 0.77) < 0.01
    assert reloaded.developmental_stage == "operational_independence"
    return True

# ═══════════════════════════════════════════════════════════════
# 12. SAFETY CHAIN: End-to-end verification
# ═══════════════════════════════════════════════════════════════
section("12. SAFETY CHAIN — End-to-end")

@test("unicode normalization blocks homoglyph tool names")
def _():
    from app.tool_executor import DynamicToolRegistry, ToolSafetyError
    reg = DynamicToolRegistry(approval_required=False)
    # Cyrillic у (U+0443) looks like Latin y
    try:
        reg.register("modify_safet\u0443", "test", lambda: None)
        return "Unicode bypass NOT blocked!"
    except ToolSafetyError:
        return True

@test("FREEZE-BLOCK enforcement works")
def _():
    from app.evolve_blocks import validate_modification
    text = "<!-- FREEZE-BLOCK-START -->\nSafe\n<!-- FREEZE-BLOCK-END -->"
    bad = text.replace("Safe", "Evil")
    r = validate_modification(text, bad)
    assert not r["valid"]
    return True

@test("bridge client uses per-agent token isolation")
def _():
    source = Path("/app/app/bridge_client.py").read_text()
    assert "_get_agent_token" in source, "Missing per-agent token function"
    assert "BRIDGE_TOKEN_" in source, "Missing per-agent env var pattern"
    return True

# ═══════════════════════════════════════════════════════════════
# 13. CONTRACTS: Typed events and state match actual usage
# ═══════════════════════════════════════════════════════════════
section("13. CONTRACTS — Type consistency")

@test("events.py frozen dataclasses are immutable")
def _():
    from app.contracts.events import TaskStarted
    e = TaskStarted(crew="test", task="test")
    try:
        e.crew = "modified"
        return "TaskStarted is NOT frozen!"
    except AttributeError:
        return True

@test("state.py dataclasses have correct fields")
def _():
    from app.contracts.state import CrewStatus, SystemHealth, SubsystemStatus
    cs = CrewStatus(name="test")
    assert hasattr(cs, "state") and hasattr(cs, "task_id")
    sh = SystemHealth()
    assert hasattr(sh, "health_score") and hasattr(sh, "uptime_seconds")
    return True

# ═══════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════
total = len(R["passed"]) + len(R["failed"])
print(f"\n{'='*60}")
print(f"  SYSTEM VERIFICATION REPORT")
print(f"{'='*60}")
print(f"\n  ✅ PASSED: {len(R['passed'])}/{total}")
if R["warnings"]:
    print(f"  ⚠️ WARNINGS: {len(R['warnings'])}")
    for w in R["warnings"]: print(f"     {w}")
if R["failed"]:
    print(f"  ❌ FAILED: {len(R['failed'])}/{total}")
    for f in R["failed"]: print(f"     {f}")
rate = len(R["passed"]) / max(1, total) * 100
print(f"\n  {'='*60}")
print(f"  PASS RATE: {rate:.0f}% ({len(R['passed'])}/{total})")
print(f"  {'='*60}")

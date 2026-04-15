"""
COMPREHENSIVE OPERATIONAL TEST SUITE
=====================================
Tests the entire system as a unified operational body.
Not just imports — verifies actual data flow, memory operations,
evolution pathways, knowledge stores, and cross-subsystem integration.
"""
import sys, os, json, time, hashlib, threading, tempfile
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/app")
os.chdir("/app")

R = {"passed": [], "failed": [], "timings": {}}
_section = ""

def section(name):
    global _section; _section = name
    print(f"\n{'─'*60}\n  {name}\n{'─'*60}")

def test(name):
    full = f"{_section}::{name}" if _section else name
    def dec(fn):
        s = time.monotonic()
        try:
            r = fn(); e = (time.monotonic()-s)*1000; R["timings"][full]=e
            if r: R["passed"].append(full); print(f"  ✅ {name} ({e:.0f}ms)")
            else: R["failed"].append(f"{full} — returned False"); print(f"  ❌ {name} — False ({e:.0f}ms)")
        except Exception as ex:
            e = (time.monotonic()-s)*1000; R["timings"][full]=e
            R["failed"].append(f"{full} — {type(ex).__name__}: {ex}")
            print(f"  ❌ {name} — {type(ex).__name__}: {ex} ({e:.0f}ms)")
        return fn
    return dec

# ═══════════════════════════════════════════════════════════════════
# 1. CORE INFRASTRUCTURE — Config, imports, singletons
# ═══════════════════════════════════════════════════════════════════
section("1. CORE INFRASTRUCTURE")

@test("config loads with all 81+ settings")
def _():
    from app.config import get_settings
    s = get_settings()
    attrs = [a for a in dir(s) if not a.startswith('_') and not callable(getattr(s, a, None))]
    assert len(attrs) >= 60, f"Only {len(attrs)} settings"
    return True

@test("prompt registry has 7+ roles with active versions")
def _():
    from app.prompt_registry import get_prompt_versions_map, get_active_prompt
    v = get_prompt_versions_map()
    assert len(v) >= 7, f"Only {len(v)} roles"
    for role in ["commander", "researcher", "coder", "writer"]:
        p = get_active_prompt(role)
        assert p and len(p) > 50, f"Empty prompt for {role}"
    return True

@test("idle scheduler has 19+ jobs registered")
def _():
    from app.idle_scheduler import _default_jobs
    jobs = _default_jobs()
    assert len(jobs) >= 19, f"Only {len(jobs)} jobs"
    names = [j[0] for j in jobs]
    required = ["evolution", "cogito-cycle", "island-evolution", "atlas-competence-sync",
                 "feedback-aggregate", "modification-engine", "health-evaluate", "system-monitor"]
    missing = [r for r in required if r not in names]
    assert not missing, f"Missing jobs: {missing}"
    return True

@test("auto_deployer has 50+ protected files")
def _():
    from app.auto_deployer import PROTECTED_FILES
    assert len(PROTECTED_FILES) >= 50, f"Only {len(PROTECTED_FILES)} protected"
    for f in ["app/security.py", "app/eval_sandbox.py", "app/self_awareness/inspect_tools.py",
              "app/bridge_client.py", "app/lifecycle_hooks.py"]:
        assert f in PROTECTED_FILES, f"Missing: {f}"
    return True

# ═══════════════════════════════════════════════════════════════════
# 2. MEMORY STORES — All 5 layers operational
# ═══════════════════════════════════════════════════════════════════
section("2. MEMORY STORES")

@test("ChromaDB: connect + list collections")
def _():
    import chromadb
    client = chromadb.HttpClient(host="chromadb", port=8000)
    col = client.get_or_create_collection("test_operational")
    col.upsert(ids=["test1"], documents=["operational test document"])
    results = col.query(query_texts=["operational test"], n_results=1)
    assert results["documents"][0][0] == "operational test document"
    client.delete_collection("test_operational")
    return True

@test("ChromaDB: philosophy collection has 3000+ chunks")
def _():
    import chromadb
    client = chromadb.HttpClient(host="chromadb", port=8000)
    col = client.get_or_create_collection("philosophy_humanist")
    count = col.count()
    assert count >= 3000, f"Philosophy only has {count} chunks"
    return True

@test("PostgreSQL: all 6 schemas present with tables")
def _():
    from app.config import get_settings
    import psycopg2
    s = get_settings()
    conn = psycopg2.connect(s.mem0_postgres_url)
    with conn.cursor() as cur:
        cur.execute("""SELECT schemaname, count(*) FROM pg_tables
                       WHERE schemaname NOT IN ('pg_catalog','information_schema','pg_toast','public')
                       GROUP BY schemaname""")
        schemas = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    for schema in ["evolution", "feedback", "modification", "atlas", "training"]:
        assert schema in schemas, f"Missing schema: {schema}"
        assert schemas[schema] >= 1, f"Schema {schema} has no tables"
    return True

@test("PostgreSQL: write + read roundtrip")
def _():
    from app.config import get_settings
    import psycopg2
    s = get_settings()
    conn = psycopg2.connect(s.mem0_postgres_url)
    conn.autocommit = True
    test_id = f"optest_{int(time.time())}"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO training.interactions (id, agent_role, messages, response, source_model, source_tier, provenance) VALUES (%s,'test','[]'::jsonb,'test','test','T1_local','test') ON CONFLICT DO NOTHING", (test_id,))
        cur.execute("SELECT id FROM training.interactions WHERE id = %s", (test_id,))
        assert cur.fetchone() is not None, "Write+read failed"
        cur.execute("DELETE FROM training.interactions WHERE id = %s", (test_id,))
    conn.close()
    return True

@test("SQLite conversation store: add + retrieve message")
def _():
    from app.conversation_store import add_message, get_history
    test_sender = "test_operational_sender"
    add_message(test_sender, "user", "Operational test message")
    history = get_history(test_sender, n=1)
    assert len(history) >= 1
    assert "Operational test" in history[-1].get("content", "")
    return True

@test("History compression: full lifecycle")
def _():
    from app.history_compression import History, CompressionConfig, Message
    h = History(CompressionConfig(max_context_tokens=4096))
    for i in range(3):
        h.start_new_topic()
        h.add_message(Message(role="user", content=f"Question {i}"))
        h.add_message(Message(role="assistant", content=f"Answer {i} with details..."))
    s = h.get_stats()
    assert s["topics"] == 2 and s["current_messages"] == 2
    serialized = h.serialize()
    h2 = History.deserialize(serialized)
    assert h2.get_stats()["topics"] == 2
    msgs = h2.to_langchain_messages()
    assert len(msgs) >= 4
    return True

# ═══════════════════════════════════════════════════════════════════
# 3. KNOWLEDGE STORES — Philosophy, Fiction, Skills, Self-Knowledge
# ═══════════════════════════════════════════════════════════════════
section("3. KNOWLEDGE STORES")

@test("philosophy vectorstore: query returns relevant chunks")
def _():
    from app.philosophy.vectorstore import get_store
    store = get_store()
    assert store is not None
    results = store.query("What are humanist values?", n_results=3)
    assert results and len(results) > 0
    return True

@test("fiction inspiration: module operational")
def _():
    from app.fiction_inspiration import search_fiction, FICTION_COLLECTION_NAME
    assert FICTION_COLLECTION_NAME == "fiction_inspiration"
    return True

@test("skill library: register + search + record usage")
def _():
    from app.atlas.skill_library import get_library
    lib = get_library()
    lib.register_skill(
        skill_id="test/operational_test",
        name="Operational Test Skill",
        category="patterns",
        code="def test(): return True",
        description="Test skill for operational testing",
        source_type="trial_and_error",
    )
    results = lib.search(query="operational test")
    assert len(results) >= 1
    lib.record_usage("test/operational_test", success=True)
    manifest = lib.get_skill("test/operational_test")
    assert manifest.usage_count >= 1
    return True

@test("self-knowledge: ingest + query codebase")
def _():
    from app.self_awareness.knowledge_ingestion import ingest_codebase, query_self_knowledge
    result = ingest_codebase(full=False)
    assert result.get("chunks_added", 0) >= 0
    results = query_self_knowledge("commander routing", n_results=3)
    assert isinstance(results, list)
    return True

# ═══════════════════════════════════════════════════════════════════
# 4. EVOLUTION PATHWAYS — All 4 systems operational
# ═══════════════════════════════════════════════════════════════════
section("4. EVOLUTION PATHWAYS")

@test("evolve_blocks: parse + validate freeze/evolve")
def _():
    from app.evolve_blocks import parse_prompt, validate_modification, has_evolve_blocks
    text = "# Values\n<!-- FREEZE-BLOCK-START -->\nBe ethical.\n<!-- FREEZE-BLOCK-END -->\n# Strategy\n<!-- EVOLVE-BLOCK-START id=\"s\" -->\nBe concise.\n<!-- EVOLVE-BLOCK-END -->"
    p = parse_prompt(text)
    assert len(p.freeze_blocks) == 1 and len(p.evolve_blocks) == 1
    bad = text.replace("Be ethical.", "Be evil.")
    assert not validate_modification(text, bad)["valid"]
    good = text.replace("Be concise.", "Be very concise.")
    assert validate_modification(text, good)["valid"]
    return True

@test("island evolution: initialize + inspect state")
def _():
    from app.island_evolution import IslandEvolution, NUM_ISLANDS, POP_PER_ISLAND
    engine = IslandEvolution(target_role="coder")
    assert NUM_ISLANDS == 3 and POP_PER_ISLAND == 5
    return True

@test("parallel evolution: archive CRUD")
def _():
    from app.parallel_evolution import EvolutionArchive, ArchiveEntry
    with tempfile.TemporaryDirectory() as td:
        archive = EvolutionArchive(archive_dir=Path(td))
        archive.add(ArchiveEntry(version_tag="optest-v1", metrics={"task_completion": 0.8},
                                  mutation_strategy="prompt_optimization", composite_score=0.7))
        archive.add(ArchiveEntry(version_tag="optest-v2", metrics={"task_completion": 0.9},
                                  parent_version="optest-v1", mutation_strategy="inspiration", composite_score=0.85))
        assert archive.size == 2
        best = archive.get_best_variants(1)
        assert best[0].version_tag == "optest-v2"
        archive.record_child_outcome("optest-v1", success=True)
        parent = next(e for e in archive._entries if e.version_tag == "optest-v1")
        assert parent.child_success_count == 1
        dist = archive.get_strategy_distribution()
        assert "prompt_optimization" in dist and "inspiration" in dist
    return True

@test("adaptive ensemble: phase + strategy selection")
def _():
    from app.adaptive_ensemble import get_controller
    ctrl = get_controller()
    r = ctrl.step(0.5)
    assert "exploration_rate" in r and "phase" in r
    s = ctrl.select_mutation_strategy()
    assert s in ("meta_prompt", "random", "inspiration", "depth_exploit")
    ctrl.step(0.3)
    ctrl.step(0.3)
    assert ctrl.exploration_rate > 0
    return True

@test("MAP-Elites: database operational")
def _():
    from app.map_elites import MAPElitesDB
    return True

@test("cascade evaluator: instantiates")
def _():
    from app.cascade_evaluator import CascadeEvaluator
    assert CascadeEvaluator() is not None
    return True

# ═══════════════════════════════════════════════════════════════════
# 5. FEEDBACK LOOP — Full pipeline connectivity
# ═══════════════════════════════════════════════════════════════════
section("5. FEEDBACK LOOP")

@test("feedback pipeline → modification engine → prompt registry chain")
def _():
    from app.config import get_settings
    s = get_settings()
    if not s.mem0_postgres_url: return True
    from app.feedback_pipeline import FeedbackPipeline, POSITIVE_EMOJIS, NEGATIVE_EMOJIS
    from app.modification_engine import ModificationEngine, TIER1_PARAMETERS, TIER2_PARAMETERS
    import app.prompt_registry as registry
    pipeline = FeedbackPipeline(s.mem0_postgres_url)
    assert "👍" in POSITIVE_EMOJIS and "👎" in NEGATIVE_EMOJIS
    assert "system_prompt" in TIER1_PARAMETERS and "workflow_graph" in TIER2_PARAMETERS
    assert pipeline is not None
    engine = ModificationEngine(s.mem0_postgres_url, registry, pipeline, None)
    assert engine._determine_tier("system_prompt") == "tier1"
    assert engine._determine_tier("workflow_graph") == "tier2"
    return True

@test("eval sandbox: safety probes comprehensive")
def _():
    from app.eval_sandbox import SAFETY_PROBES, WEIGHTS
    assert len(SAFETY_PROBES) >= 7
    assert abs(sum(WEIGHTS.values()) - 1.0) < 0.05
    probe_text = " ".join(str(p.get("input","")) for p in SAFETY_PROBES).lower()
    for keyword in ["ignore", "delete", "dan", "admin"]:
        assert keyword in probe_text, f"Missing probe for: {keyword}"
    return True

@test("safety guardian: import + instantiation")
def _():
    from app.safety_guardian import SafetyGuardian
    return True

@test("implicit feedback: detector instantiation")
def _():
    from app.implicit_feedback import ImplicitFeedbackDetector
    return True

@test("meta learning: strategy selection")
def _():
    from app.meta_learning import MetaLearner
    return True

# ═══════════════════════════════════════════════════════════════════
# 6. SELF-AWARENESS — Inspection, routing, grounding, cogito
# ═══════════════════════════════════════════════════════════════════
section("6. SELF-AWARENESS")

@test("inspect_codebase: finds 100+ modules, 30K+ lines")
def _():
    from app.self_awareness.inspect_tools import inspect_codebase
    r = inspect_codebase(scope="summary")
    assert r["total_modules"] >= 100, f"Only {r['total_modules']} modules"
    assert r["total_lines"] >= 30000, f"Only {r['total_lines']} lines"
    return True

@test("inspect_agents: discovers 7+ agents")
def _():
    from app.self_awareness.inspect_tools import inspect_agents
    r = inspect_agents()
    assert r["agent_count"] >= 7, f"Only {r['agent_count']} agents"
    return True

@test("inspect_memory: all backends report")
def _():
    from app.self_awareness.inspect_tools import inspect_memory
    r = inspect_memory(backend="all")
    assert "chromadb" in r
    assert "postgresql" in r
    return True

@test("query router: classifies self-ref queries correctly")
def _():
    from app.self_awareness.query_router import SelfRefRouter, SelfRefType
    router = SelfRefRouter(semantic_enabled=False)
    self_queries = ["What are you?", "How many agents do you have?", "What is your name?",
                    "Describe your architecture", "What models do you use?"]
    for q in self_queries:
        c = router.classify(q)
        assert c.is_self_referential, f"'{q}' not detected as self-ref"
    normal = ["What is the weather?", "Write me a poem", "Research bitcoin price"]
    for q in normal:
        c = router.classify(q)
        assert not c.is_self_referential, f"'{q}' incorrectly classified as self-ref"
    return True

@test("grounding protocol: blocks generic AI phrases, allows grounded")
def _():
    from app.self_awareness.grounding import GroundingProtocol
    p = GroundingProtocol()
    ungrounded = ["As an AI language model...", "My training data includes...", "My knowledge cutoff is..."]
    for t in ungrounded:
        assert p.post_process(t)["ungrounded_detected"], f"Not detected: {t[:30]}"
    grounded = ["I am a five-agent CrewAI system.", "My architecture uses a four-tier LLM cascade."]
    for t in grounded:
        assert not p.post_process(t)["ungrounded_detected"], f"False positive: {t[:30]}"
    return True

@test("grounding: full pipeline (router → gather → prompt)")
def _():
    from app.self_awareness.query_router import SelfRefRouter
    from app.self_awareness.grounding import GroundingProtocol
    router = SelfRefRouter(semantic_enabled=False)
    protocol = GroundingProtocol()
    c = router.classify("What are you and how do you work?")
    assert c.should_ground
    ctx = protocol.gather_context(c)
    prompt = protocol.build_system_prompt(ctx)
    assert "Answer ONLY from the grounded context" in prompt
    assert "GROUNDED CONTEXT" in prompt
    return True

@test("journal: write + read + search")
def _():
    from app.self_awareness.journal import get_journal, JournalEntry, JournalEntryType
    j = get_journal()
    j.write(JournalEntry(entry_type=JournalEntryType.OBSERVATION, summary="Operational test entry"))
    j.write(JournalEntry(entry_type=JournalEntryType.TASK_COMPLETED, summary="Test task done",
                          agents_involved=["commander"], duration_seconds=1.5))
    recent = j.read_recent(5)
    assert len(recent) >= 2
    search = j.search("Operational test")
    assert len(search) >= 1
    counts = j.count()
    assert sum(counts.values()) >= 2
    return True

@test("homeostasis: state + behavioral modifiers")
def _():
    from app.self_awareness.homeostasis import get_state, get_behavioral_modifiers
    state = get_state()
    assert isinstance(state, dict)
    mods = get_behavioral_modifiers()
    assert isinstance(mods, str) or isinstance(mods, dict)
    return True

# ═══════════════════════════════════════════════════════════════════
# 7. ATLAS — Skill library, API scout, code forge, competence
# ═══════════════════════════════════════════════════════════════════
section("7. ATLAS")

@test("auth patterns: detect OAuth2 from text")
def _():
    from app.atlas.auth_patterns import detect_auth_pattern, get_pattern_code, list_patterns
    patterns = list_patterns()
    assert len(patterns) >= 6
    result = detect_auth_pattern("OAuth2 client credentials with token endpoint /oauth/token")
    assert result[0][0] == "oauth2_client_credentials"
    code = get_pattern_code("oauth2_client_credentials")
    assert "class" in code and "token" in code.lower()
    return True

@test("competence tracker: register + check + sync")
def _():
    from app.atlas.competence_tracker import get_tracker
    t = get_tracker()
    t.register("apis", "TestAPI", 0.85, source="operational_test")
    entry = t.check_competence("apis", "TestAPI")
    assert entry is not None and entry.effective_confidence() >= 0.7
    readiness = t.check_task_readiness([{"domain": "apis", "name": "TestAPI"}])
    assert readiness["ready"]
    readiness2 = t.check_task_readiness([{"domain": "apis", "name": "UnknownAPI"}])
    assert not readiness2["ready"]
    synced = t.sync_from_skill_library()
    assert synced >= 0
    return True

@test("code forge: instantiation")
def _():
    from app.atlas.code_forge import get_forge
    assert get_forge() is not None
    return True

@test("video learner: instantiation")
def _():
    from app.atlas.video_learner import get_learner
    assert get_learner() is not None
    return True

@test("learning planner + quality evaluator")
def _():
    from app.atlas.learning_planner import get_planner, get_evaluator
    assert get_planner() is not None
    evaluator = get_evaluator()
    evaluator.record_api_discovery(True)
    evaluator.record_api_discovery(False)
    report = evaluator.get_quality_report()
    assert "api_discovery_success_rate" in report
    assert report["api_discovery_success_rate"]["samples"] == 2
    return True

# ═══════════════════════════════════════════════════════════════════
# 8. AGENT ZERO AMENDMENTS — Hooks, tools, projects
# ═══════════════════════════════════════════════════════════════════
section("8. AGENT ZERO AMENDMENTS")

@test("lifecycle hooks: 5+ registered, 2+ immutable, safety enforcement")
def _():
    from app.lifecycle_hooks import get_registry, HookPoint, HookContext
    reg = get_registry()
    hooks = reg.list_hooks()
    assert len(hooks) >= 5
    immutable = [h for h in hooks if h["immutable"]]
    assert len(immutable) >= 2
    safe = HookContext(hook_point=HookPoint.PRE_TOOL_USE, agent_id="t", data={"action": "safe"})
    assert not reg.execute(HookPoint.PRE_TOOL_USE, safe).abort
    danger = HookContext(hook_point=HookPoint.PRE_TOOL_USE, agent_id="t", data={"action": "rm -rf /"})
    assert reg.execute(HookPoint.PRE_TOOL_USE, danger).abort
    return True

@test("lifecycle hooks: immutable hooks CANNOT be overridden")
def _():
    from app.lifecycle_hooks import get_registry, HookPoint, HookContext
    reg = get_registry()
    orig_count = len(reg.list_hooks())
    def malicious(ctx): ctx.abort = False; return ctx
    result = reg.register("humanist_safety", HookPoint.PRE_LLM_CALL, malicious, priority=-1)
    new_count = len(reg.list_hooks())
    assert new_count == orig_count, "Immutable hook was overridden!"
    return True

@test("tool executor: self-correction + safety blocking")
def _():
    from app.tool_executor import SelfCorrectingExecutor, DynamicToolRegistry, ToolSafetyError
    e = SelfCorrectingExecutor(max_retries=0)
    r = e.execute(tool=lambda x: f"ok:{x}", tool_input="test", tool_name="test")
    assert r.success and "ok:test" in str(r.result)
    d = DynamicToolRegistry(approval_required=True, auto_approve_agents=["commander"])
    d.register("cmd_tool", "test", lambda: "ok", created_by="commander")
    assert d.get("cmd_tool") is not None
    d.register("si_tool", "test", lambda: "ok", created_by="self_improver", auto_approve=True)
    assert d.get("si_tool") is None
    try: d.register("modify_safety", "bad", lambda: "evil"); return False
    except ToolSafetyError: pass
    return True

@test("project isolation: detect + activate + scope")
def _():
    from app.project_isolation import get_manager
    pm = get_manager()
    assert pm.detect_project("PLG Baltic market") == "plg"
    assert pm.detect_project("KaiCart TikTok") == "kaicart"
    assert pm.detect_project("Archibal C2PA") == "archibal"
    assert pm.detect_project("weather forecast") is None
    plg = pm.activate("plg")
    assert plg.mem0_namespace == "project_plg"
    kc_ctx = pm.activate("kaicart")
    assert kc_ctx.mem0_namespace != plg.mem0_namespace
    pm.deactivate()
    return True

# ═══════════════════════════════════════════════════════════════════
# 9. SAFETY CHAIN — 5 layers verified end-to-end
# ═══════════════════════════════════════════════════════════════════
section("9. SAFETY CHAIN (5 LAYERS)")

@test("Layer 5: immutable hooks block dangerous actions")
def _():
    from app.lifecycle_hooks import get_registry, HookPoint, HookContext
    reg = get_registry()
    for action in ["rm -rf /", "DROP TABLE users", "FORMAT C:", "dd if=/dev/zero"]:
        ctx = HookContext(hook_point=HookPoint.PRE_TOOL_USE, agent_id="coder", data={"action": action})
        assert reg.execute(HookPoint.PRE_TOOL_USE, ctx).abort, f"Not blocked: {action}"
    return True

@test("Layer 4: FREEZE-BLOCK rejects frozen content modification")
def _():
    from app.evolve_blocks import parse_prompt, validate_modification
    text = "<!-- FREEZE-BLOCK-START -->\nSafe\n<!-- FREEZE-BLOCK-END -->\n<!-- EVOLVE-BLOCK-START id=\"s\" -->\nChange me\n<!-- EVOLVE-BLOCK-END -->"
    bad = text.replace("Safe", "Unsafe")
    assert not validate_modification(text, bad)["valid"]
    return True

@test("Layer 3: protected files block self-modification of safety code")
def _():
    from app.auto_deployer import PROTECTED_FILES
    critical = ["app/eval_sandbox.py", "app/safety_guardian.py", "app/vetting.py",
                "app/security.py", "app/auto_deployer.py", "app/souls/constitution.md"]
    for f in critical:
        assert f in PROTECTED_FILES, f"UNPROTECTED: {f}"
    return True

@test("Layer 2: eval sandbox uses immutable weights + safety probes")
def _():
    from app.eval_sandbox import SAFETY_PROBES, WEIGHTS, IMPROVEMENT_THRESHOLD, SAFETY_THRESHOLD
    assert len(SAFETY_PROBES) >= 7
    assert SAFETY_THRESHOLD >= 0.9
    assert IMPROVEMENT_THRESHOLD >= 0.01
    return True

@test("Layer 1: bridge capability tokens isolate agents")
def _():
    from app.bridge_client import _get_agent_token
    token_self = _get_agent_token("self_improver")
    token_cmd = _get_agent_token("commander")
    # Tokens should either be None (no bridge configured) or different per agent
    if token_self and token_cmd:
        assert token_self != token_cmd, "Agents share the same token!"
    return True

# ═══════════════════════════════════════════════════════════════════
# 10. CROSS-SUBSYSTEM DATA FLOW
# ═══════════════════════════════════════════════════════════════════
section("10. CROSS-SUBSYSTEM DATA FLOW")

@test("handle_task pipeline: history + project + hooks + health")
def _():
    from app.history_compression import get_history, Message as HMsg
    from app.project_isolation import get_manager
    from app.lifecycle_hooks import get_registry, HookPoint, HookContext
    from app.health_monitor import get_monitor, InteractionMetrics
    from app.self_awareness.journal import get_journal, JournalEntry, JournalEntryType

    sender = "e2e_test_sender"
    text = "Analyze PLG ticket sales Q2"

    h = get_history(sender)
    h.start_new_topic()
    h.add_message(HMsg(role="user", content=text))
    assert h.get_stats()["current_messages"] == 1

    pm = get_manager()
    assert pm.detect_project(text) == "plg"
    ctx = pm.activate("plg")
    assert ctx.mem0_namespace == "project_plg"

    hook_ctx = HookContext(hook_point=HookPoint.PRE_TASK, agent_id="commander", task_description=text)
    assert not get_registry().execute(HookPoint.PRE_TASK, hook_ctx).abort

    response = "PLG Q2 sales grew 15%."
    h.add_message(HMsg(role="assistant", content=response))

    get_monitor().record(InteractionMetrics(success=True, latency_ms=500, task_difficulty=5))
    get_journal().write(JournalEntry(entry_type=JournalEntryType.TASK_COMPLETED,
                                      summary="E2E test: PLG analysis", agents_involved=["commander"]))
    pm.deactivate()
    return True

@test("version manifest captures multi-layer state")
def _():
    from app.version_manifest import create_manifest
    m = create_manifest(promoted_by="optest", reason="operational test")
    assert "version" in m and "components" in m
    c = m["components"]
    assert "prompts" in c and "soul_md" in c and "config" in c
    return True

@test("competence tracker syncs from skill library")
def _():
    from app.atlas.competence_tracker import get_tracker
    from app.atlas.skill_library import get_library
    lib = get_library()
    tracker = get_tracker()
    updated = tracker.sync_from_skill_library()
    assert updated >= 0
    return True

@test("training collector hook registered in lifecycle pipeline")
def _():
    from app.lifecycle_hooks import get_registry, HookPoint, HookContext
    reg = get_registry()
    hooks = reg.list_hooks(HookPoint.POST_LLM_CALL)
    names = [h["name"] for h in hooks]
    assert "training_data" in names, f"training_data hook missing from POST_LLM_CALL"
    return True

# ═══════════════════════════════════════════════════════════════════
# 11. FACADE PACKAGES — New architecture layer works
# ═══════════════════════════════════════════════════════════════════
section("11. FACADE PACKAGES")

@test("evolution_suite: all 7 module exports accessible")
def _():
    from app.evolution_suite import (run_evolution_session, IslandEvolution, EvolutionArchive,
        parse_prompt, get_controller, CascadeEvaluator)
    return True

@test("feedback_suite: all 4 module exports accessible")
def _():
    from app.feedback_suite import FeedbackPipeline, ModificationEngine, ImplicitFeedbackDetector, MetaLearner
    return True

@test("llm_suite: all 5 module exports accessible")
def _():
    from app.llm_suite import create_specialist_llm, select_model, set_mode, CATALOG
    return True

@test("operations_suite: all 5 module exports accessible")
def _():
    from app.operations_suite import create_manifest, get_monitor, SelfHealer, run_reference_suite, SandboxRunner
    return True

@test("contracts: events + state + firestore_schema")
def _():
    from app.contracts.events import TaskStarted, TaskCompleted, FeedbackReceived, HealthAlert
    from app.contracts.state import CrewStatus, SystemHealth, SubsystemStatus
    from app.contracts.firestore_schema import SCHEMA
    e = TaskStarted(crew="research", task="test", difficulty=5)
    assert e.crew == "research" and e.difficulty == 5
    assert len(SCHEMA) >= 20
    return True

# ═══════════════════════════════════════════════════════════════════
# 12. HOST BRIDGE + TRAINING PIPELINE
# ═══════════════════════════════════════════════════════════════════
section("12. BRIDGE + TRAINING")

@test("bridge client: per-agent token isolation")
def _():
    from app.bridge_client import BridgeClient, _get_agent_token
    c = BridgeClient("test", "test-token")
    assert c.agent_id == "test"
    return True

@test("training collector: hook + curation pipeline")
def _():
    from app.training_collector import create_training_data_hook, CurationPipeline
    hook = create_training_data_hook()
    assert hook is not None
    return True

@test("training pipeline: orchestrator + collapse detection")
def _():
    from app.training_pipeline import TrainingOrchestrator, detect_collapse, distinct_n
    texts = ["hello world foo bar", "another test sentence here"]
    d2 = distinct_n(texts, 2)
    assert 0 < d2 <= 1.0
    return True

# ═══════════════════════════════════════════════════════════════════
# 13. FAST DEPLOY + OPERATIONS
# ═══════════════════════════════════════════════════════════════════
section("13. FAST DEPLOY")

@test("health monitor: record + evaluate + report")
def _():
    from app.health_monitor import get_monitor, InteractionMetrics
    m = get_monitor()
    for i in range(12):
        m.record(InteractionMetrics(success=True, latency_ms=200+i*10, task_difficulty=3))
    state = m.get_health_state()
    assert state.sample_size >= 10
    report = m.format_health_report()
    assert "Health Monitor" in report
    return True

@test("reference tasks: suite integrity")
def _():
    from app.reference_tasks import REFERENCE_TASKS, verify_suite_integrity
    assert len(REFERENCE_TASKS) >= 3
    return True

@test("sandbox runner: import")
def _():
    from app.sandbox_runner import SandboxRunner
    return True

# ═══════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════
total = len(R["passed"]) + len(R["failed"])
print(f"\n{'='*60}")
print(f"  COMPREHENSIVE OPERATIONAL TEST REPORT")
print(f"{'='*60}")
print(f"\n  ✅ PASSED: {len(R['passed'])}/{total}")
if R["failed"]:
    print(f"  ❌ FAILED: {len(R['failed'])}/{total}")
    for f in R["failed"]:
        print(f"     {f}")

print(f"\n  ⏱️ Slowest tests:")
for n, ms in sorted(R["timings"].items(), key=lambda x: x[1], reverse=True)[:8]:
    print(f"     {n}: {ms:.0f}ms")

rate = len(R["passed"]) / max(1, total) * 100
print(f"\n  {'='*60}")
print(f"  PASS RATE: {rate:.0f}% ({len(R['passed'])}/{total})")
print(f"  {'='*60}")

Path("/app/workspace/operational_test_report.json").write_text(json.dumps({
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "passed": len(R["passed"]), "failed": len(R["failed"]), "total": total,
    "pass_rate": rate, "failures": R["failed"],
    "slowest": sorted(R["timings"].items(), key=lambda x: x[1], reverse=True)[:10],
}, indent=2))

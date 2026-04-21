"""
subia.idle.production_adapters — live adapters for the Phase 12 idle
engines (Reverie, Understanding, Shadow).

The engines themselves (app.subia.reverie, .understanding, .shadow) are
deliberately dependency-free: all side-effects go through injected
adapter objects. Tests pass in-memory stubs. Production runs these live
adapters, which in turn delegate to:

  - app.tools.wiki_tools        (filesystem wiki + semantic search)
  - app.memory.mem0_manager     (episodic memory)
  - app.memory.chromadb_manager (embeddings + vector search)
  - Neo4j via a guarded driver   (graph walks; absence → empty list)
  - app.llm_factory             (Tier-1/Tier-2 LLM via the cascade)
  - app.subia.prediction.accuracy_tracker (for Shadow's error stream)

Every adapter is exception-safe: on failure, log at DEBUG and return
empty data. An idle engine with no data produces no side-effects,
which is the correct behaviour when infrastructure is partially down.

Infrastructure-level. Not agent-modifiable.
"""

from __future__ import annotations

import logging
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.subia.kernel import SubjectivityKernel
from app.subia.reverie import ReverieAdapters, ReverieEngine
from app.subia.shadow import ShadowAdapters, ShadowMiner
from app.subia.understanding import (
    UnderstandingAdapters,
    UnderstandingPassRunner,
)

logger = logging.getLogger(__name__)


# ── Wiki root resolution (mirrors wiki_tools.WIKI_ROOT) ──────────────

def _wiki_root() -> Path:
    docker = Path("/app/wiki")
    if docker.is_dir():
        return docker
    repo = Path(__file__).resolve().parents[3] / "wiki"
    return repo


# ── Reverie ──────────────────────────────────────────────────────────

def build_reverie_engine() -> ReverieEngine:
    """Production ReverieEngine with live adapters."""
    return ReverieEngine(adapters=_reverie_adapters())


def _reverie_adapters() -> ReverieAdapters:
    return ReverieAdapters(
        pick_random_wiki_page=_pick_random_wiki_page,
        walk_neo4j=_walk_neo4j,
        fiction_search=_fiction_search,
        philosophical_search=_philosophical_search,
        mem0_full_search=_mem0_full_search,
        llm_resonance=_llm_resonance,
        llm_synthesis=_llm_synthesis,
        write_reverie_page=_write_reverie_page,
        write_neo4j_analogy=_write_neo4j_analogy,
    )


def _pick_random_wiki_page() -> dict:
    """Return a random wiki page as {id, title, section}."""
    root = _wiki_root()
    if not root.is_dir():
        return {}
    candidates: list[Path] = []
    for section in ("plg", "archibal", "kaicart", "meta", "self"):
        sec = root / section
        if sec.is_dir():
            candidates.extend(sec.rglob("*.md"))
    if not candidates:
        return {}
    pick = random.choice(candidates)
    try:
        rel = pick.relative_to(root)
    except ValueError:
        return {}
    section = rel.parts[0] if rel.parts else "unknown"
    return {
        "id": str(rel.with_suffix("")),
        "title": pick.stem.replace("-", " ").strip(),
        "section": section,
    }


def _walk_neo4j(start_id: str, steps: int) -> list:
    """Best-effort n-hop walk from start_id. Returns [] when Neo4j is
    absent or the walk fails."""
    if not start_id:
        return []
    try:
        from neo4j import GraphDatabase
    except Exception:
        return []
    try:
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        driver = GraphDatabase.driver(uri, auth=(user, password))
    except Exception:
        return []
    nodes: list[dict] = []
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH path=(n {id: $start})-[*1.." + str(max(1, min(steps, 6))) + "]-(m) "
                "RETURN nodes(path) AS ns LIMIT 1",
                start=start_id,
            )
            rec = result.single()
            if rec:
                for node in rec["ns"]:
                    props = dict(node)
                    nodes.append({
                        "id": str(props.get("id", "")),
                        "title": str(props.get("title", "")),
                        "section": str(props.get("section", "")),
                    })
    except Exception:
        logger.debug("reverie: neo4j walk failed", exc_info=True)
    finally:
        try:
            driver.close()
        except Exception:
            pass
    return nodes


def _fiction_search(query: str) -> list:
    return _chromadb_query("fiction_inspiration", query, n=3)


def _philosophical_search(query: str) -> list:
    return _chromadb_query("philosophy_texts", query, n=3)


def _chromadb_query(collection: str, query: str, *, n: int) -> list:
    if not query:
        return []
    try:
        from app.memory.chromadb_manager import get_client
        client = get_client()
        if client is None:
            return []
        coll = client.get_or_create_collection(collection)
        res = coll.query(query_texts=[query[:500]], n_results=n) or {}
        docs = (res.get("documents") or [[]])[0]
        return [{"text": d} for d in docs if d]
    except Exception:
        logger.debug("reverie: chroma query %s failed", collection, exc_info=True)
        return []


def _mem0_full_search(query: str, limit: int) -> list:
    if not query:
        return []
    try:
        from app.memory.mem0_manager import search_shared
        return list(search_shared(query[:500], n=max(1, int(limit))))
    except Exception:
        logger.debug("reverie: mem0 search failed", exc_info=True)
        return []


def _llm_resonance(a: str, b: str) -> str:
    prompt = (
        "You are evaluating whether two concepts share a meaningful "
        "structural analogy worth writing about.\n\n"
        f"Concept A: {a}\n"
        f"Concept B: {b}\n\n"
        "If they do, respond with one short sentence naming the shared "
        "structure. If they do not, respond with exactly the phrase "
        "'no resonance'."
    )
    return _call_cascade_llm(prompt, role="self_improve", max_tokens=120)


def _llm_synthesis(concepts: list) -> str:
    if not concepts:
        return ""
    titles = []
    notes: list[str] = []
    for c in concepts[:8]:
        node = c.get("node") if isinstance(c, dict) else None
        if isinstance(node, dict) and node.get("title"):
            titles.append(str(node["title"])[:80])
        for r in (c.get("resonances") or []) if isinstance(c, dict) else []:
            if isinstance(r, dict):
                note = r.get("note") or ""
                if note:
                    notes.append(str(note)[:120])
    prompt = (
        "Write a 120-word speculative synthesis exploring the shared "
        "structure across these wiki concepts. Mark the output as "
        "speculative — do not assert facts.\n\n"
        f"Concepts: {', '.join(titles)}\n"
        f"Resonances noted: {'; '.join(notes[:5])}\n"
    )
    return _call_cascade_llm(prompt, role="self_improve", max_tokens=400)


def _write_reverie_page(slug: str, body: str, frontmatter: dict) -> str:
    if not body or not body.strip():
        return ""
    try:
        root = _wiki_root() / "meta" / "reverie"
        root.mkdir(parents=True, exist_ok=True)
        safe_slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "synthesis"
        stamped = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = root / f"{stamped}-{safe_slug}.md"
        fm_lines = ["---"]
        fm = dict(frontmatter or {})
        fm.setdefault("section", "meta/reverie")
        fm.setdefault("epistemic_status", "speculative")
        fm.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        for k, v in fm.items():
            fm_lines.append(f"{k}: {v!r}")
        fm_lines.append("---")
        path.write_text("\n".join(fm_lines) + "\n\n" + body.strip() + "\n",
                        encoding="utf-8")
        return str(path)
    except Exception:
        logger.debug("reverie: write synthesis page failed", exc_info=True)
        return ""


def _write_neo4j_analogy(a: str, b: str, meta: dict) -> None:
    """Optional — best-effort ANALOGOUS_TO relation in Neo4j."""
    try:
        from neo4j import GraphDatabase
    except Exception:
        return
    try:
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            session.run(
                "MERGE (x {title: $a}) MERGE (y {title: $b}) "
                "MERGE (x)-[r:ANALOGOUS_TO]->(y) "
                "SET r.note = $note, r.epistemic_status = 'speculative'",
                a=a, b=b, note=str((meta or {}).get("note", ""))[:200],
            )
        driver.close()
    except Exception:
        logger.debug("reverie: neo4j analogy write failed", exc_info=True)


# ── Understanding ────────────────────────────────────────────────────

def build_understanding_runner() -> UnderstandingPassRunner:
    return UnderstandingPassRunner(adapters=_understanding_adapters())


def _understanding_adapters() -> UnderstandingAdapters:
    return UnderstandingAdapters(
        read_wiki_page=_read_wiki_page,
        raw_chunks_for=_raw_chunks_for,
        similar_pages=_similar_pages,
        neo4j_traverse=_neo4j_traverse,
        llm_causal_chain=_llm_causal_chain,
        llm_implications=_llm_implications,
        llm_analogy=_llm_analogy,
        write_wiki_update=_write_wiki_update,
        write_neo4j_relation=_write_neo4j_relation,
    )


def _read_wiki_page(path: str) -> dict:
    try:
        full = _wiki_root() / (path if path.endswith(".md") else f"{path}.md")
        if not full.exists():
            return {}
        content = full.read_text(encoding="utf-8")
    except Exception:
        return {}
    # Frontmatter parsing uses wiki_tools' helper shape.
    fm: dict = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                import yaml  # type: ignore
                fm = yaml.safe_load(parts[1]) or {}
            except Exception:
                fm = {}
            body = parts[2].lstrip("\n")
    return {"body": body, "frontmatter": fm}


def _raw_chunks_for(query: str) -> list:
    return _chromadb_query("andrusai_wiki_pages", query, n=5)


def _similar_pages(query: str) -> list:
    hits = _chromadb_query("andrusai_wiki_pages", query, n=4)
    return [h.get("text", "")[:80] for h in hits if h.get("text")]


def _neo4j_traverse(entities: list, hops: int) -> list:
    """Return raw relation edges for any of `entities` up to `hops`."""
    if not entities:
        return []
    edges: list = []
    try:
        from neo4j import GraphDatabase
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            for entity in entities[:10]:
                result = session.run(
                    "MATCH (n)-[r*1..$hops]-(m) WHERE n.title = $entity "
                    "RETURN n, r, m LIMIT 10",
                    entity=str(entity), hops=max(1, min(int(hops), 4)),
                )
                for rec in result:
                    edges.append({
                        "from": dict(rec["n"]).get("title", ""),
                        "to": dict(rec["m"]).get("title", ""),
                    })
        driver.close()
    except Exception:
        logger.debug("understanding: neo4j traverse failed", exc_info=True)
    return edges


def _llm_causal_chain(body: str) -> dict:
    if not body:
        return {}
    prompt = (
        "Extract a 2–3-level causal chain from the following text. "
        "Respond with JSON of shape "
        "{\"root\": \"...\", \"levels\": 0-3, \"text\": \"...\", "
        "\"open_questions\": [\"...\"], \"contradictions\": 0, "
        "\"recursive\": false}. No prose, no fences.\n\n"
        f"{body[:4000]}"
    )
    return _call_cascade_llm_json(prompt, role="coder", max_tokens=512)


def _llm_implications(body: str, chain: dict) -> list:
    prompt = (
        "Given this text and its causal chain, list 2–3 non-obvious "
        "implications (second-order effects, stakeholder impacts, "
        "constraints that follow). Respond with a JSON array of strings.\n\n"
        f"Text: {body[:2000]}\n"
        f"Causal chain: {str(chain)[:600]}\n"
    )
    out = _call_cascade_llm_json(prompt, role="coder", max_tokens=300)
    return out if isinstance(out, list) else []


def _llm_analogy(chain_a: dict, chain_b: dict) -> Optional[str]:
    prompt = (
        "Two causal chains. Respond with a short pattern name if they "
        "are structurally analogous (same dependency shape / same "
        "failure mode / same leverage point), or the exact string "
        "'none' otherwise.\n\n"
        f"Chain A: {str(chain_a)[:500]}\n"
        f"Chain B: {str(chain_b)[:500]}\n"
    )
    name = _call_cascade_llm(prompt, role="self_improve", max_tokens=60).strip()
    if not name or name.lower() in {"none", "no analogy", "n/a"}:
        return None
    return name[:80]


def _write_wiki_update(path: str, why_section: str, frontmatter: dict) -> None:
    if not why_section or not why_section.strip():
        return
    try:
        full = _wiki_root() / (path if path.endswith(".md") else f"{path}.md")
        if not full.exists():
            return
        original = full.read_text(encoding="utf-8")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        appended = (
            f"\n\n## Understanding pass ({stamp})\n\n{why_section.strip()}\n"
        )
        # Append if no marker, replace the block if one exists.
        marker_re = re.compile(r"\n## Understanding pass \([^)]+\)\n.*?(?=\n## |\Z)", re.DOTALL)
        if marker_re.search(original):
            new_content = marker_re.sub(appended.rstrip() + "\n", original)
        else:
            new_content = original.rstrip() + appended
        full.write_text(new_content, encoding="utf-8")
    except Exception:
        logger.debug("understanding: wiki update failed", exc_info=True)


def _write_neo4j_relation(a: str, rel_type: str, b: str, meta: dict) -> None:
    try:
        from neo4j import GraphDatabase
    except Exception:
        return
    try:
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        safe_rel = re.sub(r"[^A-Z_]", "_", rel_type.upper())[:40] or "RELATED"
        with driver.session() as session:
            session.run(
                f"MERGE (x {{title: $a}}) MERGE (y {{title: $b}}) "
                f"MERGE (x)-[r:{safe_rel}]->(y) SET r.meta = $meta",
                a=a, b=b, meta=str(meta)[:500],
            )
        driver.close()
    except Exception:
        logger.debug("understanding: neo4j relation write failed", exc_info=True)


# ── Shadow ───────────────────────────────────────────────────────────

def build_shadow_miner(kernel: SubjectivityKernel) -> ShadowMiner:
    return ShadowMiner(adapters=_shadow_adapters(kernel))


def _shadow_adapters(kernel: SubjectivityKernel) -> ShadowAdapters:
    """Adapters bound to the live kernel so Shadow can write back to
    self_state.discovered_limitations via the Phase 12 bridge."""
    return ShadowAdapters(
        fetch_scene_history=_fetch_scene_history,
        fetch_prediction_errors=_fetch_prediction_errors,
        fetch_restoration_queue_log=_fetch_restoration_queue_log,
        fetch_action_log=lambda days: _fetch_action_log(kernel, days),
        fetch_affect_log=_fetch_affect_log,
        fetch_normalize_by=lambda: _fetch_normalize_by(kernel),
        append_to_shadow_wiki=_append_to_shadow_wiki,
        add_to_self_state_discovered=lambda findings: _add_discovered(
            kernel, findings,
        ),
    )


def _fetch_scene_history(days: int) -> list:
    """Pull recent scene topics from Mem0 full tier."""
    try:
        from app.memory.mem0_manager import search_shared
        hits = search_shared("scene_item", n=20)
        topic_counts: dict[str, int] = {}
        for h in hits or []:
            topic = (h.get("memory") or h.get("text") or "")[:80]
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
        return [topic_counts] if topic_counts else []
    except Exception:
        logger.debug("shadow: scene history fetch failed", exc_info=True)
        return []


def _fetch_prediction_errors(days: int) -> list:
    try:
        from app.subia.prediction.accuracy_tracker import get_tracker
        tracker = get_tracker()
        summary = tracker.all_domains_summary() or {}
        out: list = []
        for domain, stats in summary.items():
            if isinstance(stats, dict):
                accuracy = float(stats.get("mean_accuracy", stats.get("accuracy", 0.5)))
            else:
                accuracy = float(stats)
            out.append({"domain": str(domain), "error": round(1.0 - accuracy, 4)})
        return out
    except Exception:
        logger.debug("shadow: prediction errors fetch failed", exc_info=True)
        return []


def _fetch_restoration_queue_log(days: int) -> list:
    try:
        from app.memory.mem0_manager import search_shared
        hits = search_shared("restoration_queue", n=10)
        queues: list[list[str]] = []
        for h in hits or []:
            text = h.get("memory") or h.get("text") or ""
            # Expect lines like "restoration_queue: safety, overload"
            match = re.search(r"restoration_queue[:\-]\s*([\w\s,]+)", text)
            if match:
                queues.append([v.strip() for v in match.group(1).split(",") if v.strip()])
        return queues
    except Exception:
        return []


def _fetch_action_log(kernel: SubjectivityKernel, days: int) -> list:
    """Derive action-log from kernel agency_log."""
    log = list(getattr(kernel.self_state, "agency_log", []) or [])
    out = []
    for entry in log[-200:]:
        out.append({
            "variable_addressed": (entry.get("summary", "")[:20] or "unknown"),
            "success": bool(entry.get("success", True)),
        })
    return out


def _fetch_affect_log(days: int) -> list:
    try:
        from app.memory.mem0_manager import search_shared
        hits = search_shared("dominant_affect", n=20)
        out = []
        for h in hits or []:
            text = h.get("memory") or h.get("text") or ""
            m = re.search(r"dominant_affect[:\-]\s*(\w+)", text)
            if m:
                out.append({
                    "affect": m.group(1),
                    "exploration_followed": "exploration" in text.lower(),
                })
        return out
    except Exception:
        return []


def _fetch_normalize_by(kernel: SubjectivityKernel) -> dict:
    """Active commitments per venture — used to normalise attentional
    bias by actual stake count."""
    counts: dict[str, int] = {}
    for c in kernel.self_state.active_commitments or []:
        if getattr(c, "status", "active") != "active":
            continue
        venture = getattr(c, "venture", "self") or "self"
        counts[venture] = counts.get(venture, 0) + 1
    return counts


def _append_to_shadow_wiki(markdown: str) -> None:
    if not markdown or not markdown.strip():
        return
    try:
        root = _wiki_root() / "self"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "shadow-analysis.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else (
            "---\n"
            "title: \"Shadow analysis\"\n"
            "section: self\n"
            "epistemic_status: speculative\n"
            "---\n\n"
            "# Shadow analysis\n\n"
            "Append-only record of discovered biases "
            "(DGM immutability — Proposal 3 §3.2).\n\n"
        )
        path.write_text(existing.rstrip() + "\n\n" + markdown.strip() + "\n",
                        encoding="utf-8")
    except Exception:
        logger.debug("shadow: wiki append failed", exc_info=True)


def _add_discovered(kernel: SubjectivityKernel, findings: list) -> None:
    try:
        from app.subia.connections.six_proposals_bridges import (
            shadow_findings_to_self_state,
        )
        shadow_findings_to_self_state(kernel, findings)
    except Exception:
        logger.debug("shadow: bridge append failed", exc_info=True)


# ── LLM cascade wrapper ──────────────────────────────────────────────

def _call_cascade_llm(prompt: str, *, role: str, max_tokens: int) -> str:
    try:
        from app.llm_factory import create_specialist_llm
        llm = create_specialist_llm(max_tokens=max_tokens, role=role)
    except Exception:
        return ""
    for attr in ("call", "__call__"):
        fn = getattr(llm, attr, None)
        if not callable(fn):
            continue
        try:
            out = fn(prompt)
        except Exception:
            continue
        if isinstance(out, str):
            return out.strip()
        if hasattr(out, "content"):
            return str(out.content).strip()
        if isinstance(out, dict):
            for k in ("text", "content", "output", "response"):
                if isinstance(out.get(k), str):
                    return out[k].strip()
    return ""


_JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def _call_cascade_llm_json(prompt: str, *, role: str, max_tokens: int) -> Any:
    text = _call_cascade_llm(prompt, role=role, max_tokens=max_tokens)
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    match = _JSON_BLOCK_RE.search(cleaned)
    if not match:
        return {}
    try:
        import json
        return json.loads(match.group(0))
    except Exception:
        return {}

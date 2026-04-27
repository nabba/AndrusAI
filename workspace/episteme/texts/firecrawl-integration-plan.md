---
title: "firecrawl-integration-plan.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# Firecrawl Integration Architecture for AndrusAI (`crewai-team`)

## Implementation Plan — April 2026

---

## 1. Strategic Assessment

### Why Firecrawl (Not Crawl4AI, Scrapy, etc.)

Firecrawl is the right choice for the `crewai-team` system for these verified reasons:

1. **Native CrewAI integration** — `crewai_tools` ships three Firecrawl tool classes (`FirecrawlScrapeWebsiteTool`, `FirecrawlCrawlWebsiteTool`, `FirecrawlSearchTool`) out of the box. Zero custom glue code needed for basic integration.
2. **Self-hosted with Ollama support** — The `OLLAMA_BASE_URL` env var (experimental) routes LLM extraction calls to your local Ollama, which aligns with the four-tier LLM cascade philosophy.
3. **ARM64 Docker images** — Since v2.8.0 (released ~Q1 2026), Firecrawl publishes multi-arch images supporting `linux/arm64`. This runs natively on your M4 Max via Docker Desktop without Rosetta emulation for the core services.
4. **MCP server** — The official `firecrawl-mcp` server supports `FIRECRAWL_API_URL` for self-hosted instances, opening a future MCP integration path.
5. **Output is LLM-ready** — Markdown, structured JSON via Pydantic schemas, screenshots. This feeds directly into your ChromaDB RAG pipeline without intermediate transformation.

### Self-Hosted Limitations (Verified)

These constraints are documented in Firecrawl's official self-hosting docs:

| Capability | Cloud | Self-Hosted |
|---|---|---|
| `/agent` endpoint | Yes | **No** |
| `/browser` endpoint | Yes | **No** |
| Fire-engine (anti-bot, IP rotation) | Yes | **No** |
| Ollama / local LLM extraction | No | **Yes (experimental)** |
| Screenshot support | Yes | Yes (with Playwright) |
| `/scrape`, `/crawl`, `/map`, `/search`, `/extract` | Yes | Yes |

**Key implication**: The `/agent` autonomous research endpoint is cloud-only. Your CrewAI agents will need to orchestrate scrape → extract → reason loops themselves, which is actually preferable for your architecture since it keeps orchestration logic in the Commander/Researcher agents rather than delegating it to a black-box Firecrawl agent.

---

## 2. Infrastructure Architecture

### 2.1 Docker Composition Strategy

Your existing `crewai-team` Docker stack already runs PostgreSQL/pgvector, Redis (for Mem0), Neo4j, and ChromaDB. Firecrawl brings its own Redis + Playwright + API/Worker services.

**Recommended approach: Dedicated Firecrawl Docker Compose with shared network.**

```
┌─────────────────────────────────────────────────────────────┐
│                    HOST: M4 Max (48GB)                       │
│                                                              │
│  ┌──────────────┐     ┌──────────────────────────────────┐  │
│  │   Ollama      │     │  Docker Desktop (ARM64 native)   │  │
│  │  (Metal GPU)  │     │                                  │  │
│  │  port 11434   │◄────┤  ┌────────────────────────────┐  │  │
│  └──────────────┘     │  │  crewai-team network        │  │  │
│                       │  │  ┌─────────┐ ┌──────────┐   │  │  │
│                       │  │  │Commander│ │Researcher│   │  │  │
│                       │  │  │  Agent  │ │  Agent   │   │  │  │
│                       │  │  └────┬────┘ └────┬─────┘   │  │  │
│                       │  │       │           │         │  │  │
│                       │  │       ▼           ▼         │  │  │
│                       │  │  ┌────────────────────┐     │  │  │
│                       │  │  │  Firecrawl Client  │     │  │  │
│                       │  │  │  (firecrawl-py)    │     │  │  │
│                       │  │  └─────────┬──────────┘     │  │  │
│                       │  └────────────┼────────────────┘  │  │
│                       │               │ http://firecrawl  │  │
│                       │               │   -api:3002       │  │
│                       │  ┌────────────▼────────────────┐  │  │
│                       │  │  firecrawl network          │  │  │
│                       │  │  ┌──────┐ ┌──────────────┐  │  │  │
│                       │  │  │ API  │ │   Worker     │  │  │  │
│                       │  │  │:3002 │ │  (Bull Queue)│  │  │  │
│                       │  │  └──┬───┘ └──────┬───────┘  │  │  │
│                       │  │     │             │          │  │  │
│                       │  │  ┌──▼─┐  ┌───────▼──────┐   │  │  │
│                       │  │  │Redis│  │  Playwright  │   │  │  │
│                       │  │  │:6380│  │  Service     │   │  │  │
│                       │  │  └────┘  │  :3000       │   │  │  │
│                       │  │          └──────────────┘   │  │  │
│                       │  └─────────────────────────────┘  │  │
│                       └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Critical decision: Separate Redis instance.** Firecrawl uses Redis for Bull job queues and rate limiting. Do NOT share with your Mem0 Redis — a runaway crawl job flooding the queue could impact agent memory operations. Use port `6380` for Firecrawl Redis.

### 2.2 Docker Compose File: `docker-compose.firecrawl.yml`

```yaml
name: firecrawl

networks:
  firecrawl-backend:
    driver: bridge
  crewai-bridge:
    external: true  # Created by crewai-team compose

x-common-env: &common-env
  REDIS_URL: redis://firecrawl-redis:6379
  REDIS_RATE_LIMIT_URL: redis://firecrawl-redis:6379
  PLAYWRIGHT_MICROSERVICE_URL: http://playwright-service:3000/scrape
  USE_DB_AUTHENTICATION: "false"
  NUM_WORKERS_PER_QUEUE: ${FC_NUM_WORKERS:-4}
  CRAWL_CONCURRENT_REQUESTS: ${FC_CRAWL_CONCURRENT:-5}
  MAX_CONCURRENT_JOBS: ${FC_MAX_JOBS:-10}
  # Ollama on host — uses host.docker.internal on macOS Docker Desktop
  OLLAMA_BASE_URL: http://host.docker.internal:11434/api
  MODEL_NAME: ${FC_MODEL_NAME:-qwen3:30b-a3b}
  # Optional: OpenRouter fallback for extraction when Ollama is busy
  # OPENAI_BASE_URL: https://openrouter.ai/api/v1
  # OPENAI_API_KEY: ${OPENROUTER_API_KEY}
  LOGGING_LEVEL: ${FC_LOG_LEVEL:-info}
  BULL_AUTH_KEY: ${FC_BULL_AUTH_KEY:-change-me-in-production}

services:
  firecrawl-api:
    image: ghcr.io/firecrawl/firecrawl:latest
    # For pinned version: ghcr.io/firecrawl/firecrawl:v2.8.0
    environment:
      <<: *common-env
      PORT: "3002"
      HOST: "0.0.0.0"
    networks:
      - firecrawl-backend
      - crewai-bridge  # Accessible from crewai-team agents
    ports:
      - "127.0.0.1:3002:3002"  # Bind to localhost only
    depends_on:
      firecrawl-redis:
        condition: service_healthy
      playwright-service:
        condition: service_started
    extra_hosts:
      - "host.docker.internal:host-gateway"
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 4G
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3002/"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  firecrawl-worker:
    image: ghcr.io/firecrawl/firecrawl:latest
    environment:
      <<: *common-env
    networks:
      - firecrawl-backend
    depends_on:
      firecrawl-redis:
        condition: service_healthy
      playwright-service:
        condition: service_started
    extra_hosts:
      - "host.docker.internal:host-gateway"
    command: ["node", "dist/src/harness.js", "--start-docker"]
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 4G
    restart: unless-stopped

  playwright-service:
    image: ghcr.io/firecrawl/playwright-service:latest
    environment:
      PORT: "3000"
      BLOCK_MEDIA: ${FC_BLOCK_MEDIA:-true}
    networks:
      - firecrawl-backend
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 2G
    shm_size: "1gb"  # Critical for Chromium stability
    restart: unless-stopped

  firecrawl-redis:
    image: redis:alpine
    networks:
      - firecrawl-backend
    command: redis-server --bind 0.0.0.0 --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes:
      - firecrawl-redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 768M
    restart: unless-stopped

volumes:
  firecrawl-redis-data:
```

### 2.3 Resource Budget (M4 Max 48GB)

| Service | CPU | RAM | Notes |
|---|---|---|---|
| Ollama (host, qwen3:30b-a3b) | Shared | ~18-20GB | Metal GPU, unified memory |
| crewai-team stack | ~2 cores | ~6GB | Agents + PostgreSQL + Neo4j + ChromaDB + Redis |
| Firecrawl API | 2 cores | 4GB | |
| Firecrawl Worker | 2 cores | 4GB | |
| Playwright Service | 1 core | 2GB (+1GB shm) | Chromium headless |
| Firecrawl Redis | Minimal | 768MB | |
| **Total Firecrawl** | **~5 cores** | **~11GB** | |
| **Total System** | **~9 cores** | **~37GB** | Leaves ~11GB headroom |

[Inference] The M4 Max has 16 CPU cores (12P + 4E). This allocation uses roughly 60% of CPU capacity and 77% of memory. The Playwright service is the most volatile consumer — heavy JS-rendering pages can spike memory. The `BLOCK_MEDIA: true` setting mitigates this by skipping images/video during scrape.

**Recommendation**: Start with `FC_NUM_WORKERS=4` and `FC_CRAWL_CONCURRENT=5`. Monitor with `docker stats`. If Ollama inference slows during crawls, reduce to `FC_CRAWL_CONCURRENT=3`.

---

## 3. CrewAI Integration Layer

### 3.1 Integration Path Selection

There are three viable integration paths. Here's the analysis:

| Path | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Native CrewAI Tools** (`crewai_tools.Firecrawl*`) | Zero custom code, maintained by CrewAI team | Limited configurability, tied to API v1 patterns | **Use for simple scrape/search** |
| **B: Custom CrewAI Tool wrapping `firecrawl-py`** | Full SDK control, async support, Pydantic extraction schemas | More code to maintain | **Use for structured extraction & crawl pipelines** |
| **C: MCP Server** | Protocol-level integration, future-proof | Adds latency, separate process, overkill for current architecture | **Park for Phase 2** |

**Recommended: Hybrid A+B.** Use native CrewAI tools for the Researcher agent's basic web lookups. Build custom tools for the structured extraction pipeline that feeds ChromaDB.

### 3.2 Installation

```bash
pip install firecrawl-py 'crewai[tools]'
```

### 3.3 Custom Firecrawl Tool for crewai-team

```python
# tools/firecrawl_tools.py
"""
Firecrawl integration tools for crewai-team.
Wraps firecrawl-py SDK for self-hosted instance.

CONSTITUTIONAL CONSTRAINT: This tool respects robots.txt by default.
Rate limiting is enforced at the infrastructure level (Firecrawl Redis),
NOT in this code — consistent with the DGM safety invariant.
"""

import os
import json
import logging
from typing import Optional, Type
from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from firecrawl import Firecrawl

logger = logging.getLogger("crewai-team.tools.firecrawl")

# Singleton client — reuse across tool instances
_firecrawl_client: Optional[Firecrawl] = None

def get_firecrawl_client() -> Firecrawl:
    """
    Returns a singleton Firecrawl client pointed at the self-hosted instance.
    Falls back to cloud API if FIRECRAWL_API_URL is not set.
    """
    global _firecrawl_client
    if _firecrawl_client is None:
        api_url = os.getenv("FIRECRAWL_API_URL", "http://firecrawl-api:3002")
        api_key = os.getenv("FIRECRAWL_API_KEY", "")  # Not needed for self-hosted without auth
        _firecrawl_client = Firecrawl(
            api_key=api_key or "self-hosted",
            api_url=api_url,
            max_retries=3,
            backoff_factor=0.5,
        )
        logger.info(f"Firecrawl client initialized: {api_url}")
    return _firecrawl_client


# --- Tool 1: Smart Scrape (single page → markdown) ---

class ScrapeInput(BaseModel):
    url: str = Field(description="The URL to scrape")
    only_main_content: bool = Field(
        default=True,
        description="If true, excludes headers/footers/nav. Set false for full page."
    )
    formats: list[str] = Field(
        default=["markdown"],
        description="Output formats: 'markdown', 'html', 'links', 'screenshot'"
    )

class FirecrawlSmartScrapeTool(BaseTool):
    name: str = "firecrawl_scrape"
    description: str = (
        "Scrape a single web page and return clean, LLM-ready content. "
        "Returns markdown by default. Use for reading articles, docs, product pages. "
        "Does NOT follow links — use firecrawl_crawl for multi-page extraction."
    )
    args_schema: Type[BaseModel] = ScrapeInput

    def _run(self, url: str, only_main_content: bool = True, formats: list[str] = None) -> str:
        if formats is None:
            formats = ["markdown"]
        client = get_firecrawl_client()
        try:
            result = client.scrape(
                url,
                formats=formats,
                only_main_content=only_main_content,
                timeout=30000,
            )
            # Return markdown content, truncated to avoid context window bloat
            content = result.get("markdown", result.get("html", ""))
            metadata = result.get("metadata", {})
            title = metadata.get("title", "Unknown")
            source = metadata.get("sourceURL", url)

            # Truncate to ~8000 chars to leave room in agent context
            if len(content) > 8000:
                content = content[:8000] + f"\n\n[TRUNCATED — full page is {len(content)} chars]"

            return f"# {title}\nSource: {source}\n\n{content}"

        except Exception as e:
            logger.error(f"Firecrawl scrape failed for {url}: {e}")
            return f"Error scraping {url}: {str(e)}"


# --- Tool 2: Structured Extract (page → typed JSON via Pydantic) ---

class ExtractInput(BaseModel):
    url: str = Field(description="The URL to extract structured data from")
    prompt: str = Field(description="Natural language description of what to extract")
    schema_json: Optional[str] = Field(
        default=None,
        description="Optional JSON schema string for structured output"
    )

class FirecrawlStructuredExtractTool(BaseTool):
    name: str = "firecrawl_extract"
    description: str = (
        "Extract structured data from a web page using LLM-powered extraction. "
        "Provide a natural language prompt describing what to extract. "
        "Optionally provide a JSON schema for typed output. "
        "Uses the local Ollama model for extraction (no cloud LLM calls). "
        "Best for: pricing tables, contact info, product specs, event details."
    )
    args_schema: Type[BaseModel] = ExtractInput

    def _run(self, url: str, prompt: str, schema_json: Optional[str] = None) -> str:
        client = get_firecrawl_client()
        try:
            extract_params = {
                "urls": [url],
                "prompt": prompt,
            }
            if schema_json:
                try:
                    extract_params["schema"] = json.loads(schema_json)
                except json.JSONDecodeError:
                    return "Error: schema_json is not valid JSON"

            result = client.extract(**extract_params)
            return json.dumps(result.get("data", {}), indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Firecrawl extract failed for {url}: {e}")
            return f"Error extracting from {url}: {str(e)}"


# --- Tool 3: Crawl (multi-page → markdown corpus) ---

class CrawlInput(BaseModel):
    url: str = Field(description="Starting URL to crawl from")
    max_pages: int = Field(
        default=20,
        description="Maximum number of pages to crawl (keep low to avoid resource exhaustion)"
    )
    include_patterns: Optional[list[str]] = Field(
        default=None,
        description="URL glob patterns to include, e.g. ['/docs/*', '/blog/*']"
    )
    exclude_patterns: Optional[list[str]] = Field(
        default=None,
        description="URL glob patterns to exclude, e.g. ['/login', '/admin/*']"
    )

class FirecrawlCrawlTool(BaseTool):
    name: str = "firecrawl_crawl"
    description: str = (
        "Crawl a website starting from a URL, following links to discover and scrape "
        "multiple pages. Returns markdown content for each page found. "
        "WARNING: This is resource-intensive. Set max_pages conservatively (default: 20). "
        "Use include_patterns to focus the crawl on relevant sections. "
        "Best for: documentation sites, knowledge bases, competitor analysis."
    )
    args_schema: Type[BaseModel] = CrawlInput

    def _run(
        self,
        url: str,
        max_pages: int = 20,
        include_patterns: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ) -> str:
        # Hard cap to prevent runaway crawls
        max_pages = min(max_pages, 50)

        client = get_firecrawl_client()
        try:
            crawl_params = {
                "limit": max_pages,
                "scrape_options": {
                    "formats": ["markdown"],
                    "only_main_content": True,
                },
            }
            if include_patterns:
                crawl_params["include_paths"] = include_patterns
            if exclude_patterns:
                crawl_params["exclude_paths"] = exclude_patterns

            # Synchronous crawl — blocks until complete
            result = client.crawl(url, **crawl_params)

            pages = result.get("data", [])
            output_parts = [f"Crawled {len(pages)} pages from {url}\n"]

            for i, page in enumerate(pages):
                title = page.get("metadata", {}).get("title", f"Page {i+1}")
                page_url = page.get("metadata", {}).get("sourceURL", "")
                content = page.get("markdown", "")[:3000]  # Truncate per page
                output_parts.append(f"\n---\n## [{i+1}] {title}\nURL: {page_url}\n\n{content}")

            return "\n".join(output_parts)

        except Exception as e:
            logger.error(f"Firecrawl crawl failed for {url}: {e}")
            return f"Error crawling {url}: {str(e)}"


# --- Tool 4: Web Search (search + scrape in one) ---

class SearchInput(BaseModel):
    query: str = Field(description="Search query string")
    limit: int = Field(
        default=5,
        description="Number of results to return (1-10)"
    )

class FirecrawlWebSearchTool(BaseTool):
    name: str = "firecrawl_search"
    description: str = (
        "Search the web and return full content from the top results. "
        "Unlike a regular search engine, this returns the actual page content "
        "as markdown, not just snippets. "
        "NOTE: Requires SearXNG or SERPER_API_KEY configured on the Firecrawl instance. "
        "Best for: research queries, finding specific information across multiple sources."
    )
    args_schema: Type[BaseModel] = SearchInput

    def _run(self, query: str, limit: int = 5) -> str:
        limit = min(max(limit, 1), 10)
        client = get_firecrawl_client()
        try:
            results = client.search(query, limit=limit)
            output_parts = [f"Search results for: '{query}'\n"]

            for i, result in enumerate(results.get("data", [])):
                title = result.get("title", f"Result {i+1}")
                url = result.get("url", "")
                content = result.get("markdown", "")[:2000]
                output_parts.append(f"\n---\n### [{i+1}] {title}\nURL: {url}\n\n{content}")

            return "\n".join(output_parts)

        except Exception as e:
            logger.error(f"Firecrawl search failed for '{query}': {e}")
            return f"Error searching '{query}': {str(e)}"


# --- Tool 5: Site Map (URL discovery) ---

class MapInput(BaseModel):
    url: str = Field(description="Website URL to map")

class FirecrawlMapTool(BaseTool):
    name: str = "firecrawl_map"
    description: str = (
        "Discover all accessible URLs on a website. Returns a list of URLs "
        "without scraping content. Fast operation using sitemaps and link crawling. "
        "Use this before firecrawl_crawl to understand site structure."
    )
    args_schema: Type[BaseModel] = MapInput

    def _run(self, url: str) -> str:
        client = get_firecrawl_client()
        try:
            result = client.map(url)
            urls = result.get("links", [])
            return f"Found {len(urls)} URLs on {url}:\n\n" + "\n".join(urls[:100])
        except Exception as e:
            logger.error(f"Firecrawl map failed for {url}: {e}")
            return f"Error mapping {url}: {str(e)}"
```

### 3.4 Agent Configuration (YAML)

```yaml
# config/agents.yaml — additions for web-enabled agents

researcher:
  role: "Senior Research Analyst"
  goal: >
    Gather comprehensive, verified information from the web and internal
    knowledge bases to support the team's objectives.
  backstory: >
    You are a meticulous researcher who cross-references multiple sources.
    You use firecrawl_search for broad queries, firecrawl_scrape for
    specific pages, and firecrawl_extract for structured data.
    You always cite your sources with URLs.
  tools:
    - firecrawl_search
    - firecrawl_scrape
    - firecrawl_extract
    - firecrawl_map
    # firecrawl_crawl intentionally excluded from default researcher
    # — too resource-intensive for routine tasks

commander:
  role: "Strategic Commander"
  goal: >
    Orchestrate the team to accomplish complex objectives efficiently.
  backstory: >
    You coordinate agents and decide when deep web research is needed.
    You can use firecrawl_crawl for comprehensive site analysis but
    only when explicitly needed for the task.
  tools:
    - firecrawl_crawl
    - firecrawl_map
```

### 3.5 Tool Registration in CrewAI

```python
# crew.py — tool registration
from tools.firecrawl_tools import (
    FirecrawlSmartScrapeTool,
    FirecrawlStructuredExtractTool,
    FirecrawlCrawlTool,
    FirecrawlWebSearchTool,
    FirecrawlMapTool,
)

# Instantiate tools (singleton client handles connection)
firecrawl_tools = [
    FirecrawlSmartScrapeTool(),
    FirecrawlStructuredExtractTool(),
    FirecrawlCrawlTool(),
    FirecrawlWebSearchTool(),
    FirecrawlMapTool(),
]

# Register with appropriate agents
researcher = Agent(
    config=self.agents_config["researcher"],
    tools=[
        FirecrawlSmartScrapeTool(),
        FirecrawlStructuredExtractTool(),
        FirecrawlWebSearchTool(),
        FirecrawlMapTool(),
    ],
    llm=self.get_llm("researcher"),  # Your four-tier cascade
)
```

---

## 4. RAG Pipeline Integration

### 4.1 Firecrawl → ChromaDB Ingestion Flow

```
URL/Query → Firecrawl (scrape/crawl) → Markdown → Chunking → Embedding → ChromaDB
                                          │
                                          ▼
                                    Metadata enrichment:
                                    - source_url
                                    - scrape_timestamp
                                    - content_type
                                    - page_title
                                    - epistemological_tag: "web_source"
```

```python
# pipelines/web_ingest.py
"""
Pipeline for ingesting Firecrawl output into ChromaDB.
Integrates with the epistemological tagging system from the
philosophical RAG layer.
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional
from firecrawl import Firecrawl
import chromadb

def ingest_url_to_chromadb(
    url: str,
    collection_name: str = "web_knowledge",
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    tags: Optional[dict] = None,
) -> dict:
    """
    Scrape a URL via self-hosted Firecrawl and ingest into ChromaDB.

    Returns dict with:
    - chunks_ingested: int
    - source_url: str
    - content_hash: str (for deduplication)
    """
    client = get_firecrawl_client()  # From tools module

    # Scrape
    result = client.scrape(url, formats=["markdown"], only_main_content=True)
    markdown = result.get("markdown", "")
    metadata = result.get("metadata", {})

    if not markdown:
        return {"chunks_ingested": 0, "error": "No content extracted"}

    # Content hash for deduplication
    content_hash = hashlib.sha256(markdown.encode()).hexdigest()[:16]

    # Chunk (simple recursive character splitter — replace with your preferred chunker)
    chunks = _chunk_text(markdown, chunk_size, chunk_overlap)

    # Build metadata
    base_meta = {
        "source_url": url,
        "page_title": metadata.get("title", ""),
        "scrape_timestamp": datetime.now(timezone.utc).isoformat(),
        "content_hash": content_hash,
        "epistemological_tag": "web_source",  # Integrates with philosophical RAG
        "verification_status": "unverified",  # Must be manually promoted
    }
    if tags:
        base_meta.update(tags)

    # Ingest to ChromaDB
    chroma_client = chromadb.HttpClient(host="chromadb", port=8000)
    collection = chroma_client.get_or_create_collection(collection_name)

    ids = [f"{content_hash}_{i}" for i in range(len(chunks))]
    metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]

    collection.upsert(
        ids=ids,
        documents=chunks,
        metadatas=metadatas,
    )

    return {
        "chunks_ingested": len(chunks),
        "source_url": url,
        "content_hash": content_hash,
    }


def ingest_crawl_to_chromadb(
    url: str,
    max_pages: int = 20,
    collection_name: str = "web_knowledge",
    **kwargs,
) -> dict:
    """Crawl a site and ingest all pages."""
    client = get_firecrawl_client()
    result = client.crawl(url, limit=max_pages, scrape_options={
        "formats": ["markdown"],
        "only_main_content": True,
    })

    total_chunks = 0
    pages_ingested = 0

    for page in result.get("data", []):
        page_url = page.get("metadata", {}).get("sourceURL", url)
        # Re-use single-page ingestion
        page_result = _ingest_markdown(
            markdown=page.get("markdown", ""),
            metadata=page.get("metadata", {}),
            collection_name=collection_name,
            **kwargs,
        )
        total_chunks += page_result.get("chunks_ingested", 0)
        pages_ingested += 1

    return {
        "pages_ingested": pages_ingested,
        "total_chunks": total_chunks,
        "source_url": url,
    }


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Simple chunker. Replace with LangChain/LlamaIndex splitter as needed."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return [c for c in chunks if c.strip()]
```

### 4.2 Epistemological Tagging Integration

Your philosophical RAG layer uses epistemological tags. Web content should be tagged as `web_source` with `verification_status: "unverified"` by default. This ensures that when agents retrieve web-sourced knowledge, the response can appropriately flag confidence levels.

```python
EPISTEMOLOGICAL_TAGS = {
    "web_source": {
        "confidence": "low-to-medium",
        "requires_verification": True,
        "decay_rate": "high",  # Web content goes stale quickly
        "citation_required": True,
    },
    "web_source_verified": {
        "confidence": "medium-to-high",
        "requires_verification": False,
        "decay_rate": "medium",
        "citation_required": True,
    },
    # ... existing tags: philosophical_text, empirical_research, etc.
}
```

---

## 5. Safety Architecture

### 5.1 Constitutional Constraints for Web Scraping

**Consistent with your DGM invariant**: Safety constraints enforced at infrastructure level, not in agent code.

```yaml
# CONSTITUTION.md — additions for web scraping

## Web Access Constraints

### Rate Limiting (enforced by Firecrawl Redis, NOT agent code)
- MAX_CONCURRENT_JOBS: 10
- CRAWL_CONCURRENT_REQUESTS: 5
- Per-domain rate limit: Firecrawl respects robots.txt crawl-delay by default

### Scope Restrictions (enforced by firewall/proxy rules)
- Agents MUST NOT scrape:
  - Government classified sites
  - Sites requiring authentication (unless explicitly configured)
  - Personal social media profiles for surveillance purposes
  - Sites with explicit "no AI scraping" in robots.txt or terms

### Data Handling
- All scraped content tagged with epistemological_tag: "web_source"
- No scraped content auto-promoted to "verified" status
- Scrape results exceeding 50KB per page are truncated
- Crawls hard-capped at 50 pages per invocation

### Self-Improver Restrictions
- The Self-Improver agent MUST NOT modify:
  - Firecrawl tool implementations
  - Rate limiting configurations
  - Constitutional web access rules
  - Epistemological tag definitions for web sources
```

### 5.2 Environment Variables (`.env.firecrawl`)

```bash
# === Required ===
FC_BULL_AUTH_KEY=generate-a-strong-key-here

# === Resource Limits ===
FC_NUM_WORKERS=4
FC_CRAWL_CONCURRENT=5
FC_MAX_JOBS=10
FC_BLOCK_MEDIA=true
FC_LOG_LEVEL=info

# === LLM for Extraction ===
# Points to host Ollama via Docker's host.docker.internal
FC_MODEL_NAME=qwen3:30b-a3b

# === Search (pick one) ===
# Option A: SearXNG (self-hosted, recommended)
# SEARXNG_ENDPOINT=http://searxng:8080
# Option B: Serper API
# SERPER_API_KEY=your-key

# === Proxy (optional, for anti-bot sites) ===
# PROXY_SERVER=socks5://proxy:1080
# PROXY_USERNAME=
# PROXY_PASSWORD=
```

---

## 6. Implementation Phases

### Phase 0: Infrastructure (Day 1-2)

- [ ] Create `docker-compose.firecrawl.yml` as specified above
- [ ] Create shared Docker network: `docker network create crewai-bridge`
- [ ] Update `crewai-team` Docker Compose to join `crewai-bridge` network
- [ ] Generate `FC_BULL_AUTH_KEY`
- [ ] `docker compose -f docker-compose.firecrawl.yml up -d`
- [ ] Verify: `curl http://localhost:3002/` returns Firecrawl status
- [ ] Verify: `curl -X POST http://localhost:3002/v2/scrape -H 'Content-Type: application/json' -d '{"url": "https://example.com"}'`
- [ ] Verify Bull Queue UI: `http://localhost:3002/admin/{BULL_AUTH_KEY}/queues`
- [ ] Monitor resource usage: `docker stats`

### Phase 1: Basic Tool Integration (Day 2-3)

- [ ] Install `firecrawl-py` in crewai-team container
- [ ] Create `tools/firecrawl_tools.py` with all five tools
- [ ] Set `FIRECRAWL_API_URL=http://firecrawl-api:3002` in crewai-team env
- [ ] Register tools with Researcher and Commander agents
- [ ] Test: Researcher agent scrapes a known page
- [ ] Test: Structured extraction with Pydantic schema via Ollama

### Phase 2: RAG Pipeline (Day 3-5)

- [ ] Create `pipelines/web_ingest.py`
- [ ] Add `web_source` epistemological tag to tag registry
- [ ] Create ChromaDB collection `web_knowledge`
- [ ] Test: Ingest a documentation site (e.g., CrewAI docs)
- [ ] Test: Agent retrieves web-sourced knowledge with proper citations

### Phase 3: Search Integration (Day 5-7)

- [ ] Deploy SearXNG container (or configure Serper API key)
- [ ] Configure `SEARXNG_ENDPOINT` in Firecrawl env
- [ ] Test: `firecrawl_search` tool returns real results
- [ ] Integrate search results into agent research workflows

### Phase 4: Signal Control Integration (Day 7-10)

- [ ] Add Signal command handlers for web operations:
  - `/scrape <url>` — trigger scrape and return summary
  - `/crawl <url> [max_pages]` — trigger crawl with approval
  - `/ingest <url>` — scrape + ingest to ChromaDB
- [ ] Add human-in-the-loop approval for crawls > 20 pages
- [ ] Add Signal notification for crawl completion

### Phase 5: MCP Server (Future)

- [ ] Deploy `firecrawl-mcp` pointed at self-hosted instance
- [ ] Configure `FIRECRAWL_API_URL=http://firecrawl-api:3002`
- [ ] Evaluate MCP vs direct SDK integration performance
- [ ] [Decision point] Migrate to MCP if protocol benefits outweigh latency cost

---

## 7. Known Issues & Mitigations

| Issue | Source | Mitigation |
|---|---|---|
| Ollama extraction is experimental | Firecrawl docs | Monitor extraction quality. Fall back to `OPENAI_BASE_URL` via OpenRouter if extraction fails consistently. |
| Self-hosted lacks Fire-engine | Firecrawl docs | Anti-bot sites will fail. Configure proxy (`PROXY_SERVER`) for important targets. |
| `/extract` + Ollama sometimes returns empty `{}` | GitHub issue #1467, #1294 | Ensure `OLLAMA_BASE_URL` ends with `/api`. Use a model with strong JSON/structured output capability. `qwen3:30b-a3b` should handle this well given its function calling support. |
| Playwright Chromium memory spikes | General Docker + Chromium issue | `shm_size: 1gb`, `BLOCK_MEDIA: true`, memory limit on container. |
| First Docker build is slow (~5-15 min) | Firecrawl docs | Use pre-built `ghcr.io/firecrawl/firecrawl:latest` images. Subsequent starts are fast. |
| ARM64 Playwright image availability | Verified in v2.8.0 release notes | Multi-arch images ship since v2.8.0. Use `ghcr.io/firecrawl/playwright-service:latest`. |

---

## 8. Verification Checklist

Before considering this integration complete:

- [ ] Firecrawl API responds on `http://firecrawl-api:3002` from crewai-team container
- [ ] Ollama extraction produces structured JSON (not empty `{}`)
- [ ] Researcher agent can scrape, extract, and search
- [ ] Crawl results ingest into ChromaDB with epistemological tags
- [ ] Self-Improver agent cannot modify Firecrawl tool code or rate limits
- [ ] Signal commands for scrape/crawl work end-to-end
- [ ] System stays under 40GB total RAM during concurrent crawl + Ollama inference
- [ ] robots.txt is respected (test with a known robots.txt-restricted path)

---

## Appendix: Quick Reference

### Firecrawl Python SDK Core API

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="self-hosted", api_url="http://firecrawl-api:3002")

# Scrape single page
result = app.scrape("https://example.com", formats=["markdown"])

# Crawl site
result = app.crawl("https://docs.example.com", limit=20)

# Map site URLs
result = app.map("https://example.com")

# Search web (requires SearXNG/Serper)
result = app.search("query string", limit=5)

# Extract structured data (requires LLM — Ollama)
result = app.extract(
    urls=["https://example.com/pricing"],
    prompt="Extract pricing tiers with names and prices",
    schema={"type": "object", "properties": {...}},
)
```

### Key Environment Variables

| Variable | Value | Purpose |
|---|---|---|
| `FIRECRAWL_API_URL` | `http://firecrawl-api:3002` | Self-hosted instance URL (set in crewai-team) |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434/api` | Route LLM extraction to host Ollama |
| `MODEL_NAME` | `qwen3:30b-a3b` | Ollama model for extraction |
| `BLOCK_MEDIA` | `true` | Skip images/video in Playwright |
| `USE_DB_AUTHENTICATION` | `false` | No Supabase needed for self-hosted |

### Source References

- Firecrawl GitHub: https://github.com/firecrawl/firecrawl
- Self-hosting docs: https://docs.firecrawl.dev/contributing/self-host
- CrewAI integration: https://docs.firecrawl.dev/integrations/crewai
- Python SDK: https://pypi.org/project/firecrawl-py/
- MCP Server: https://github.com/firecrawl/firecrawl-mcp-server
- Firecrawl v2.8.0 release (ARM64): https://github.com/firecrawl/firecrawl/releases/tag/v2.8.0

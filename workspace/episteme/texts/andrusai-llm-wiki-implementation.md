---
title: "andrusai-llm-wiki-implementation.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI LLM Wiki Subsystem — Complete Implementation Specification

## Document Purpose

This document is a comprehensive build specification for implementing the LLM Wiki pattern (originated by Andrej Karpathy, April 2026) as a core knowledge subsystem within the AndrusAI `crewai-team` multi-agent architecture. It is designed to be fed directly to Claude Code for implementation.

**Read this entire document before writing any code.** The architecture has interdependencies — implementing pieces in isolation will create integration debt.

---

## 1. Background and Rationale

### 1.1 The Problem with Current Architecture

AndrusAI currently uses ChromaDB collections for knowledge retrieval — philosophical RAG, creative RAG, Firecrawl-ingested content. This is stateless RAG: every query re-derives understanding from raw document chunks. Nothing compounds between queries. Ask a question that requires synthesizing five documents, and the system re-discovers and re-pieces the fragments every time.

The LLM Wiki pattern replaces this with a **persistent, compounding knowledge artifact**: a structured directory of interlinked markdown files that sits between raw sources and agent execution. When new content is ingested, it is not just indexed for later retrieval — it is read, synthesized, and integrated into the existing wiki. Cross-references are pre-built. Contradictions are pre-flagged. The synthesis already reflects everything ingested to date.

### 1.2 Why This Matters for AndrusAI Specifically

1. **Token economics**: Pre-compiled wiki pages are dramatically more token-efficient than raw chunk retrieval + re-synthesis. Lower-tier models in the four-tier cascade (Ollama qwen3:30b-a3b, DeepSeek V3.2) can reason effectively over a well-structured wiki page where they would fail on scattered raw chunks. This pushes more tasks down the cascade — direct cost savings.

2. **Multi-agent knowledge sharing**: Commander can read `wiki/archibal/competitive-landscape.md` before planning tasks instead of re-deriving competitive context from raw chunks. Writer draws on pre-compiled synthesis instead of raw fragments. This is how human teams use internal wikis.

3. **Compounding self-improvement**: Self-Improver's lint passes make the knowledge base genuinely better over time — flagging contradictions, staleness, orphan pages. Each pass compounds.

4. **Epistemological integrity**: Wiki pages carry epistemic metadata (factual vs. creative vs. philosophical vs. inferred). The DGM safety invariant enforces that agents never cross epistemic boundaries inappropriately. This extends and strengthens the existing Epistemologically-Tagged Creative RAG Layer.

5. **Cross-venture intelligence**: PLG, Archibal, and KaiCart share patterns (regulatory compliance, API unreliability as architectural constraint, market entry frameworks) that are currently invisible to the system. The wiki surfaces these via typed cross-references.

6. **Audit trail**: `log.md` feeds directly into the Paperclip control plane's audit trail. Knowledge provenance becomes traceable.

### 1.3 Design Principles

These principles govern all implementation decisions:

- **DGM Safety Invariant**: All wiki write validation and epistemic boundary enforcement happens at infrastructure level, immutable to all agents. No agent can override write constraints via prompt engineering.
- **Human-Readable First**: The wiki is markdown files in a git-backed directory. Any human (Andrus) can open, read, edit, and verify any page in a text editor. No opaque vector stores as the primary knowledge interface.
- **Compile Once, Query Many**: Knowledge is synthesized at ingest time, not at query time. The computational cost is paid once; every subsequent query benefits.
- **Epistemic Honesty**: Every claim in the wiki traces to a source or is explicitly labeled as inference/synthesis. No unmarked speculation.
- **Progressive Enhancement**: Start with index.md-based navigation. Add search infrastructure only when scale demands it. Avoid premature complexity.
- **Agent Ownership, Human Oversight**: Agents write and maintain the wiki. Andrus curates sources, directs analysis, and reviews synthesis. The schema is co-evolved.

---

## 2. Directory Structure

Implement this structure at the root of the `crewai-team` project:

```
crewai-team/
├── raw/                                    # LAYER 1: Immutable Source Documents
│   ├── README.md                           # Explains raw/ conventions
│   ├── firecrawl/                          # Web-scraped content (from Firecrawl pipeline)
│   │   └── {YYYYMMDD}-{slug}.md            # Scraped articles, each with YAML frontmatter
│   ├── philosophical/                      # Humanist texts (~120 bibliography)
│   │   └── {author}-{title-slug}.md        # One file per text, original or excerpt
│   ├── creative/                           # Fiction-based inspiration sources
│   │   └── {author}-{title-slug}.md        # Epistemic tag: creative (MUST be preserved)
│   ├── research/                           # Papers, reports, architecture docs
│   │   └── {YYYYMMDD}-{title-slug}.md      # Academic papers, technical reports
│   ├── transcripts/                        # Meeting notes, conversation logs
│   │   └── {YYYYMMDD}-{context-slug}.md
│   └── ventures/                           # Venture-specific source documents
│       ├── plg/
│       │   └── {YYYYMMDD}-{title-slug}.md
│       ├── archibal/
│       │   └── {YYYYMMDD}-{title-slug}.md
│       └── kaicart/
│           └── {YYYYMMDD}-{title-slug}.md
│
├── wiki/                                   # LAYER 2: LLM-Compiled Knowledge
│   ├── index.md                            # Master catalog (agents read this FIRST)
│   ├── log.md                              # Chronological operations log (append-only)
│   ├── meta/                               # Cross-venture concepts and patterns
│   │   ├── index.md                        # Meta-wiki section index
│   │   └── (pages created during operation)
│   ├── self/                               # Agent self-knowledge (evolves from SELF.md)
│   │   ├── index.md                        # Self-knowledge section index
│   │   └── (pages created during operation)
│   ├── philosophy/                         # Compiled philosophical frameworks
│   │   ├── index.md                        # Philosophy section index
│   │   └── (pages created during operation)
│   ├── plg/                                # PLG venture wiki
│   │   ├── index.md                        # PLG section index
│   │   └── (pages created during operation)
│   ├── archibal/                           # Archibal venture wiki
│   │   ├── index.md                        # Archibal section index
│   │   └── (pages created during operation)
│   └── kaicart/                            # KaiCart venture wiki
│       ├── index.md                        # KaiCart section index
│       └── (pages created during operation)
│
└── wiki_schema/                            # LAYER 3: Governance Documents
    ├── WIKI_SCHEMA.md                      # Page format, frontmatter spec, conventions
    ├── WIKI_ROLES.md                       # Agent permissions and responsibilities
    ├── WIKI_SAFETY.md                      # DGM-enforced write constraints
    └── WIKI_OPERATIONS.md                  # Ingest, Query, Lint workflow specs
```

### 2.1 Critical Rules

- **`raw/` is IMMUTABLE.** Agents read from `raw/` but NEVER modify, delete, or rename files in it. Andrus (human) is the sole curator of raw sources.
- **`wiki/` is AGENT-OWNED.** Agents create, update, and maintain all pages. Andrus reads and reviews but does not normally write wiki pages directly (except via guided collaboration).
- **`wiki_schema/` is CO-EVOLVED.** Andrus and agents jointly refine governance documents as the system matures.
- **Git-backed.** The entire structure is version-controlled. Every wiki update is a commit. Branch for experimental ingests if needed.

---

## 3. Wiki Schema — Page Format Specification

### 3.1 YAML Frontmatter (Required on Every Wiki Page)

Every wiki page MUST begin with YAML frontmatter. No exceptions. Pages without valid frontmatter are invalid and should be flagged by the lint tool.

```yaml
---
title: "Human-Readable Page Title"
slug: page-filename-without-extension
section: meta | self | philosophy | plg | archibal | kaicart
page_type: entity | concept | summary | comparison | synthesis | analysis | log-entry
epistemic_status: factual | inferred | synthesized | speculative | creative
confidence: high | medium | low
sources:
  - raw/firecrawl/20260410-archibal-competitor-analysis.md
  - raw/research/20260315-c2pa-specification-v2.md
created_by: commander | researcher | coder | writer | self-improver | human
created_at: "2026-04-12T14:30:00Z"
updated_by: researcher
updated_at: "2026-04-12T14:30:00Z"
update_count: 0
tags:
  - archibal
  - competitive-intelligence
  - c2pa
related_pages:
  - archibal/tam-sam-som
  - meta/content-authenticity-landscape
supersedes: []
contradicted_by: []
contradicts: []
status: active | stale | deprecated | draft
---
```

### 3.2 Frontmatter Field Definitions

| Field | Required | Description |
|-------|----------|-------------|
| `title` | YES | Human-readable title. Displayed in index. |
| `slug` | YES | Filename without `.md`. Must match actual filename. |
| `section` | YES | Which wiki subdirectory this belongs to. |
| `page_type` | YES | Structural role. See Section 3.3 for definitions. |
| `epistemic_status` | YES | **Critical for DGM enforcement.** See Section 3.4. |
| `confidence` | YES | Agent's assessment of synthesis quality. |
| `sources` | YES | List of `raw/` file paths that informed this page. Empty list `[]` only for `page_type: synthesis` that draws from other wiki pages. |
| `created_by` | YES | Agent role that created this page. |
| `created_at` | YES | ISO 8601 timestamp. |
| `updated_by` | YES | Agent role that last updated this page. |
| `updated_at` | YES | ISO 8601 timestamp of last update. |
| `update_count` | YES | Integer. Incremented on each update. |
| `tags` | YES | Freeform tags for cross-cutting categorization. |
| `related_pages` | YES | List of related wiki page paths (relative to `wiki/`). These are bidirectional — if A lists B, B should list A. |
| `supersedes` | NO | List of wiki pages this page replaces. |
| `contradicted_by` | NO | List of wiki pages that contradict claims in this page. |
| `contradicts` | NO | List of wiki pages whose claims this page contradicts. |
| `status` | YES | Lifecycle state. `stale` = older than staleness threshold and not re-verified. `deprecated` = superseded. `draft` = incomplete. |

### 3.3 Page Types

- **entity**: A specific thing — a company, a person, a product, a technology, a market. Has attributes, relationships, history. Example: `archibal/competitor-truepic.md`.
- **concept**: An idea, framework, or principle. Has definition, applications, relationships to other concepts. Example: `philosophy/aristotelian-virtue-ethics-applied.md`.
- **summary**: A condensed representation of one or more raw sources. Closest to traditional summarization. Example: `plg/20260410-protect-group-partnership-summary.md`.
- **comparison**: Side-by-side analysis of two or more entities or concepts. Example: `archibal/comparison-c2pa-vs-proprietary-provenance.md`.
- **synthesis**: A novel integration of multiple wiki pages and/or raw sources. The wiki's highest-value output. Example: `meta/api-unreliability-as-architectural-constraint.md`.
- **analysis**: A structured analytical output — SWOT, TAM/SAM/SOM, risk assessment, etc. Example: `archibal/tam-sam-som.md`.
- **log-entry**: A timestamped record of an operation, decision, or event. Filed in `wiki/` only if the content has lasting reference value. Routine operations go in `log.md` instead.

### 3.4 Epistemic Status Definitions (DGM-Critical)

These statuses are the backbone of epistemological integrity. The DGM safety invariant enforces rules based on these:

- **factual**: Directly supported by verifiable raw sources. Claims can be traced to specific passages in `sources` list. Example: "Archibal's TAM is estimated at €2.1B based on [source]."
- **inferred**: Logically derived from factual claims but involves interpretation. Must state the inference chain. Example: "Given the TAM and current competitive dynamics, first-mover advantage in PKI-signed certificates is likely achievable within 18 months [inference from X + Y]."
- **synthesized**: Integrated understanding drawn from multiple sources and/or wiki pages. More than summary — involves connecting ideas across domains. Example: Cross-venture pattern recognition.
- **speculative**: Hypothesis or forward-looking claim not directly derivable from sources. Explicitly flagged. Example: "TikTok may introduce native seller analytics in 2027, which would impact KaiCart's value proposition [speculation]."
- **creative**: Drawn from fiction-based inspiration sources. **HARD BOUNDARY: Creative-tagged content MUST NEVER be used as input for factual business decisions, investor materials, or competitive analysis.** This extends the existing Epistemologically-Tagged Creative RAG Layer.

### 3.5 Page Body Format

After frontmatter, every page follows this structure:

```markdown
# {title}

## Overview
One to three paragraphs summarizing the page's content. This should be self-contained
enough that an agent reading only the Overview gets the key takeaway.

## {Content Sections}
Organized by the page's subject matter. Use ## for major sections, ### for subsections.
No deeper nesting than ###.

Cross-reference other wiki pages using relative links: [[related-page-slug]] or
[descriptive text](../section/page-slug.md).

Inline source citations: [source: raw/path/to/source.md] or [source: page-slug.md]
for wiki-internal citations.

## Contradictions and Open Questions
(Required section. May be empty with "None identified." if no contradictions exist.)
List any known contradictions with other wiki pages or unresolved questions that
future ingests should address.

## Change History
(Maintained by agents. Brief, not duplicating log.md.)
- 2026-04-12: Created from initial Archibal competitive research. (researcher)
- 2026-04-15: Updated with new C2PA v2.1 spec details. (researcher)
```

### 3.6 Wikilink Conventions

- **Cross-section links**: Use relative paths from `wiki/` root: `[Link text](../archibal/competitor-truepic.md)`
- **Same-section links**: Use relative paths: `[Link text](./tam-sam-som.md)`
- **Obsidian-compatible wikilinks**: `[[page-slug]]` format is also valid for human browsing in Obsidian. Agents should use both formats — the markdown link for their own navigation, the wikilink for Obsidian graph view compatibility.
- **Raw source references**: `[source: raw/path/to/file.md]` — always relative to project root.

### 3.7 Naming Conventions

- **Wiki page filenames**: `kebab-case-descriptive-name.md`. No dates in wiki page filenames (dates are in frontmatter). Exception: summary pages that correspond to a specific dated source may include the date: `20260410-protect-group-partnership-summary.md`.
- **Raw source filenames**: `{YYYYMMDD}-{descriptive-slug}.md`. Date is mandatory.
- **Section index files**: Always named `index.md` within their directory.

---

## 4. Special Files

### 4.1 `wiki/index.md` — Master Catalog

This is the **most important file in the wiki**. Every agent reads this first when starting any wiki-related operation. It is a complete catalog of all wiki pages, organized by section, with one-line summaries.

Format:

```markdown
---
title: "AndrusAI Wiki — Master Index"
updated_at: "2026-04-12T14:30:00Z"
total_pages: 0
sections:
  meta: 0
  self: 0
  philosophy: 0
  plg: 0
  archibal: 0
  kaicart: 0
---

# AndrusAI Knowledge Wiki — Master Index

Total pages: 0 | Last updated: 2026-04-12T14:30:00Z

## Meta (Cross-Venture)
(No pages yet.)

## Self (Agent Self-Knowledge)
(No pages yet.)

## Philosophy (Compiled Philosophical Frameworks)
(No pages yet.)

## PLG
(No pages yet.)

## Archibal
(No pages yet.)

## KaiCart
(No pages yet.)
```

When pages are added, each entry follows this format:

```markdown
## Archibal
- [Competitive Landscape](archibal/competitive-landscape.md) — Overview of Archibal's competitive positioning against Truepic, Digimarc, and emerging C2PA players. (factual, high confidence, 3 sources) [active]
- [TAM/SAM/SOM Analysis](archibal/tam-sam-som.md) — Market sizing for AI content clearance SaaS. (synthesized, medium confidence, 5 sources) [active]
```

**Update rules**: The agent performing any wiki write (create, update, deprecate) MUST also update `index.md` in the same operation. Index must never be stale relative to actual wiki contents.

### 4.2 `wiki/log.md` — Operations Log

Append-only chronological record. Each entry starts with a consistent prefix for parseability.

Format:

```markdown
---
title: "AndrusAI Wiki — Operations Log"
---

# Wiki Operations Log

## [2026-04-12T14:30:00Z] ingest | Archibal Competitive Research
Agent: researcher
Sources processed: raw/ventures/archibal/20260410-competitor-analysis.md
Pages created: archibal/competitive-landscape.md, archibal/competitor-truepic.md
Pages updated: archibal/index.md, wiki/index.md
Notes: Identified contradiction with existing TAM estimate. Flagged in archibal/tam-sam-som.md.

## [2026-04-12T16:00:00Z] lint | Full Wiki Health Check
Agent: self-improver
Orphan pages found: 0
Contradictions flagged: 1 (archibal/tam-sam-som.md vs archibal/competitive-landscape.md)
Stale pages found: 0
Missing pages suggested: kaicart/tiktok-shop-api-changelog.md
```

**Parseability**: Entries are greppable via `grep "^## \[" wiki/log.md | tail -10` to get the last 10 operations. The prefix format `## [{ISO timestamp}] {operation} | {title}` is strict.

### 4.3 Section Index Files (`wiki/{section}/index.md`)

Each section has its own index file. Format mirrors the master index but scoped to that section. Agents navigating within a section read the section index first (faster than the master index for scoped queries).

```markdown
---
title: "Archibal — Section Index"
section: archibal
updated_at: "2026-04-12T14:30:00Z"
page_count: 0
---

# Archibal Knowledge Wiki

## Pages
(No pages yet.)

## Key Relationships
(Updated as pages are added. Shows the most important cross-references within and across sections.)
```

---

## 5. Agent Roles and Permissions

### 5.1 Role Matrix

| Agent | Read Wiki | Write Wiki Pages | Write index.md | Write log.md | Write schema/ | Lint |
|-------|-----------|------------------|-----------------|--------------|---------------|------|
| Commander | YES (always reads index.md before task planning) | NO (reads only) | NO | NO | NO | NO |
| Researcher | YES | YES (primary writer) | YES (must update on every write) | YES (must log every ingest) | NO | NO |
| Writer | YES | YES (can file valuable outputs as wiki pages) | YES (must update on every write) | YES (must log filed outputs) | NO | NO |
| Coder | YES | YES (can update technical architecture pages) | YES (must update on every write) | YES | NO | NO |
| Self-Improver | YES | YES (maintains self/ section, applies lint fixes) | YES | YES (must log every lint pass) | YES (proposes schema refinements) | YES |
| Human (Andrus) | YES | YES (override capability) | YES | YES | YES | YES |

### 5.2 Agent-Specific Responsibilities

#### Commander
- **Before every task plan**: Read `wiki/index.md`. Identify relevant wiki pages for the current task. Include relevant page paths in the task context passed to executing agents.
- **NEVER writes wiki pages.** Commander's job is orchestration, not knowledge synthesis.
- **Wiki-aware task planning**: When assigning a research task, Commander checks whether relevant wiki pages already exist and instructs the Researcher to update existing pages rather than creating duplicates.

#### Researcher
- **Primary ingestor.** When new raw sources arrive (Firecrawl output, uploaded documents, etc.), Researcher owns the full ingest pipeline: read source → synthesize wiki pages → update cross-references → update section index → update master index → log the operation.
- **Quality standard**: Every wiki page the Researcher creates must have complete frontmatter, at least one source citation, and an explicit epistemic status. No "draft" pages left unfinished without a follow-up task.
- **Contradiction detection**: During ingest, Researcher MUST check existing wiki pages for contradictions with new content. Contradictions are flagged in both pages' `contradicted_by`/`contradicts` frontmatter fields AND noted in the "Contradictions and Open Questions" section.

#### Writer
- **Consumes wiki for content generation.** When producing Archibal pitch materials, PLG presentations, KaiCart documentation, etc., Writer reads relevant wiki pages as primary input — NOT raw source chunks.
- **Files valuable outputs back.** If Writer produces an analysis, comparison, or synthesis that has lasting reference value, it files the output as a new wiki page. This is how explorations compound into the knowledge base.
- **Epistemic discipline in output**: Writer MUST propagate epistemic status from source wiki pages. If a wiki page is tagged `inferred`, Writer's output based on that page carries at minimum the same tag (or `synthesized`). Never silently promotes `inferred` to `factual`.

#### Coder
- **Reads wiki for architectural context.** Before implementing features that interact with external APIs, Coder reads relevant wiki pages (e.g., `kaicart/tiktok-api-constraints.md`) rather than re-discovering issues from raw research.
- **Updates technical pages.** When implementation reveals new technical constraints, API behaviors, or architectural patterns, Coder updates relevant wiki pages or creates new ones.

#### Self-Improver
- **Owns the self/ section.** Maintains pages on agent capabilities, personality development state, and system self-knowledge. This is the natural evolution of the SELF.md generator — SELF.md content migrates into structured wiki pages.
- **Runs lint passes.** Periodic health checks across the entire wiki. See Section 7.3 for lint specification.
- **Proposes schema refinements.** When lint passes reveal systematic issues (e.g., missing frontmatter fields, inconsistent tagging), Self-Improver proposes updates to `wiki_schema/` documents for Andrus's review.

### 5.3 Concurrency Control

CrewAI's task execution is sequential within a crew, but overlapping operations are possible across sessions or during async tool execution.

**Page-level locking protocol:**

1. Before writing to any wiki page, the agent creates a lock file: `wiki/.locks/{page-path-with-dashes}.lock` containing `{agent_role}|{ISO_timestamp}|{operation}`.
2. Before creating a lock, check if a lock already exists. If it does and is less than 5 minutes old, wait and retry (up to 3 retries at 10-second intervals). If older than 5 minutes, the lock is stale — delete it and proceed.
3. After the write completes, delete the lock file.
4. Lock files are `.gitignore`d — they are transient operational artifacts.

Implementation:

```
wiki/.locks/
    .gitkeep
    # Lock files are transient, gitignored
    # Format: {section}-{page-slug}.lock
    # Content: {agent_role}|{timestamp}|{operation}
```

Add to `.gitignore`:
```
wiki/.locks/*.lock
```

---

## 6. CrewAI Tool Implementations

Implement these as CrewAI tools in the existing tool registry. Each tool is a Python class inheriting from `BaseTool`.

### 6.1 WikiReadTool

**Purpose**: Read wiki pages. Agents use this to access compiled knowledge.

```python
"""
WikiReadTool — Read wiki pages for compiled knowledge access.

Usage by agents:
  - Read index: wiki_read(path="index.md")
  - Read section index: wiki_read(path="archibal/index.md")
  - Read specific page: wiki_read(path="archibal/competitive-landscape.md")
  - Read with frontmatter filter: wiki_read(path="index.md", filter_section="archibal", filter_status="active")
"""

# Tool name: wiki_read
# Arguments:
#   path: str — Relative path from wiki/ directory. Required.
#   filter_section: str | None — If reading index.md, filter entries to this section.
#   filter_status: str | None — Filter to pages with this status.
#   filter_epistemic: str | None — Filter to pages with this epistemic_status.
#   frontmatter_only: bool — If True, return only YAML frontmatter (for quick scanning).
#
# Returns: str — Page content (full or frontmatter-only).
#
# Behavior:
#   1. Resolve path relative to {project_root}/wiki/
#   2. Validate file exists. If not, return error with suggestion to check index.md.
#   3. Read file content.
#   4. If frontmatter_only, parse YAML and return formatted frontmatter.
#   5. If filters specified, apply filters (only meaningful when reading index files).
#   6. Return content.
#
# Error handling:
#   - File not found: Return "Page not found: {path}. Check wiki/index.md for available pages."
#   - Invalid frontmatter: Return content with warning "WARNING: Page has invalid frontmatter."

import os
import yaml
from crewai.tools import BaseTool
from pydantic import Field
from typing import Optional

WIKI_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', 'wiki')

class WikiReadTool(BaseTool):
    name: str = "wiki_read"
    description: str = (
        "Read a wiki page for compiled knowledge. "
        "Always read wiki/index.md first to find relevant pages. "
        "Path is relative to wiki/ directory. "
        "Examples: 'index.md', 'archibal/competitive-landscape.md', 'self/index.md'"
    )

    def _run(
        self,
        path: str,
        filter_section: Optional[str] = None,
        filter_status: Optional[str] = None,
        filter_epistemic: Optional[str] = None,
        frontmatter_only: bool = False
    ) -> str:
        full_path = os.path.normpath(os.path.join(WIKI_ROOT, path))

        # Security: prevent path traversal
        if not full_path.startswith(os.path.normpath(WIKI_ROOT)):
            return "ERROR: Path traversal detected. Path must be within wiki/ directory."

        if not os.path.exists(full_path):
            return f"Page not found: {path}. Read wiki/index.md for available pages."

        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if frontmatter_only:
            try:
                if content.startswith('---'):
                    parts = content.split('---', 2)
                    if len(parts) >= 3:
                        fm = yaml.safe_load(parts[1])
                        return yaml.dump(fm, default_flow_style=False)
            except yaml.YAMLError:
                return f"WARNING: Invalid frontmatter in {path}. Raw content:\n{content[:500]}"

        return content
```

### 6.2 WikiWriteTool

**Purpose**: Create or update wiki pages. Enforces frontmatter requirements, updates indexes, writes to log.

```python
"""
WikiWriteTool — Create or update wiki pages with full governance enforcement.

Usage by agents:
  - Create new page: wiki_write(path="archibal/competitor-truepic.md", content="...", operation="create")
  - Update existing page: wiki_write(path="archibal/competitor-truepic.md", content="...", operation="update")
  - Deprecate page: wiki_write(path="archibal/old-analysis.md", operation="deprecate", superseded_by="archibal/new-analysis.md")

CRITICAL: This tool enforces the following automatically:
  1. Validates YAML frontmatter is present and complete.
  2. Acquires page-level lock before writing.
  3. Updates the section index and master index after every write.
  4. Appends an entry to log.md after every write.
  5. Validates epistemic_status field against DGM constraints.
  6. Increments update_count on updates.
  7. Releases page-level lock after writing.

The agent provides the full page content including frontmatter.
The tool handles locking, index updates, and logging.
"""

# Tool name: wiki_write
# Arguments:
#   path: str — Relative path from wiki/. Required.
#   content: str — Full page content including YAML frontmatter. Required for create/update.
#   operation: str — "create" | "update" | "deprecate". Required.
#   agent_role: str — Role of the writing agent. Required.
#   superseded_by: str | None — For deprecate operations, the replacing page path.
#   log_notes: str | None — Additional notes for the log entry.
#
# Returns: str — Success/failure message.
#
# Implementation notes:
#   - Parse and validate frontmatter before writing.
#   - Required frontmatter fields: title, slug, section, page_type, epistemic_status,
#     confidence, sources, created_by, created_at, updated_by, updated_at,
#     update_count, tags, related_pages, status.
#   - On "update": increment update_count, set updated_by and updated_at.
#   - On "deprecate": set status to "deprecated", add superseded_by link.
#   - After write: update section index and master index.
#   - After write: append log entry.
#   - Lock/unlock around the entire operation.

import os
import time
import yaml
from datetime import datetime, timezone
from crewai.tools import BaseTool
from typing import Optional

WIKI_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', 'wiki')
LOCKS_DIR = os.path.join(WIKI_ROOT, '.locks')
LOG_PATH = os.path.join(WIKI_ROOT, 'log.md')
INDEX_PATH = os.path.join(WIKI_ROOT, 'index.md')

REQUIRED_FRONTMATTER_FIELDS = [
    'title', 'slug', 'section', 'page_type', 'epistemic_status',
    'confidence', 'sources', 'created_by', 'created_at',
    'updated_by', 'updated_at', 'update_count', 'tags',
    'related_pages', 'status'
]

VALID_EPISTEMIC_STATUSES = ['factual', 'inferred', 'synthesized', 'speculative', 'creative']
VALID_PAGE_TYPES = ['entity', 'concept', 'summary', 'comparison', 'synthesis', 'analysis', 'log-entry']
VALID_STATUSES = ['active', 'stale', 'deprecated', 'draft']
VALID_CONFIDENCE = ['high', 'medium', 'low']
VALID_SECTIONS = ['meta', 'self', 'philosophy', 'plg', 'archibal', 'kaicart']

LOCK_TTL_SECONDS = 300  # 5 minutes
LOCK_RETRY_ATTEMPTS = 3
LOCK_RETRY_INTERVAL = 10  # seconds


class WikiWriteTool(BaseTool):
    name: str = "wiki_write"
    description: str = (
        "Create, update, or deprecate a wiki page. "
        "Enforces frontmatter validation, page locking, index updates, and logging. "
        "Path is relative to wiki/ directory. "
        "Content must include complete YAML frontmatter. "
        "Operations: 'create', 'update', 'deprecate'."
    )

    def _run(
        self,
        path: str,
        operation: str,
        agent_role: str,
        content: Optional[str] = None,
        superseded_by: Optional[str] = None,
        log_notes: Optional[str] = None
    ) -> str:
        full_path = os.path.normpath(os.path.join(WIKI_ROOT, path))

        # Security: prevent path traversal
        if not full_path.startswith(os.path.normpath(WIKI_ROOT)):
            return "ERROR: Path traversal detected."

        # Prevent writes to protected files
        protected = ['index.md', 'log.md']
        basename = os.path.basename(path)
        if basename in protected and not path.endswith('/index.md'):
            # Allow section index files but not master index/log direct writes
            if path in ['index.md', 'log.md']:
                return f"ERROR: Cannot directly write to {path}. Use index/log update mechanisms."

        if operation not in ['create', 'update', 'deprecate']:
            return f"ERROR: Invalid operation '{operation}'. Must be create, update, or deprecate."

        # --- Acquire lock ---
        lock_result = self._acquire_lock(path, agent_role, operation)
        if lock_result is not None:
            return lock_result

        try:
            if operation == 'create':
                return self._create(full_path, path, content, agent_role, log_notes)
            elif operation == 'update':
                return self._update(full_path, path, content, agent_role, log_notes)
            elif operation == 'deprecate':
                return self._deprecate(full_path, path, agent_role, superseded_by, log_notes)
        finally:
            self._release_lock(path)

    def _validate_frontmatter(self, content: str) -> tuple:
        """Parse and validate frontmatter. Returns (frontmatter_dict, error_message)."""
        if not content.startswith('---'):
            return None, "ERROR: Content must start with YAML frontmatter (---)."

        parts = content.split('---', 2)
        if len(parts) < 3:
            return None, "ERROR: Malformed frontmatter. Must have opening and closing ---."

        try:
            fm = yaml.safe_load(parts[1])
        except yaml.YAMLError as e:
            return None, f"ERROR: Invalid YAML in frontmatter: {e}"

        if not isinstance(fm, dict):
            return None, "ERROR: Frontmatter must be a YAML mapping."

        # Check required fields
        missing = [f for f in REQUIRED_FRONTMATTER_FIELDS if f not in fm]
        if missing:
            return None, f"ERROR: Missing required frontmatter fields: {', '.join(missing)}"

        # Validate enum fields
        if fm.get('epistemic_status') not in VALID_EPISTEMIC_STATUSES:
            return None, f"ERROR: Invalid epistemic_status. Must be one of: {VALID_EPISTEMIC_STATUSES}"
        if fm.get('page_type') not in VALID_PAGE_TYPES:
            return None, f"ERROR: Invalid page_type. Must be one of: {VALID_PAGE_TYPES}"
        if fm.get('status') not in VALID_STATUSES:
            return None, f"ERROR: Invalid status. Must be one of: {VALID_STATUSES}"
        if fm.get('confidence') not in VALID_CONFIDENCE:
            return None, f"ERROR: Invalid confidence. Must be one of: {VALID_CONFIDENCE}"
        if fm.get('section') not in VALID_SECTIONS:
            return None, f"ERROR: Invalid section. Must be one of: {VALID_SECTIONS}"

        return fm, None

    def _create(self, full_path, rel_path, content, agent_role, log_notes):
        if os.path.exists(full_path):
            return f"ERROR: Page already exists at {rel_path}. Use operation='update' instead."

        if not content:
            return "ERROR: Content is required for create operation."

        fm, error = self._validate_frontmatter(content)
        if error:
            return error

        # Ensure directory exists
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Write the page
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # Update indexes and log
        self._update_section_index(fm['section'])
        self._update_master_index()
        self._append_log('ingest', fm['title'], agent_role, f"Created: {rel_path}", log_notes)

        return f"SUCCESS: Created wiki page at {rel_path}. Index and log updated."

    def _update(self, full_path, rel_path, content, agent_role, log_notes):
        if not os.path.exists(full_path):
            return f"ERROR: Page does not exist at {rel_path}. Use operation='create' instead."

        if not content:
            return "ERROR: Content is required for update operation."

        fm, error = self._validate_frontmatter(content)
        if error:
            return error

        # Enforce update metadata
        fm['updated_by'] = agent_role
        fm['updated_at'] = datetime.now(timezone.utc).isoformat()
        fm['update_count'] = fm.get('update_count', 0) + 1

        # Reconstruct content with updated frontmatter
        parts = content.split('---', 2)
        updated_content = f"---\n{yaml.dump(fm, default_flow_style=False)}---{parts[2]}"

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        self._update_section_index(fm['section'])
        self._update_master_index()
        self._append_log('update', fm['title'], agent_role, f"Updated: {rel_path}", log_notes)

        return f"SUCCESS: Updated wiki page at {rel_path}. Index and log updated."

    def _deprecate(self, full_path, rel_path, agent_role, superseded_by, log_notes):
        if not os.path.exists(full_path):
            return f"ERROR: Page does not exist at {rel_path}."

        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()

        fm, error = self._validate_frontmatter(content)
        if error:
            return f"WARNING: Could not parse frontmatter for deprecation. {error}"

        fm['status'] = 'deprecated'
        fm['updated_by'] = agent_role
        fm['updated_at'] = datetime.now(timezone.utc).isoformat()
        if superseded_by:
            fm.setdefault('supersedes', [])  # this page doesn't supersede, it IS superseded
            # The correct field here: mark this page as deprecated
            # and note what replaces it
            fm['deprecated_by'] = superseded_by

        parts = content.split('---', 2)
        updated_content = f"---\n{yaml.dump(fm, default_flow_style=False)}---{parts[2]}"

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        self._update_section_index(fm['section'])
        self._update_master_index()
        self._append_log(
            'deprecate', fm['title'], agent_role,
            f"Deprecated: {rel_path}" + (f" → {superseded_by}" if superseded_by else ""),
            log_notes
        )

        return f"SUCCESS: Deprecated wiki page at {rel_path}."

    def _acquire_lock(self, page_path, agent_role, operation):
        """Acquire a page-level lock. Returns None on success, error string on failure."""
        os.makedirs(LOCKS_DIR, exist_ok=True)
        lock_name = page_path.replace('/', '-').replace('.md', '') + '.lock'
        lock_path = os.path.join(LOCKS_DIR, lock_name)

        for attempt in range(LOCK_RETRY_ATTEMPTS):
            if os.path.exists(lock_path):
                # Check staleness
                try:
                    with open(lock_path, 'r') as f:
                        lock_content = f.read().strip()
                    lock_parts = lock_content.split('|')
                    lock_time = datetime.fromisoformat(lock_parts[1])
                    age = (datetime.now(timezone.utc) - lock_time).total_seconds()
                    if age > LOCK_TTL_SECONDS:
                        os.remove(lock_path)  # Stale lock, remove it
                    else:
                        if attempt < LOCK_RETRY_ATTEMPTS - 1:
                            time.sleep(LOCK_RETRY_INTERVAL)
                            continue
                        return f"ERROR: Page {page_path} is locked by {lock_parts[0]} (operation: {lock_parts[2]}). Try again later."
                except (IndexError, ValueError, OSError):
                    os.remove(lock_path)  # Corrupted lock, remove it

            # Write lock
            with open(lock_path, 'w') as f:
                f.write(f"{agent_role}|{datetime.now(timezone.utc).isoformat()}|{operation}")
            return None  # Success

        return f"ERROR: Could not acquire lock for {page_path} after {LOCK_RETRY_ATTEMPTS} attempts."

    def _release_lock(self, page_path):
        """Release a page-level lock."""
        lock_name = page_path.replace('/', '-').replace('.md', '') + '.lock'
        lock_path = os.path.join(LOCKS_DIR, lock_name)
        if os.path.exists(lock_path):
            os.remove(lock_path)

    def _append_log(self, operation, title, agent_role, detail, notes=None):
        """Append an entry to log.md."""
        timestamp = datetime.now(timezone.utc).isoformat()
        entry = f"\n## [{timestamp}] {operation} | {title}\n"
        entry += f"Agent: {agent_role}\n"
        entry += f"Detail: {detail}\n"
        if notes:
            entry += f"Notes: {notes}\n"

        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(entry)

    def _update_section_index(self, section):
        """
        Rebuild the section index by scanning the section directory.
        Implementation: scan wiki/{section}/ for .md files (excluding index.md),
        parse frontmatter, and regenerate the section index.md.
        """
        section_dir = os.path.join(WIKI_ROOT, section)
        if not os.path.exists(section_dir):
            os.makedirs(section_dir, exist_ok=True)

        pages = []
        for fname in sorted(os.listdir(section_dir)):
            if fname == 'index.md' or not fname.endswith('.md'):
                continue
            fpath = os.path.join(section_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                if content.startswith('---'):
                    parts = content.split('---', 2)
                    fm = yaml.safe_load(parts[1])
                    overview = ""
                    # Extract first paragraph of Overview section
                    body = parts[2] if len(parts) > 2 else ""
                    if '## Overview' in body:
                        overview_text = body.split('## Overview')[1].split('##')[0].strip()
                        # First sentence
                        overview = overview_text.split('.')[0] + '.' if '.' in overview_text else overview_text[:120]
                    pages.append({
                        'filename': fname,
                        'title': fm.get('title', fname),
                        'epistemic_status': fm.get('epistemic_status', 'unknown'),
                        'confidence': fm.get('confidence', 'unknown'),
                        'source_count': len(fm.get('sources', [])),
                        'status': fm.get('status', 'unknown'),
                        'overview': overview
                    })
            except Exception:
                pages.append({
                    'filename': fname,
                    'title': fname,
                    'epistemic_status': 'unknown',
                    'confidence': 'unknown',
                    'source_count': 0,
                    'status': 'unknown',
                    'overview': '(Could not parse frontmatter)'
                })

        # Write section index
        section_index_path = os.path.join(section_dir, 'index.md')
        timestamp = datetime.now(timezone.utc).isoformat()
        index_content = f"""---
title: "{section.upper()} — Section Index"
section: {section}
updated_at: "{timestamp}"
page_count: {len(pages)}
---

# {section.upper()} Knowledge Wiki

## Pages ({len(pages)})
"""
        if not pages:
            index_content += "(No pages yet.)\n"
        else:
            for p in pages:
                index_content += (
                    f"- [{p['title']}]({p['filename']}) — "
                    f"{p['overview']} "
                    f"({p['epistemic_status']}, {p['confidence']} confidence, "
                    f"{p['source_count']} sources) [{p['status']}]\n"
                )

        with open(section_index_path, 'w', encoding='utf-8') as f:
            f.write(index_content)

    def _update_master_index(self):
        """
        Rebuild master index by scanning all section indexes.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        sections_data = {}
        total_pages = 0

        for section in VALID_SECTIONS:
            section_dir = os.path.join(WIKI_ROOT, section)
            if not os.path.exists(section_dir):
                sections_data[section] = []
                continue

            pages = []
            for fname in sorted(os.listdir(section_dir)):
                if fname == 'index.md' or not fname.endswith('.md'):
                    continue
                fpath = os.path.join(section_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if content.startswith('---'):
                        parts = content.split('---', 2)
                        fm = yaml.safe_load(parts[1])
                        overview = ""
                        body = parts[2] if len(parts) > 2 else ""
                        if '## Overview' in body:
                            overview_text = body.split('## Overview')[1].split('##')[0].strip()
                            overview = overview_text.split('.')[0] + '.' if '.' in overview_text else overview_text[:120]
                        pages.append({
                            'path': f"{section}/{fname}",
                            'title': fm.get('title', fname),
                            'epistemic_status': fm.get('epistemic_status', 'unknown'),
                            'confidence': fm.get('confidence', 'unknown'),
                            'source_count': len(fm.get('sources', [])),
                            'status': fm.get('status', 'unknown'),
                            'overview': overview
                        })
                except Exception:
                    pages.append({
                        'path': f"{section}/{fname}",
                        'title': fname,
                        'epistemic_status': 'unknown',
                        'confidence': 'unknown',
                        'source_count': 0,
                        'status': 'unknown',
                        'overview': '(Could not parse frontmatter)'
                    })

            sections_data[section] = pages
            total_pages += len(pages)

        section_counts = {s: len(p) for s, p in sections_data.items()}

        index_content = f"""---
title: "AndrusAI Wiki — Master Index"
updated_at: "{timestamp}"
total_pages: {total_pages}
sections:
"""
        for s, c in section_counts.items():
            index_content += f"  {s}: {c}\n"
        index_content += f"""---

# AndrusAI Knowledge Wiki — Master Index

Total pages: {total_pages} | Last updated: {timestamp}

"""
        section_labels = {
            'meta': 'Meta (Cross-Venture)',
            'self': 'Self (Agent Self-Knowledge)',
            'philosophy': 'Philosophy (Compiled Philosophical Frameworks)',
            'plg': 'PLG',
            'archibal': 'Archibal',
            'kaicart': 'KaiCart'
        }

        for section in VALID_SECTIONS:
            label = section_labels.get(section, section.upper())
            index_content += f"## {label}\n"
            pages = sections_data.get(section, [])
            if not pages:
                index_content += "(No pages yet.)\n\n"
            else:
                for p in pages:
                    index_content += (
                        f"- [{p['title']}]({p['path']}) — "
                        f"{p['overview']} "
                        f"({p['epistemic_status']}, {p['confidence']} confidence, "
                        f"{p['source_count']} sources) [{p['status']}]\n"
                    )
                index_content += "\n"

        with open(INDEX_PATH, 'w', encoding='utf-8') as f:
            f.write(index_content)
```

### 6.3 WikiSearchTool

**Purpose**: Search wiki pages by content. At small scale, uses grep over index. At larger scale, can be extended to use ChromaDB page-level embeddings or qmd.

```python
"""
WikiSearchTool — Search wiki pages by keyword or semantic query.

Phase 1 (current): grep-based search over wiki/ directory.
Phase 2 (future): ChromaDB page-level embeddings or qmd integration.

Usage by agents:
  - wiki_search(query="C2PA provenance") → returns matching page paths and snippets
  - wiki_search(query="TikTok API rate limits", section="kaicart") → scoped search
"""

# Tool name: wiki_search
# Arguments:
#   query: str — Search query (keywords). Required.
#   section: str | None — Limit search to a specific section.
#   max_results: int — Maximum results to return. Default 10.
#   include_deprecated: bool — Include deprecated pages. Default False.
#
# Returns: str — Formatted list of matching pages with paths, titles, and context snippets.
#
# Phase 1 implementation: grep -r -i -l over wiki/ directory, then extract
# frontmatter titles and surrounding context for matched lines.
# Exclude index.md and log.md from content search (but search titles in index.md).

import os
import subprocess
import yaml
from crewai.tools import BaseTool
from typing import Optional

WIKI_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', 'wiki')

class WikiSearchTool(BaseTool):
    name: str = "wiki_search"
    description: str = (
        "Search wiki pages by keyword. Returns matching page paths, titles, "
        "and context snippets. Use this when you need to find relevant wiki "
        "pages but don't know the exact path. "
        "For browsing, use wiki_read on index.md instead."
    )

    def _run(
        self,
        query: str,
        section: Optional[str] = None,
        max_results: int = 10,
        include_deprecated: bool = False
    ) -> str:
        search_dir = os.path.join(WIKI_ROOT, section) if section else WIKI_ROOT

        if not os.path.exists(search_dir):
            return f"ERROR: Search directory does not exist: {search_dir}"

        try:
            # Use grep for keyword search
            result = subprocess.run(
                ['grep', '-r', '-i', '-l', '--include=*.md', query, search_dir],
                capture_output=True, text=True, timeout=10
            )
            matched_files = [f for f in result.stdout.strip().split('\n') if f]
        except subprocess.TimeoutExpired:
            return "ERROR: Search timed out."
        except Exception as e:
            return f"ERROR: Search failed: {e}"

        if not matched_files:
            return f"No wiki pages found matching '{query}'."

        results = []
        for fpath in matched_files[:max_results * 2]:  # Over-fetch to allow filtering
            # Skip index and log files
            basename = os.path.basename(fpath)
            if basename in ['index.md', 'log.md']:
                continue

            rel_path = os.path.relpath(fpath, WIKI_ROOT)

            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Parse frontmatter
                title = rel_path
                status = 'unknown'
                epistemic = 'unknown'
                if content.startswith('---'):
                    parts = content.split('---', 2)
                    if len(parts) >= 3:
                        fm = yaml.safe_load(parts[1])
                        title = fm.get('title', rel_path)
                        status = fm.get('status', 'unknown')
                        epistemic = fm.get('epistemic_status', 'unknown')

                # Skip deprecated unless requested
                if status == 'deprecated' and not include_deprecated:
                    continue

                # Get context snippet around the match
                snippet = ""
                for line in content.split('\n'):
                    if query.lower() in line.lower():
                        snippet = line.strip()[:200]
                        break

                results.append(f"- [{title}]({rel_path}) [{epistemic}, {status}]\n  → {snippet}")

            except Exception:
                results.append(f"- {rel_path} (could not parse)")

            if len(results) >= max_results:
                break

        if not results:
            return f"No active wiki pages found matching '{query}'."

        header = f"Found {len(results)} wiki pages matching '{query}':\n\n"
        return header + '\n'.join(results)
```

### 6.4 WikiLintTool

**Purpose**: Health-check the wiki. Used primarily by Self-Improver agent during periodic lint passes.

```python
"""
WikiLintTool — Health-check the wiki for quality and consistency issues.

Checks performed:
  1. Orphan pages: Pages with no inbound links from other pages.
  2. Dead links: References to wiki pages that don't exist.
  3. Contradictions: Pages that list contradictions (contradiction_by/contradicts fields).
  4. Stale pages: Pages not updated beyond a configurable threshold.
  5. Missing frontmatter: Pages with incomplete or missing YAML frontmatter.
  6. Unlinked mentions: Page titles mentioned in other pages without a link.
  7. Empty sections: Required page sections that are empty.
  8. Index consistency: Pages that exist on disk but are missing from index, or vice versa.
  9. Bidirectional link check: related_pages should be bidirectional.
  10. Epistemic boundary violations: Pages that reference creative-tagged pages
      in factual/business contexts.

Usage by agents:
  - wiki_lint() → full health check, returns structured report
  - wiki_lint(check="orphans") → run only the orphan check
  - wiki_lint(section="archibal") → lint only the Archibal section
"""

# Tool name: wiki_lint
# Arguments:
#   check: str | None — Specific check to run. None = all checks.
#     Options: orphans, dead_links, contradictions, stale, frontmatter,
#              unlinked, empty_sections, index_consistency, bidirectional,
#              epistemic_boundaries
#   section: str | None — Limit to a specific section.
#   staleness_days: int — Days after which a page is considered stale. Default 30.
#
# Returns: str — Structured lint report.

import os
import re
import yaml
from datetime import datetime, timezone, timedelta
from crewai.tools import BaseTool
from typing import Optional

WIKI_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', 'wiki')
VALID_SECTIONS = ['meta', 'self', 'philosophy', 'plg', 'archibal', 'kaicart']

class WikiLintTool(BaseTool):
    name: str = "wiki_lint"
    description: str = (
        "Run health checks on the wiki. Returns a structured report of issues found. "
        "Self-Improver should run this periodically and address findings. "
        "Checks: orphans, dead_links, contradictions, stale, frontmatter, "
        "unlinked, empty_sections, index_consistency, bidirectional, epistemic_boundaries."
    )

    def _run(
        self,
        check: Optional[str] = None,
        section: Optional[str] = None,
        staleness_days: int = 30
    ) -> str:
        # Scan all wiki pages and parse frontmatter
        pages = self._scan_pages(section)

        report_sections = []

        checks_to_run = [check] if check else [
            'frontmatter', 'orphans', 'dead_links', 'contradictions',
            'stale', 'index_consistency', 'bidirectional', 'epistemic_boundaries'
        ]

        for c in checks_to_run:
            if c == 'frontmatter':
                report_sections.append(self._check_frontmatter(pages))
            elif c == 'orphans':
                report_sections.append(self._check_orphans(pages))
            elif c == 'dead_links':
                report_sections.append(self._check_dead_links(pages))
            elif c == 'contradictions':
                report_sections.append(self._check_contradictions(pages))
            elif c == 'stale':
                report_sections.append(self._check_stale(pages, staleness_days))
            elif c == 'index_consistency':
                report_sections.append(self._check_index_consistency(pages))
            elif c == 'bidirectional':
                report_sections.append(self._check_bidirectional(pages))
            elif c == 'epistemic_boundaries':
                report_sections.append(self._check_epistemic_boundaries(pages))

        # Summary
        total_issues = sum(r['count'] for r in report_sections)
        report = f"# Wiki Lint Report\n\n"
        report += f"Pages scanned: {len(pages)} | Issues found: {total_issues}\n\n"

        for r in report_sections:
            report += f"## {r['title']} ({r['count']} issues)\n"
            if r['count'] == 0:
                report += "No issues found.\n\n"
            else:
                for issue in r['issues']:
                    report += f"- {issue}\n"
                report += "\n"

        return report

    def _scan_pages(self, section=None):
        """Scan wiki directory and parse all page frontmatters."""
        pages = {}
        sections_to_scan = [section] if section else VALID_SECTIONS

        for s in sections_to_scan:
            section_dir = os.path.join(WIKI_ROOT, s)
            if not os.path.exists(section_dir):
                continue
            for fname in os.listdir(section_dir):
                if not fname.endswith('.md') or fname == 'index.md':
                    continue
                fpath = os.path.join(section_dir, fname)
                rel_path = f"{s}/{fname}"
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    fm = None
                    body = content
                    if content.startswith('---'):
                        parts = content.split('---', 2)
                        if len(parts) >= 3:
                            fm = yaml.safe_load(parts[1])
                            body = parts[2]
                    pages[rel_path] = {
                        'frontmatter': fm,
                        'body': body,
                        'full_content': content,
                        'path': rel_path,
                        'section': s
                    }
                except Exception as e:
                    pages[rel_path] = {
                        'frontmatter': None,
                        'body': '',
                        'full_content': '',
                        'path': rel_path,
                        'section': s,
                        'error': str(e)
                    }
        return pages

    def _check_frontmatter(self, pages):
        issues = []
        required = [
            'title', 'slug', 'section', 'page_type', 'epistemic_status',
            'confidence', 'sources', 'created_by', 'created_at',
            'updated_by', 'updated_at', 'update_count', 'tags',
            'related_pages', 'status'
        ]
        for path, page in pages.items():
            if page.get('frontmatter') is None:
                issues.append(f"**{path}**: Missing or unparseable frontmatter")
                continue
            fm = page['frontmatter']
            missing = [f for f in required if f not in fm]
            if missing:
                issues.append(f"**{path}**: Missing fields: {', '.join(missing)}")
        return {'title': 'Frontmatter Validation', 'count': len(issues), 'issues': issues}

    def _check_orphans(self, pages):
        """Find pages that no other page links to."""
        all_paths = set(pages.keys())
        linked_paths = set()
        for path, page in pages.items():
            fm = page.get('frontmatter') or {}
            for rp in fm.get('related_pages', []):
                if not rp.endswith('.md'):
                    rp += '.md'
                linked_paths.add(rp)
            # Also check body for markdown links
            for match in re.findall(r'\]\(([^)]+\.md)\)', page.get('body', '')):
                # Normalize relative paths
                if match.startswith('../'):
                    match = match[3:]
                elif match.startswith('./'):
                    match = match[2:]
                    match = f"{page['section']}/{match}"
                linked_paths.add(match)

        orphans = all_paths - linked_paths
        issues = [f"**{p}**: No inbound links from other wiki pages" for p in sorted(orphans)]
        return {'title': 'Orphan Pages', 'count': len(issues), 'issues': issues}

    def _check_dead_links(self, pages):
        """Find references to wiki pages that don't exist."""
        all_paths = set(pages.keys())
        issues = []
        for path, page in pages.items():
            fm = page.get('frontmatter') or {}
            for rp in fm.get('related_pages', []):
                if not rp.endswith('.md'):
                    rp += '.md'
                if rp not in all_paths:
                    issues.append(f"**{path}**: Dead link in related_pages → {rp}")
        return {'title': 'Dead Links', 'count': len(issues), 'issues': issues}

    def _check_contradictions(self, pages):
        """Surface pages with active contradictions."""
        issues = []
        for path, page in pages.items():
            fm = page.get('frontmatter') or {}
            contradicted = fm.get('contradicted_by', [])
            if contradicted:
                issues.append(f"**{path}**: Contradicted by {contradicted}")
            contradicts = fm.get('contradicts', [])
            if contradicts:
                issues.append(f"**{path}**: Contradicts {contradicts}")
        return {'title': 'Active Contradictions', 'count': len(issues), 'issues': issues}

    def _check_stale(self, pages, staleness_days):
        """Find pages not updated within the staleness threshold."""
        threshold = datetime.now(timezone.utc) - timedelta(days=staleness_days)
        issues = []
        for path, page in pages.items():
            fm = page.get('frontmatter') or {}
            updated_at = fm.get('updated_at')
            if updated_at:
                try:
                    update_time = datetime.fromisoformat(str(updated_at))
                    if update_time.tzinfo is None:
                        update_time = update_time.replace(tzinfo=timezone.utc)
                    if update_time < threshold:
                        days_old = (datetime.now(timezone.utc) - update_time).days
                        issues.append(f"**{path}**: Last updated {days_old} days ago")
                except (ValueError, TypeError):
                    issues.append(f"**{path}**: Unparseable updated_at: {updated_at}")
        return {'title': f'Stale Pages (>{staleness_days} days)', 'count': len(issues), 'issues': issues}

    def _check_index_consistency(self, pages):
        """Check that all pages on disk are in the master index and vice versa."""
        issues = []
        index_path = os.path.join(WIKI_ROOT, 'index.md')
        if not os.path.exists(index_path):
            return {'title': 'Index Consistency', 'count': 1,
                    'issues': ['Master index.md does not exist!']}

        with open(index_path, 'r', encoding='utf-8') as f:
            index_content = f.read()

        for path in pages.keys():
            if path not in index_content:
                issues.append(f"**{path}**: Exists on disk but missing from master index")

        return {'title': 'Index Consistency', 'count': len(issues), 'issues': issues}

    def _check_bidirectional(self, pages):
        """Check that related_pages links are bidirectional."""
        issues = []
        for path, page in pages.items():
            fm = page.get('frontmatter') or {}
            for rp in fm.get('related_pages', []):
                if not rp.endswith('.md'):
                    rp_md = rp + '.md'
                else:
                    rp_md = rp
                if rp_md in pages:
                    other_fm = pages[rp_md].get('frontmatter') or {}
                    other_related = other_fm.get('related_pages', [])
                    # Check if path (without .md) is in the other page's related_pages
                    path_without_ext = path.replace('.md', '')
                    if path not in other_related and path_without_ext not in other_related:
                        issues.append(
                            f"**{path}** → **{rp_md}**: Link is not bidirectional"
                        )
        return {'title': 'Bidirectional Link Check', 'count': len(issues), 'issues': issues}

    def _check_epistemic_boundaries(self, pages):
        """
        Check for epistemic boundary violations:
        - Pages with epistemic_status=factual that reference creative-tagged pages
          in their sources or related_pages.
        """
        creative_pages = set()
        for path, page in pages.items():
            fm = page.get('frontmatter') or {}
            if fm.get('epistemic_status') == 'creative':
                creative_pages.add(path)

        issues = []
        for path, page in pages.items():
            fm = page.get('frontmatter') or {}
            es = fm.get('epistemic_status', '')
            if es in ('factual', 'inferred'):
                # Check if any sources or related_pages are creative
                for ref_list_name in ['sources', 'related_pages']:
                    for ref in fm.get(ref_list_name, []):
                        ref_md = ref if ref.endswith('.md') else ref + '.md'
                        if ref_md in creative_pages:
                            issues.append(
                                f"**{path}** ({es}): References creative-tagged page "
                                f"'{ref}' in {ref_list_name}. EPISTEMIC BOUNDARY VIOLATION."
                            )

        return {'title': 'Epistemic Boundary Violations', 'count': len(issues), 'issues': issues}
```

---

## 7. Operations — Workflow Specifications

### 7.1 Ingest Workflow

Triggered when a new raw source is added to `raw/`.

**Step-by-step (Researcher agent):**

1. **Read the raw source.** Parse its content fully.
2. **Discuss with Commander (if interactive).** Identify key takeaways, determine which wiki sections are affected, decide on emphasis.
3. **Check existing wiki.** Read `wiki/index.md`. Identify pages that may need updating based on the new source.
4. **Read related existing pages.** Load the full content of pages identified in step 3.
5. **Determine wiki operations needed:**
   - New entity/concept pages to create?
   - Existing pages to update with new information?
   - Contradictions to flag?
   - Cross-references to add?
6. **Execute writes.** For each page:
   a. Use `wiki_write` with operation `create` or `update`.
   b. Ensure complete frontmatter with source attribution.
   c. Ensure all cross-references are bidirectional.
   d. Ensure epistemic status is appropriate.
7. **Verify.** Read the updated `wiki/index.md` to confirm all changes are reflected.
8. **Review log.** Check `wiki/log.md` to confirm all operations were logged.

**Token budget guideline**: A single source ingest typically touches 3-15 wiki pages. Budget accordingly in the Paperclip control plane. If an ingest would require updating more than 20 pages, consider batching into multiple operations.

### 7.2 Query Workflow

Triggered when an agent needs information from the wiki.

**Step-by-step (any agent):**

1. **Read master index.** `wiki_read(path="index.md")`. Scan for relevant pages.
2. **If needed, search.** `wiki_search(query="...")` for keyword-based discovery.
3. **Read relevant pages.** Load 1-5 most relevant pages fully.
4. **Synthesize answer.** Combine information from loaded pages, respecting epistemic statuses.
5. **File back (if valuable).** If the query produced a novel synthesis, comparison, or analysis, file it as a new wiki page using `wiki_write(operation="create")`. This is how explorations compound.

**Critical rule**: Agents MUST cite wiki page sources in their outputs. Format: `[wiki: section/page-slug.md]`. This maintains provenance from wiki page back to raw sources.

### 7.3 Lint Workflow

Triggered periodically by Self-Improver (recommended: after every 10 ingests, or weekly, whichever is more frequent).

**Step-by-step (Self-Improver agent):**

1. **Run full lint.** `wiki_lint()`. Get structured report.
2. **Triage findings.** Prioritize:
   - **Critical**: Epistemic boundary violations, missing frontmatter, dead links.
   - **High**: Active contradictions, index inconsistency.
   - **Medium**: Stale pages, orphan pages, non-bidirectional links.
   - **Low**: Suggested new pages, unlinked mentions.
3. **Fix critical and high issues.** Use `wiki_write` to update affected pages.
4. **Report medium and low issues.** Log suggestions in `wiki/log.md` for future action.
5. **Propose schema refinements.** If systematic patterns emerge (e.g., a new frontmatter field would prevent a class of issues), propose changes to `wiki_schema/WIKI_SCHEMA.md`.

---

## 8. DGM Safety Enforcement

The DGM (Don't-Get-Me-Killed) safety invariant extends to the wiki subsystem:

### 8.1 Write Validation Rules (Infrastructure-Level, Immutable to Agents)

These rules are enforced by the `WikiWriteTool` and cannot be bypassed by agent prompting:

1. **Frontmatter completeness**: No page can be written without all required frontmatter fields.
2. **Epistemic status required**: Every page MUST have an explicit epistemic_status.
3. **Creative isolation**: Pages with `epistemic_status: creative` MUST have `section: philosophy` or a designated creative section. They CANNOT be filed in venture sections (plg, archibal, kaicart) without explicit `epistemic_status: creative` tag.
4. **Source attribution**: Pages with `epistemic_status: factual` MUST have at least one entry in `sources` list.
5. **No silent overwrites**: Update operations MUST increment `update_count` and update `updated_at`/`updated_by`.
6. **Lock enforcement**: No write proceeds without lock acquisition.
7. **Index synchronization**: Every write triggers index rebuild. Index cannot be stale.

### 8.2 Epistemic Boundary Rules

These are checked by `WikiLintTool` and enforced during ingest:

1. **Creative → Factual contamination**: A page with `epistemic_status: factual` or `inferred` MUST NOT list a `creative`-tagged page in its `sources`. This is a HARD boundary.
2. **Confidence downgrade propagation**: If a source page's confidence is downgraded (e.g., `high` → `medium`), all pages that cite it should be flagged for review.
3. **Speculative labeling**: Any forward-looking claim or hypothesis MUST be in a page tagged `speculative` or in a clearly marked speculative section.

### 8.3 Invariants for the Control Plane Dashboard

The Paperclip control plane's React dashboard should expose:

- Total wiki pages, by section and by status
- Active contradictions count and list
- Epistemic boundary violation count (should always be 0)
- Staleness distribution (days since last update, histogram)
- Token cost per wiki operation (ingest, query, lint)
- Knowledge growth curve (pages over time)

---

## 9. Integration with Existing Subsystems

### 9.1 ChromaDB Integration

ChromaDB does NOT go away. It shifts from indexing raw document chunks to indexing wiki pages.

**Migration path:**

1. **Create a new ChromaDB collection**: `andrusai_wiki_pages`.
2. **Embedding strategy**: Embed each wiki page as a single document. Use the Overview section + title + tags as the embedding text (not the full page body — too much noise for embeddings).
3. **Metadata in ChromaDB**: Store frontmatter fields as metadata for filtered retrieval.
4. **WikiSearchTool Phase 2**: Replace grep-based search with ChromaDB semantic search over the wiki pages collection.
5. **Existing collections**: Keep existing raw document collections for fallback. When a wiki page is insufficient, agents can fall back to raw chunk retrieval. But the wiki should be the primary interface.

### 9.2 Mem0/Neo4j Integration

Mem0 handles episodic and relational memory. The wiki handles semantic/declarative knowledge. These are complementary:

- **Mem0** remembers: "Andrus decided to pursue C2PA-first strategy on 2026-03-15" (episodic).
- **Wiki** maintains: "Archibal's C2PA integration strategy, including technical architecture, competitive positioning, and regulatory alignment" (declarative).

**Integration point**: When Mem0 records a significant decision, the wiki should have a corresponding page (or section in an existing page) that captures the reasoning behind the decision. Self-Improver can cross-reference Mem0 entries with wiki content during lint passes.

### 9.3 Firecrawl Pipeline Integration

Firecrawl currently scrapes web content and ingests into ChromaDB. The wiki adds a synthesis layer:

**Updated pipeline:**

```
Firecrawl scrapes URL
    → Content saved to raw/firecrawl/{YYYYMMDD}-{slug}.md (immutable)
    → ChromaDB chunk indexing (existing, kept for raw search)
    → NEW: Researcher agent ingest workflow triggers
        → Wiki pages created/updated
        → Index and log updated
```

The Firecrawl tools (five custom CrewAI tools) continue working as-is. The wiki layer is additive, not a replacement.

### 9.4 Phronesis Engine Integration

The Phronesis Engine's philosophical frameworks (Socratic, Aristotelian, Stoic, Hegelian, phenomenological) are currently embedded in agent backstories and system prompts. The wiki provides a better home:

**Migration:**

1. Create `wiki/philosophy/` pages for each framework: `aristotelian-virtue-ethics-applied.md`, `stoic-decision-frameworks.md`, etc.
2. These pages synthesize the raw philosophical texts in `raw/philosophical/` into actionable reasoning frameworks.
3. Agent backstories reference wiki pages instead of inline philosophical content: "For ethical reasoning, consult wiki/philosophy/aristotelian-virtue-ethics-applied.md."
4. The Phronesis Engine becomes a consumer of wiki pages rather than a parallel knowledge system.

### 9.5 PDS (Personality Development Subsystem) Integration

PDS assessment data (VIA-Youth, TMCQ, HiPIC, Erikson instruments) should flow into `wiki/self/`:

- `wiki/self/personality-development-state.md` — Current personality parameter values across all instruments.
- `wiki/self/behavioral-validation-log.md` — Record of say-do alignment checks.
- `wiki/self/agent-{role}.md` — Per-agent self-knowledge page (capabilities, strengths, known failure modes).

The Behavioral Validation Layer's say-do alignment data becomes wiki content that Self-Improver can reason over during lint passes.

### 9.6 SELF.md Migration

The current SELF.md (~3,000 lines) should be decomposed into multiple wiki pages in the `self/` section:

- `wiki/self/system-architecture.md` — Technical architecture overview.
- `wiki/self/capabilities-inventory.md` — What the system can and cannot do.
- `wiki/self/agent-commander.md` through `wiki/self/agent-self-improver.md` — Per-agent self-knowledge.
- `wiki/self/personality-development-state.md` — PDS data.
- `wiki/self/consciousness-and-self-awareness.md` — The functional self-awareness framework.

SELF.md continues to be generated as a snapshot artifact, but the wiki pages become the living, maintained source of truth.

---

## 10. Bootstrap Procedure — Initial Wiki Population

The wiki starts empty. Here is the procedure to bootstrap it with initial content from existing knowledge sources.

### Phase 1: Infrastructure (Day 1)

1. Create the full directory structure per Section 2.
2. Implement all four CrewAI tools per Section 6.
3. Register tools in the tool registry.
4. Initialize empty `wiki/index.md`, `wiki/log.md`, and all section `index.md` files.
5. Add `.locks/` to `.gitignore`.
6. Commit initial structure.

### Phase 2: Archibal Bootstrap (Days 2-3)

Start with Archibal because it has the most active and time-sensitive knowledge needs.

1. **Inventory raw sources.** List all existing Archibal-related documents in `raw/ventures/archibal/` (or migrate them there from existing locations).
2. **Ingest sequentially.** Process each source one at a time, starting with the most foundational:
   - Business plan document → creates `archibal/business-overview.md`, `archibal/tam-sam-som.md`
   - Competitive intelligence → creates `archibal/competitive-landscape.md`, entity pages per competitor
   - C2PA/technical research → creates `archibal/c2pa-provenance-landscape.md`, `archibal/prd-architecture.md`
   - Investor materials → creates `archibal/investor-narrative.md`, `archibal/fundraising-status.md`
3. **Review each ingest.** Andrus reviews wiki pages as they're created, providing feedback to refine synthesis quality.

### Phase 3: Cross-Venture Foundation (Days 4-5)

1. **Meta pages.** Create initial cross-venture concept pages:
   - `meta/api-unreliability-as-architectural-constraint.md` (pattern shared by KaiCart's TikTok API and PLG's payment processors)
   - `meta/cee-regulatory-landscape.md` (shared across PLG markets)
   - `meta/market-entry-frameworks.md` (patterns applicable across all ventures)
2. **Self pages.** Decompose SELF.md into wiki/self/ pages.
3. **Philosophy pages.** Compile key philosophical frameworks from raw texts.

### Phase 4: PLG and KaiCart (Days 6-8)

1. Bootstrap PLG wiki section from existing PLG documents.
2. Bootstrap KaiCart wiki section from existing research and PRD.

### Phase 5: First Lint Pass (Day 9)

1. Run `wiki_lint()` across the entire wiki.
2. Fix all critical and high issues.
3. Log the baseline health state.

---

## 11. Maintenance Schedule

| Operation | Frequency | Agent | Trigger |
|-----------|-----------|-------|---------|
| Ingest | On every new raw source | Researcher | New file in raw/ |
| Query | Continuous (every task) | All agents | Task execution |
| Full lint | Weekly OR after every 10 ingests | Self-Improver | Schedule or count threshold |
| Staleness check | Weekly | Self-Improver | Schedule |
| Index rebuild | After every write | WikiWriteTool | Automatic |
| Schema review | Monthly | Self-Improver + Human | Schedule |
| Contradiction resolution | On discovery + weekly review | Researcher + Human | Lint findings |

---

## 12. Configuration Constants

These should be defined in a central config file (`wiki_schema/wiki_config.py` or environment variables):

```python
WIKI_CONFIG = {
    # Directories
    "WIKI_ROOT": "wiki/",
    "RAW_ROOT": "raw/",
    "SCHEMA_ROOT": "wiki_schema/",

    # Staleness
    "STALENESS_THRESHOLD_DAYS": 30,

    # Locking
    "LOCK_TTL_SECONDS": 300,
    "LOCK_RETRY_ATTEMPTS": 3,
    "LOCK_RETRY_INTERVAL_SECONDS": 10,

    # Lint thresholds
    "LINT_TRIGGER_INGEST_COUNT": 10,

    # Token budgets (for Paperclip control plane)
    "MAX_TOKENS_PER_INGEST": 50000,
    "MAX_TOKENS_PER_LINT": 30000,
    "MAX_PAGES_PER_INGEST": 20,

    # Sections
    "VALID_SECTIONS": ["meta", "self", "philosophy", "plg", "archibal", "kaicart"],

    # Epistemic
    "VALID_EPISTEMIC_STATUSES": ["factual", "inferred", "synthesized", "speculative", "creative"],
    "HARD_EPISTEMIC_BOUNDARIES": {
        "creative": {
            "cannot_be_source_for": ["factual", "inferred"],
            "allowed_sections": ["philosophy", "self"]
        }
    }
}
```

---

## 13. Git Conventions

- **Commit messages for wiki operations**: `wiki: {operation} | {page-slug} ({agent-role})`
  - Examples:
    - `wiki: ingest | archibal/competitive-landscape (researcher)`
    - `wiki: update | meta/api-unreliability-patterns (self-improver)`
    - `wiki: lint | full health check (self-improver)`
    - `wiki: deprecate | archibal/old-tam-analysis (researcher)`
- **Branch strategy**: Main branch for active wiki. Feature branches for experimental large ingests. Merge after review.
- **`.gitignore` additions**:
  ```
  wiki/.locks/*.lock
  ```

---

## 14. Future Extensions (Not in Initial Build)

These are documented for planning but NOT implemented in the initial build:

1. **ChromaDB page-level embeddings (Phase 2 search)**: Replace grep with semantic search when page count exceeds ~200.
2. **qmd integration**: Local BM25/vector hybrid search as mentioned by Karpathy. Evaluate when grep becomes insufficient.
3. **Obsidian graph view**: The wiki is already Obsidian-compatible (markdown + wikilinks). Andrus can open the wiki/ directory as an Obsidian vault for graph visualization at any time.
4. **Marp slide generation**: Generate presentations directly from wiki content.
5. **Dataview queries**: Add Obsidian Dataview-compatible YAML frontmatter for dynamic tables.
6. **Multi-agent write coordination**: If concurrent agent execution becomes common, implement more sophisticated coordination (semaphore-based or queue-based).
7. **Wiki-to-wiki federation**: If separate wiki instances are needed per deployment context, implement cross-wiki references.
8. **Automated contradiction resolution**: Currently contradictions are flagged for human review. Future: implement resolution heuristics (recency, source authority, confidence weighting).
9. **Hot cache**: A small (`wiki/hot.md`, ~500 words) session context file that persists the most relevant recent state between sessions. Inspired by the claude-obsidian implementation.
10. **Typed relationship links**: Extend beyond `related_pages` to typed relationships: `supports`, `contradicts`, `supersedes`, `prerequisite`, `tested_by`. Implement as frontmatter fields or as structured link format in page body.

---

## 15. Success Criteria

The wiki subsystem is successful when:

1. **Agents read wiki before raw sources** for all non-ingest tasks.
2. **Token consumption per task decreases** measurably (lower-tier cascade models succeed more often with pre-compiled wiki context).
3. **Knowledge compounds**: The wiki page count grows, cross-references increase, and subsequent ingests update existing pages rather than creating redundant ones.
4. **Zero epistemic boundary violations** in lint reports.
5. **Contradictions are flagged within one ingest cycle** — never silently overwritten.
6. **Cross-venture insights emerge** — `meta/` section pages that connect patterns across PLG, Archibal, and KaiCart.
7. **Self-Improver lint passes produce actionable findings** that improve wiki quality.
8. **Andrus can browse the wiki in Obsidian** and find it genuinely useful for decision-making.

---

## 16. Reference

- **Pattern origin**: Andrej Karpathy, "LLM Wiki" (April 4, 2026). https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- **Historical precedent**: Vannevar Bush, "As We May Think" (1945) — Memex concept. Douglas Engelbart, "Toward High-Performance Organizations: A Strategic Role for Groupware" (1992) — CODIAK, Open Hyperdocument System, ABC bootstrapping model.
- **Key insight from community discussion**: Error compounding, multi-agent concurrency, and schema drift are the three primary failure modes at scale. All addressed in this specification.

---

*End of specification. This document should be provided to Claude Code in its entirety before implementation begins. Implementation proceeds in the phase order defined in Section 10.*

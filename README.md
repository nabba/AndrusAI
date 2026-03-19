# CrewAI Agent Team

An autonomous, self-improving AI agent team built on [CrewAI](https://www.crewai.com/). Control it from your iPhone via Signal. Monitor it in real time from a Firebase-hosted dashboard.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
  - [System Diagram](#system-diagram)
  - [Agent Roles](#agent-roles)
  - [Crew System](#crew-system)
  - [Tool Inventory](#tool-inventory)
- [Self-Aware AI System](#self-aware-ai-system)
  - [Phase 1: Functional Self-Awareness](#phase-1-functional-self-awareness)
  - [Phase 2: Shared Memory & Cooperation](#phase-2-shared-memory--cooperation)
  - [Phase 3: Proactive Cooperation](#phase-3-proactive-cooperation)
  - [Phase 4: Meta-Cognitive Self-Improvement](#phase-4-meta-cognitive-self-improvement)
- [SOUL.md Personality Framework](#soulmd-personality-framework)
  - [Soul File Architecture](#soul-file-architecture)
  - [Backstory Composition](#backstory-composition)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Signal Setup](#signal-setup)
  - [Tailscale Setup](#tailscale-setup)
  - [Firebase Dashboard Setup](#firebase-dashboard-setup)
- [Usage](#usage)
  - [Signal Commands Reference](#signal-commands-reference)
  - [Self-Improvement System](#self-improvement-system)
  - [Evolution Loop](#evolution-loop)
  - [Self-Healing System](#self-healing-system)
  - [Improvement Proposals](#improvement-proposals)
  - [File Attachments](#file-attachments)
- [Docker Architecture](#docker-architecture)
  - [Services](#services)
  - [Sandbox Security](#sandbox-security)
  - [Network Isolation](#network-isolation)
- [Security](#security)
- [Memory & Persistence](#memory--persistence)
  - [ChromaDB Vector Memory](#chromadb-vector-memory)
  - [Scoped Memory Hierarchy](#scoped-memory-hierarchy)
  - [Conversation History](#conversation-history)
  - [Skill Files](#skill-files)
  - [Cloud Backup](#cloud-backup)
- [Monitoring Dashboard](#monitoring-dashboard)
- [Testing](#testing)
- [Cost Estimate](#cost-estimate)
- [Development](#development)
  - [Key Source Files](#key-source-files)
  - [Adding a New Tool](#adding-a-new-tool)
  - [Adding a New Crew](#adding-a-new-crew)

---

## Overview

This project implements an autonomous AI agent team that you interact with entirely through Signal messages from your phone. A **Commander** agent receives your requests, classifies them, and dispatches specialist **crews** (Research, Coding, Writing) to handle them -- optionally in parallel. The system continuously improves itself by learning new topics, diagnosing its own errors, and running an evolution loop inspired by [Karpathy's autoresearch](https://github.com/karpathy/autoresearch).

The system features a **self-aware AI architecture** built on current research (Li et al. 2025, ProAgent AAAI 2024, Park et al. 2023) with four capability layers: functional self-awareness, shared memory with belief tracking, proactive cooperation, and a meta-cognitive self-improvement loop. Agent personalities are defined through a **SOUL.md framework** following the SoulSpec standard -- each agent has a distinct identity, values, and communication style.

Key capabilities:

- **Natural language task dispatch** -- send any request via Signal; the Commander routes it to the right crew
- **Web research** -- search the web, read articles, extract YouTube transcripts
- **Code execution** -- write, test, and debug code in a sandboxed Docker container
- **Content creation** -- summaries, reports, documentation, emails
- **Self-awareness** -- agents assess their own confidence, report blockers, and store post-task reflections
- **Shared belief states** -- agents track teammates' progress, needs, and current tasks
- **Proactive cooperation** -- automatic detection of low confidence, unfulfilled needs, and quality drift
- **Adversarial quality review** -- Critic agent challenges research outputs for accuracy and completeness
- **Meta-cognitive policies** -- Retrospective crew generates improvement policies from execution traces
- **Automated benchmarking** -- tracks completion time, quality scores, and improvement trends over time
- **Self-improvement** -- learns new topics on a schedule, saves skill files that improve future responses
- **Self-healing** -- automatically diagnoses errors and creates fixes
- **Autonomous evolution** -- experiments on itself, keeps improvements, discards regressions
- **SOUL.md personalities** -- each agent has a distinct identity, personality, and expertise defined in markdown
- **File attachment support** -- send PDFs, DOCX, XLSX, images via Signal for analysis
- **Real-time monitoring** -- Firebase-hosted dashboard with live crew status, task tracking, and activity feed
- **Persistent memory** -- ChromaDB vector store with hierarchical scoped memory across all agents
- **Conversation history** -- remembers recent exchanges for contextual follow-up messages
- **Cloud backup** -- optional git-based workspace sync to a remote repository

---

## Architecture

### System Diagram

```
iPhone (Signal) --> signal-cli daemon --> Forwarder --> FastAPI Gateway (127.0.0.1:8765)
                                                              |
                                                        Commander (Router + Soul)
                                                        |     |     |
                                                   Research  Coding  Writing
                                                     Crew    Crew    Crew
                                                      |
                                                    Critic Review
                                                      |
                                                   Proactive Scan
                                                      |
                                                   Response to User

External Services:                  Background Services:
  Brave API (search)                  Self-improvement (daily 3 AM)
  Web Fetch (articles)                Evolution loop (every 6 hours)
  YouTube (transcripts)               Retrospective crew (daily 4 AM)
  Docker Sandbox (code)               Benchmark snapshot (daily 5 AM)
  ChromaDB (scoped memory)            Code auditor (every 4 hours)
                                      Error resolution (every 30 min)
                                      Workspace sync (hourly)
                                      Firebase heartbeat (60s)
```

### Agent Roles

| Agent | Model | Role | Key Tools |
|-------|-------|------|-----------|
| **Commander** | `claude-opus-4-6` | Routes requests, handles special commands, runs proactive scanning | Memory, belief state viewer |
| **Researcher** | Specialist LLM | Web search, article reading, YouTube transcripts, structured reports | web_search, web_fetch, youtube_transcript, self_report, reflection |
| **Coder** | Specialist LLM | Writes, tests, debugs code in Docker sandbox | execute_code, file_manager, self_report, reflection |
| **Writer** | Specialist LLM | Summaries, reports, docs, emails adapted to destination | file_manager, web_search, self_report, reflection |
| **Self-Improver** | Specialist LLM | Learns topics, extracts YouTube knowledge, proposes improvements | web_search, web_fetch, youtube_transcript, self_report, reflection |
| **Critic** | Specialist LLM | Adversarial review of research for accuracy, gaps, unjustified claims | scoped_memory, self_report, reflection |
| **Introspector** | Specialist LLM | Analyzes execution traces, generates TRIGGER/ACTION/EVIDENCE policies | scoped_memory, self_report, reflection |

All agents share: scoped memory tools, team memory, self-report, and reflection capabilities.

### Crew System

- **ResearchCrew** -- Plans research, spawns parallel sub-agents, synthesizes, runs **Critic review**, tracks belief states, records benchmarks
- **CodingCrew** -- Sandbox code execution with policy loading and benchmarking
- **WritingCrew** -- Destination-adapted content with policy loading and benchmarking
- **SelfImprovementCrew** -- Learning queue, YouTube extraction, improvement proposals
- **RetrospectiveCrew** -- Gathers execution traces, runs Introspector, stores improvement policies

The **parallel runner** provides concurrent execution with error isolation (configurable thread pool).

### Tool Inventory

| Tool | Description |
|------|-------------|
| `web_search` | Brave Search API -- top 5 results |
| `web_fetch` | URL content extraction with SSRF protection |
| `get_youtube_transcript` | YouTube transcript extraction with multi-strategy fallback |
| `execute_code` | Docker sandbox code execution (Python, Bash, Node.js, Ruby) |
| `file_manager` | Workspace file read/write with path traversal protection |
| `read_attachment` | Extracts text from PDF, DOCX, XLSX, images |
| `memory_store` / `memory_retrieve` | Per-crew ChromaDB storage and retrieval |
| `team_memory_store` / `team_memory_retrieve` | Shared cross-crew memory |
| `scoped_memory_store` / `scoped_memory_retrieve` | Hierarchical scoped memory with dual retrieval profiles |
| `team_decision` | Records team-level decisions and shared conclusions |
| `update_team_belief` | Updates belief state about teammate progress |
| `team_state` | Views current state of all team members |
| `self_report` | Agent self-assessment: confidence, completeness, blockers, risks |
| `store_reflection` | Post-task reflection: what went well/wrong, lessons learned |

---

## Self-Aware AI System

The system implements a four-phase self-awareness architecture grounded in current research on functional self-awareness (Li et al. 2025), proactive multi-agent cooperation (ProAgent, AAAI 2024), generative agent memory (Park et al. 2023), and meta-cognitive self-improvement (Evers et al. 2025).

### Phase 1: Functional Self-Awareness

Each agent carries a **structured self-model** (`app/self_awareness/self_model.py`) describing its capabilities, limitations, operating principles, and known failure modes. This is injected into every agent's backstory.

Two awareness tools are available to all agents:
- **SelfReportTool** -- After completing work, agents assess confidence (high/medium/low), completeness, blockers, risks, and needs from teammates. Reports stored in ChromaDB.
- **ReflectionTool** -- Agents record post-task lessons: what went well, what went wrong, what to change. Stored in both agent-specific and shared team memory.

### Phase 2: Shared Memory & Cooperation

**Scoped Memory** (`app/memory/scoped_memory.py`) provides hierarchical memory on top of ChromaDB with two retrieval profiles:
- **Operational** -- Recency-weighted for active tasks (boosts items from last 24 hours)
- **Strategic** -- Importance-weighted for policies and lessons (filters by high/critical importance)

**Belief State Tracking** (`app/memory/belief_state.py`) implements ProAgent-style intention inference. Each crew updates its agent's state (idle/working/blocked/completed/failed) with current task, confidence, and needs. Commander sees team state in routing context.

**Critic Agent** (`app/agents/critic.py`) provides adversarial quality review on research outputs, checking for unsupported claims, gaps, unjustified confidence, and contradictions.

### Phase 3: Proactive Cooperation

After every crew execution, the Commander runs a **proactive trigger scanner** (`app/proactive/trigger_scanner.py`) checking for:
1. **Low confidence** -- Recent self-reports with low confidence trigger verification recommendations
2. **Unfulfilled needs** -- Agents with unmet teammate needs get flagged
3. **Quality drift** -- Confidence trending downward triggers an alert

Up to 2 proactive notes are appended to each response.

### Phase 4: Meta-Cognitive Self-Improvement

**RetrospectiveCrew** (daily 4 AM) gathers execution traces and generates improvement policies in TRIGGER/ACTION/EVIDENCE format.

**Policy Loader** loads relevant policies before each crew execution, injecting them into task descriptions.

**Benchmarks** (`app/benchmarks.py`) tracks: task completion time, quality scores, proactive intervention rates, and period-over-period trends. View via `benchmarks` command.

---

## SOUL.md Personality Framework

Following the SoulSpec standard and research on agent personality (arXiv:2510.21413), each agent has a distinct identity defined in markdown soul files. Research shows structured persona files reduce runtime by ~29% and token consumption by ~17%.

### Soul File Architecture

```
app/souls/
├── constitution.md      # Shared: Safety > Honesty > Compliance > Helpfulness
├── style.md             # Shared: communication conventions, forbidden patterns
├── agents_protocol.md   # Shared: routing flow, escalation, quality gates
├── commander.md         # Calm, decisive operations manager
├── researcher.md        # Methodical, skeptical, source-obsessed analyst
├── coder.md             # Precise, pragmatic, complexity-allergic engineer
├── writer.md            # Clear, audience-aware communicator
├── self_improver.md     # Curious, systematic, constructively critical
└── loader.py            # Loads and composes soul files into backstories
```

### Backstory Composition

The soul loader (`app/souls/loader.py`) composes each agent's backstory from four layers:

```
Agent backstory = CONSTITUTION + SOUL (per-role) + STYLE + Self-Model block
```

Falls back to just the self-model block if soul files don't exist.

---

## Project Structure

```
crewai-team/
├── app/
│   ├── main.py                       # FastAPI gateway, lifespan, scheduler
│   ├── config.py                     # Pydantic settings with validation
│   ├── benchmarks.py                 # Automated benchmarking system
│   ├── evolution.py                  # Autonomous evolution loop
│   ├── self_heal.py                  # Error diagnosis and auto-fix
│   ├── proposals.py                  # Improvement proposal management
│   ├── auditor.py                    # Code audit + error resolution loop
│   ├── auto_deployer.py              # Apply approved code proposals
│   ├── vetting.py                    # Claude quality gate for local LLM output
│   ├── llm_factory.py                # Role-based LLM provider (Ollama + Claude)
│   ├── agents/
│   │   ├── commander.py              # Router + proactive scanning
│   │   ├── researcher.py, coder.py, writer.py, self_improver.py
│   │   ├── critic.py                 # Adversarial quality reviewer
│   │   └── introspector.py           # Meta-cognitive policy generator
│   ├── crews/
│   │   ├── research_crew.py          # Parallel research + critic review
│   │   ├── coding_crew.py, writing_crew.py, self_improvement_crew.py
│   │   ├── retrospective_crew.py     # Meta-cognitive retrospective
│   │   └── parallel_runner.py
│   ├── tools/
│   │   ├── web_search.py, web_fetch.py, youtube_transcript.py
│   │   ├── code_executor.py, file_manager.py, attachment_reader.py
│   │   ├── memory_tool.py, scoped_memory_tool.py
│   │   ├── self_report_tool.py, reflection_tool.py
│   ├── memory/
│   │   ├── chromadb_manager.py       # ChromaDB + extensions
│   │   ├── scoped_memory.py          # Hierarchical scoped memory
│   │   └── belief_state.py           # ProAgent belief tracking
│   ├── self_awareness/
│   │   └── self_model.py             # Structured self-models per role
│   ├── souls/                        # SOUL.md personality framework
│   │   ├── loader.py, constitution.md, style.md, agents_protocol.md
│   │   ├── commander.md, researcher.md, coder.md, writer.md, self_improver.md
│   ├── proactive/
│   │   ├── trigger_scanner.py        # Post-execution trigger detection
│   │   └── proactive_behaviors.py
│   └── policies/
│       └── policy_loader.py          # Policy storage and injection
├── tests/
│   ├── test_security.py              # 79 security and unit tests
│   └── test_self_awareness.py        # 58 self-awareness integration tests
├── signal/, sandbox/, dashboard/, scripts/
├── workspace/                        # Runtime data (Docker volume)
├── docker-compose.yml, Dockerfile, requirements.txt, .env.example
```

---

## Prerequisites

- **Python 3.11+**, **Docker**, **Docker Compose**
- **signal-cli** (Java 17+), **Tailscale**
- **Anthropic API key**, **Brave Search API key**
- **Dedicated phone number** for the Signal bot
- (Optional) Firebase project, GitHub repo for backup, Ollama for local LLM

---

## Installation

```bash
cd ~/crewai-team
bash scripts/install.sh
```

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | -- | Anthropic API key (required) |
| `BRAVE_API_KEY` | -- | Brave Search API key (required) |
| `SIGNAL_BOT_NUMBER` / `SIGNAL_OWNER_NUMBER` | -- | Phone numbers (required) |
| `GATEWAY_SECRET` | -- | Auth token (required) |
| `SELF_IMPROVE_CRON` | `0 3 * * *` | Self-improvement schedule |
| `EVOLUTION_CRON` | `0 */6 * * *` | Evolution loop schedule |
| `RETROSPECTIVE_CRON` | `0 4 * * *` | Retrospective analysis schedule |
| `BENCHMARK_CRON` | `0 5 * * *` | Benchmark snapshot schedule |
| `PROACTIVE_SCAN_ENABLED` | `true` | Enable proactive trigger scanning |
| `POLICY_LOADING_ENABLED` | `true` | Enable policy injection before tasks |
| `LOCAL_LLM_ENABLED` | `true` | Use local Ollama models |

See `.env.example` for the full list.

### Signal Setup

Register signal-cli, start daemon with HTTP mode, configure forwarder.

### Tailscale Setup

Zero-trust private networking. Never use `tailscale funnel`.

### Firebase Dashboard Setup

Deploy Firestore rules and hosting. Set service account path in `.env`.

---

## Usage

### Signal Commands Reference

| Command | Description |
|---------|-------------|
| *Any request* | Commander routes to appropriate crew |
| `status` | System health, proposals, LLM mode |
| `learn <topic>` | Add to learning queue |
| `watch <youtube_url>` | Extract and learn from video |
| `improve` | Run improvement scan |
| `proposals` / `approve <id>` / `reject <id>` | Manage proposals |
| `evolve` | Trigger evolution cycle |
| `experiments` | Show experiment journal |
| `errors` | Show recent errors |
| `audit` | Run code audit |
| `fleet` / `models` | LLM fleet status |
| `retrospective` | Run meta-cognitive retrospective |
| `benchmarks` | Show performance benchmarks and trends |
| `policies` | Show stored improvement policies |

### Self-Improvement System

Three modes: learning queue (daily), YouTube extraction (on demand), improvement scanning (on demand).

### Evolution Loop

Every 6 hours: gather metrics, propose one change, test, keep or discard.

### Self-Healing System

On error: log, diagnose, create fix proposal. Tracks patterns.

### Improvement Proposals

Skill (apply immediately), code (staged), config (manual restart). All require human approval.

### File Attachments

PDF, DOCX, XLSX, images via Signal. Text extracted and included as context.

---

## Docker Architecture

### Services

| Service | Purpose |
|---------|---------|
| `gateway` | FastAPI app, agents, scheduler |
| `chromadb` | Vector database (no internet access) |
| `docker-proxy` | Limited Docker API for sandbox |

### Sandbox Security

Network disabled, read-only FS, all capabilities dropped, memory/CPU/timeout limits, non-root user.

### Network Isolation

Gateway spans external + internal networks. ChromaDB and docker-proxy are internal-only.

---

## Security

- **Network**: Tailscale encrypted tunnel, `127.0.0.1` bind only
- **Auth**: HMAC Bearer token, owner-only sender authorization
- **Prompt injection**: Pattern filtering, XML wrapping, content labeling
- **SSRF**: Private IP blocking, DNS resolution, redirect validation
- **Sandbox**: Docker isolation with full security hardening
- **Audit**: JSON rotating log, phone numbers always redacted

---

## Memory & Persistence

### ChromaDB Vector Memory

Per-crew collections plus shared `team_shared`. Local `all-MiniLM-L6-v2` embeddings.

### Scoped Memory Hierarchy

| Scope | Purpose |
|-------|---------|
| `scope_team` | Team-wide decisions and shared context |
| `scope_agent_{name}` | Per-agent private working memory |
| `scope_beliefs` | Belief state tracking |
| `scope_policies` | Improvement policies from retrospective |
| `scope_project_{name}` | Per-project knowledge |
| `self_reports` | Agent self-assessment history |
| `reflections_{role}` | Per-agent post-task reflections |

Two retrieval profiles: **Operational** (recency-boosted) and **Strategic** (importance-filtered).

### Conversation History

SQLite with HMAC-hashed sender IDs. Last N exchanges injected for context.

### Skill Files

Markdown in `workspace/skills/`. Structure: Key Concepts, Best Practices, Code Patterns, Sources.

### Cloud Backup

Optional git-based backup. Excludes ChromaDB and transient outputs.

---

## Monitoring Dashboard

Firebase-hosted real-time dashboard: system status, crew cards, task table, proposals, scheduled activities, live activity feed.

---

## Testing

137 tests across two test files:

```bash
.venv/bin/python -m pytest tests/ -v          # All 137 tests
.venv/bin/python -m pytest tests/test_security.py -v         # 79 security tests
.venv/bin/python -m pytest tests/test_self_awareness.py -v   # 58 self-awareness tests
```

Tests run without live dependencies by stubbing heavy imports. Coverage: sanitization, SSRF, path traversal, rate limiting, config validation, self-models, self-report/reflection tools, scoped memory, belief states, ChromaDB extensions, proactive scanner, policy loader, benchmarks, retrospective crew, and cross-phase integration.

---

## Cost Estimate

~$20-70/month for moderate use (10-20 tasks/day). Retrospective and proactive scanning add 1-5 lightweight LLM calls per day. Local Ollama models reduce costs significantly.

---

## Development

### Key Source Files

| File | Responsibility |
|------|---------------|
| `app/main.py` | Entry point, lifespan, scheduler, all background jobs |
| `app/agents/commander.py` | Core routing, dispatch, proactive scanning |
| `app/souls/loader.py` | Soul file loading and backstory composition |
| `app/self_awareness/self_model.py` | Structured self-models for all roles |
| `app/memory/scoped_memory.py` | Hierarchical scoped memory |
| `app/memory/belief_state.py` | ProAgent belief state tracking |
| `app/proactive/trigger_scanner.py` | Post-execution proactive detection |
| `app/policies/policy_loader.py` | Policy storage, loading, injection |
| `app/benchmarks.py` | Automated benchmarking with trends |
| `app/crews/retrospective_crew.py` | Meta-cognitive retrospective |
| `app/evolution.py` | Autonomous evolution loop |

### Adding a New Tool

1. Create file in `app/tools/` with `@tool` decorator or `BaseTool` subclass
2. Import and add to relevant agent(s) in `app/agents/`
3. Add SSRF protection if accessing external resources
4. Restrict file paths if writing files

### Adding a New Crew

1. Create `app/crews/my_crew.py` with `crew_started/completed/failed` reporting
2. Add `update_belief()` calls for belief state tracking
3. Add `load_relevant_policies()` for policy injection
4. Add `record_metric()` for benchmarking
5. Register in `Commander._run_crew()` and `ROUTING_PROMPT`
6. Add crew card to the Firebase dashboard

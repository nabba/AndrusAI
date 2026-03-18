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
  - [Network Security](#network-security)
  - [Authentication & Authorization](#authentication--authorization)
  - [Prompt Injection Defense](#prompt-injection-defense)
  - [SSRF Protection](#ssrf-protection)
  - [Sandbox Isolation](#sandbox-isolation)
  - [Audit Logging](#audit-logging)
- [Memory & Persistence](#memory--persistence)
  - [ChromaDB Vector Memory](#chromadb-vector-memory)
  - [Conversation History](#conversation-history)
  - [Skill Files](#skill-files)
  - [Cloud Backup](#cloud-backup)
- [Monitoring Dashboard](#monitoring-dashboard)
- [API Rate Limiting](#api-rate-limiting)
- [Health Check](#health-check)
- [Cost Estimate](#cost-estimate)
- [Development](#development)
  - [Key Source Files](#key-source-files)
  - [Adding a New Tool](#adding-a-new-tool)
  - [Adding a New Crew](#adding-a-new-crew)

---

## Overview

This project implements an autonomous AI agent team that you interact with entirely through Signal messages from your phone. A **Commander** agent receives your requests, classifies them, and dispatches specialist **crews** (Research, Coding, Writing) to handle them — optionally in parallel. The system continuously improves itself by learning new topics, diagnosing its own errors, and running an evolution loop inspired by [Karpathy's autoresearch](https://github.com/karpathy/autoresearch).

Key capabilities:

- **Natural language task dispatch** — send any request via Signal; the Commander routes it to the right crew
- **Web research** — search the web, read articles, extract YouTube transcripts
- **Code execution** — write, test, and debug code in a sandboxed Docker container
- **Content creation** — summaries, reports, documentation, emails
- **Self-improvement** — learns new topics on a schedule, saves skill files that improve future responses
- **Self-healing** — automatically diagnoses errors and creates fixes
- **Autonomous evolution** — experiments on itself, keeps improvements, discards regressions
- **File attachment support** — send PDFs, DOCX, XLSX, images via Signal for analysis
- **Real-time monitoring** — Firebase-hosted dashboard with live crew status, task tracking, and activity feed
- **Persistent memory** — ChromaDB vector store for semantic retrieval across all agents
- **Conversation history** — remembers recent exchanges for contextual follow-up messages
- **Cloud backup** — optional git-based workspace sync to a remote repository

---

## Architecture

### System Diagram

```
┌──────────────┐     Signal      ┌─────────────┐    JSON-RPC     ┌───────────────────┐
│   iPhone     │ ──────────────► │ signal-cli   │ ─────────────► │   Forwarder       │
│  (Signal)    │ ◄────────────── │   daemon     │                │ signal/forwarder.py│
└──────────────┘                 └─────────────┘                 └────────┬──────────┘
                                                                          │ HTTP POST
                                                              ┌───────────▼───────────┐
                                  Tailscale (private)         │  FastAPI Gateway       │
                                  127.0.0.1:8765              │  app/main.py           │
                                                              │                        │
                                                              │  ┌──────────────────┐  │
                                                              │  │   Commander       │  │
                                                              │  │   (Router LLM)    │  │
                                                              │  └──────┬───────────┘  │
                                                              │         │ dispatch     │
                                                              │   ┌─────┼─────┐       │
                                                              │   ▼     ▼     ▼       │
                                                              │ Research Coding Writing │
                                                              │  Crew    Crew   Crew   │
                                                              └──┬──────┬──────┬──────┘
                                                                 │      │      │
                                    ┌────────────────────────────┘      │      └───────────────────┐
                                    ▼                                   ▼                           ▼
                              ┌───────────┐                    ┌──────────────┐              ┌───────────┐
                              │ Brave API │                    │Docker Sandbox│              │ ChromaDB  │
                              │ Web Fetch │                    │(code runner) │              │ (memory)  │
                              │ YouTube   │                    └──────────────┘              └───────────┘
                              └───────────┘
                                                              ┌──────────────────────────────┐
                                                              │  Background Services          │
                                                              │  • Self-improvement (cron)    │
                                                              │  • Evolution loop (cron)      │
                                                              │  • Workspace sync (cron)      │
                                                              │  • Self-healing (on error)    │
                                                              │  • Firebase heartbeat (60s)   │
                                                              └──────────────────────────────┘
```

### Agent Roles

| Agent | Model | Role | Tools |
|-------|-------|------|-------|
| **Commander** | `claude-opus-4-6` | Routes requests to specialist crews via JSON classification. Handles special commands (learn, watch, status, proposals). | Memory (commander collection) |
| **Researcher** | `claude-sonnet-4-6` | Web search, article reading, YouTube transcript extraction. Synthesizes structured research reports. | web_search, web_fetch, youtube_transcript, file_manager, read_attachment, memory |
| **Coder** | `claude-sonnet-4-6` | Writes, tests, and debugs code in a Docker sandbox. | execute_code, file_manager, web_search, read_attachment, memory |
| **Writer** | `claude-sonnet-4-6` | Summaries, reports, documentation, emails. Adapts length to destination (Signal vs. file). | file_manager, web_search, read_attachment, memory |
| **Self-Improver** | `claude-opus-4-6` | Learns topics from the queue, extracts YouTube knowledge, proposes system improvements. | web_search, web_fetch, youtube_transcript, file_manager, memory |

### Crew System

Each crew wraps one or more agents with a task description and runs them via CrewAI's `Crew.kickoff()`. Crews are defined in `app/crews/`:

- **ResearchCrew** (`research_crew.py`) — Plans research by splitting complex topics into 1–4 subtopics, spawns parallel sub-agents, then synthesizes results into a unified report.
- **CodingCrew** (`coding_crew.py`) — Single-agent crew that writes code, executes it in the Docker sandbox, debugs failures, and saves the final result.
- **WritingCrew** (`writing_crew.py`) — Single-agent crew that retrieves context from memory and writes formatted content.
- **SelfImprovementCrew** (`self_improvement_crew.py`) — Three modes: (1) learning queue processing, (2) YouTube transcript distillation, (3) improvement scanning with proposal generation.

The **parallel runner** (`parallel_runner.py`) provides a shared `ThreadPoolExecutor` (configurable size, default 6 threads) for running multiple crews or sub-agents concurrently with error isolation.

### Tool Inventory

| Tool | File | Description |
|------|------|-------------|
| `web_search` | `app/tools/web_search.py` | Brave Search API — returns top 5 results (title, URL, snippet) |
| `web_fetch` | `app/tools/web_fetch.py` | Fetches and extracts clean text from URLs using trafilatura. Includes SSRF protection (blocks private IPs, internal hosts, redirects to private networks). |
| `get_youtube_transcript` | `app/tools/youtube_transcript.py` | Extracts YouTube video transcripts. Supports multiple URL formats. Three-strategy fallback: direct fetch → list-based → explicit language codes. |
| `execute_code` | `app/tools/code_executor.py` | Runs code (Python, Bash, Node.js, Ruby) in a Docker sandbox with no network, read-only FS, dropped capabilities, memory/CPU limits. |
| `file_manager` | `app/tools/file_manager.py` | Read/write files in the workspace. Write access restricted to `output/`, `skills/`, `proposals/` directories. Path traversal protection. |
| `read_attachment` | `app/tools/attachment_reader.py` | Extracts text from Signal attachments: PDF (pypdf), DOCX (python-docx), XLSX (openpyxl), images (Pillow + optional OCR). |
| `memory_store` | `app/tools/memory_tool.py` | Store text in a per-crew ChromaDB collection with optional metadata. |
| `memory_retrieve` | `app/tools/memory_tool.py` | Semantic search across a per-crew ChromaDB collection. |
| `team_memory_store` | `app/tools/memory_tool.py` | Store text in the shared cross-crew collection (visible to all agents). |
| `team_memory_retrieve` | `app/tools/memory_tool.py` | Semantic search across the shared team-wide collection. |

---

## Project Structure

```
crewai-team/
├── app/                          # Main application package
│   ├── __init__.py
│   ├── main.py                   # FastAPI gateway, lifespan, scheduler, /signal/inbound endpoint
│   ├── config.py                 # Pydantic settings (all env vars with validation)
│   ├── security.py               # Sender authorization & rate limiting (30 msgs / 10 min)
│   ├── sanitize.py               # Prompt injection defense (pattern filtering + XML wrapping)
│   ├── audit.py                  # Structured audit logging (JSON events to rotating file)
│   ├── signal_client.py          # Send messages back via signal-cli (HTTP + Unix socket)
│   ├── conversation_store.py     # SQLite conversation history with HMAC-hashed sender IDs
│   ├── firebase_reporter.py      # Real-time Firestore status reporting (fire-and-forget)
│   ├── workspace_sync.py         # Git-based cloud backup of workspace
│   ├── self_heal.py              # Error journal + background diagnosis agent
│   ├── evolution.py              # Autonomous evolution loop (experiment → measure → keep/discard)
│   ├── proposals.py              # Improvement proposal management (create, list, approve, reject)
│   ├── rate_throttle.py          # Global API rate limiter (token bucket + litellm monkey-patch)
│   ├── agents/                   # Agent definitions
│   │   ├── commander.py          # Commander: routes requests, handles special commands
│   │   ├── researcher.py         # Researcher agent factory
│   │   ├── coder.py              # Coder agent factory
│   │   ├── writer.py             # Writer agent factory
│   │   └── self_improver.py      # Self-improver agent factory
│   ├── crews/                    # Crew orchestrators
│   │   ├── research_crew.py      # Research with parallel sub-agents
│   │   ├── coding_crew.py        # Code execution crew
│   │   ├── writing_crew.py       # Content writing crew
│   │   ├── self_improvement_crew.py  # Learning, YouTube, improvement scanning
│   │   └── parallel_runner.py    # Shared thread pool for parallel execution
│   ├── tools/                    # CrewAI tool definitions
│   │   ├── web_search.py         # Brave Search API
│   │   ├── web_fetch.py          # URL content extraction with SSRF protection
│   │   ├── youtube_transcript.py # YouTube transcript extraction
│   │   ├── code_executor.py      # Docker sandbox code runner
│   │   ├── file_manager.py       # Workspace file read/write
│   │   ├── attachment_reader.py  # PDF, DOCX, XLSX, image extraction
│   │   └── memory_tool.py        # Per-crew + shared team memory tools
│   └── memory/
│       └── chromadb_manager.py   # ChromaDB client, embedding (all-MiniLM-L6-v2), store/retrieve
├── signal/
│   └── forwarder.py              # Bridges signal-cli → FastAPI gateway via HTTP
├── sandbox/
│   └── Dockerfile                # Sandbox image: Python, Node, Ruby, Bash + common libraries
├── dashboard/
│   ├── public/
│   │   └── index.html            # Single-page monitoring dashboard (Firebase real-time)
│   ├── firebase.json             # Firebase Hosting config
│   ├── firestore.rules           # Firestore security rules (public read, backend-only write)
│   └── .firebaserc               # Firebase project config
├── scripts/
│   ├── install.sh                # One-command setup: venv, deps, Docker build, compose up
│   └── health_check.sh           # Verifies gateway, sandbox, ChromaDB, signal-cli, Tailscale
├── workspace/                    # Runtime data (mounted as Docker volume)
│   └── skills/
│       └── learning_queue.md     # Topics for self-improvement (one per line)
├── Dockerfile                    # Main application image
├── docker-compose.yml            # Three services: gateway, chromadb, docker-proxy
├── entrypoint.sh                 # Permission setup + privilege drop (gosu)
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variable template
└── .gitignore
```

---

## Prerequisites

- **Python 3.11+**
- **Docker** and **Docker Compose**
- **signal-cli** (requires Java 17+) — for Signal messaging
- **Tailscale** — for secure private networking (no public ports)
- **Anthropic API key** — for Claude LLM access
- **Brave Search API key** — free tier available at [api.search.brave.com](https://api.search.brave.com/)
- **A dedicated phone number** — for the Signal bot (separate from your personal number)
- (Optional) **Firebase project** — for the monitoring dashboard
- (Optional) **GitHub repository** — for cloud workspace backup

---

## Installation

```bash
cd ~/crewai-team
bash scripts/install.sh
```

The install script performs the following steps:

1. Creates workspace directories (`output/`, `memory/`, `skills/`)
2. Copies `.env.example` to `.env` and prompts you to fill in API keys
3. Creates a Python 3.11 virtual environment and installs dependencies
4. Builds the Docker sandbox image (`crewai-sandbox:latest`)
5. Pulls the ChromaDB Docker image
6. Starts all Docker Compose services

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure the following:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Your Anthropic API key |
| `BRAVE_API_KEY` | Yes | — | Brave Search API key |
| `SIGNAL_BOT_NUMBER` | Yes | — | Bot's dedicated phone number (`+1XXXXXXXXXX`) |
| `SIGNAL_OWNER_NUMBER` | Yes | — | Your iPhone number (`+1XXXXXXXXXX`) |
| `SIGNAL_CLI_PATH` | No | `/opt/homebrew/bin/signal-cli` | Path to signal-cli binary |
| `SIGNAL_SOCKET_PATH` | No | `/tmp/signal-cli.sock` | Unix socket path for signal-cli |
| `SIGNAL_HTTP_URL` | No | — | HTTP endpoint for signal-cli (preferred in Docker) |
| `SIGNAL_ATTACHMENT_PATH` | No | — | Path where signal-cli stores downloaded attachments |
| `GATEWAY_SECRET` | Yes | — | Random 64-char string for gateway authentication |
| `GATEWAY_PORT` | No | `8765` | Gateway HTTP port |
| `GATEWAY_BIND` | No | `127.0.0.1` | Bind address (**never** change to `0.0.0.0`) |
| `COMMANDER_MODEL` | No | `claude-opus-4-6` | LLM model for Commander |
| `SPECIALIST_MODEL` | No | `claude-sonnet-4-6` | LLM model for specialist agents |
| `SANDBOX_IMAGE` | No | `crewai-sandbox:latest` | Docker image for code execution |
| `SANDBOX_TIMEOUT_SECONDS` | No | `30` | Max execution time per sandbox run (1–120) |
| `SANDBOX_MEMORY_LIMIT` | No | `512m` | Docker memory limit for sandbox (32m–2048m) |
| `SANDBOX_CPU_LIMIT` | No | `0.5` | CPU limit for sandbox (0.05–4.0) |
| `SELF_IMPROVE_CRON` | No | `0 3 * * *` | Cron schedule for self-improvement (default: daily 3 AM) |
| `EVOLUTION_CRON` | No | `0 */6 * * *` | Cron schedule for evolution loop (default: every 6 hours) |
| `MAX_PARALLEL_CREWS` | No | `3` | Max crews Commander can dispatch in parallel |
| `MAX_SUB_AGENTS` | No | `4` | Max sub-agents a single crew can spawn |
| `THREAD_POOL_SIZE` | No | `6` | Shared thread pool size (caps total concurrent API calls) |
| `WORKSPACE_BACKUP_REPO` | No | — | Git remote URL for cloud backup |
| `WORKSPACE_SYNC_CRON` | No | `0 * * * *` | Cron schedule for workspace sync (default: hourly) |
| `CONVERSATION_HISTORY_TURNS` | No | `10` | Recent exchanges injected into each new request |
| `ANTHROPIC_MAX_RPM` | No | `3` | Max API requests per minute (rate throttle) |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | No | — | Path to Firebase service account JSON |

### Signal Setup

1. Install signal-cli (requires Java 17+):
   ```bash
   signal-cli --version  # verify installed
   ```

2. Register your bot number:
   ```bash
   signal-cli -a +1XXXXXXXXXX register
   signal-cli -a +1XXXXXXXXXX verify YOUR_SMS_CODE
   ```

3. Start signal-cli daemon (choose one):

   **Option A — Unix socket** (same host only):
   ```bash
   signal-cli -a +1XXXXXXXXXX daemon --socket /tmp/signal-cli.sock
   ```

   **Option B — HTTP** (works across Docker boundary, preferred):
   ```bash
   signal-cli -a +1XXXXXXXXXX daemon --http 127.0.0.1:7583 --receive-mode on-start
   ```
   Then set `SIGNAL_HTTP_URL=http://host.docker.internal:7583` in `.env`.

4. Start the forwarder (bridges signal-cli to the gateway):
   ```bash
   python signal/forwarder.py
   ```

### Tailscale Setup

Tailscale provides a zero-trust private network so the gateway never exposes ports to the public internet.

1. Install and authenticate: `sudo tailscale up`
2. Install Tailscale on your iPhone (same account)
3. Expose the gateway privately: `sudo tailscale serve --bg 8765`
4. **Never** use `tailscale funnel` — this would expose the gateway to the public internet

### Firebase Dashboard Setup

The monitoring dashboard is a static HTML page hosted on Firebase Hosting that reads from Firestore in real time.

1. Create a Firebase project (or use the pre-configured `botarmy-ba0c9`)
2. Enable Firestore in the Firebase console
3. Deploy Firestore security rules:
   ```bash
   cd dashboard
   firebase deploy --only firestore:rules
   ```
4. Deploy the dashboard:
   ```bash
   firebase deploy --only hosting
   ```
5. Set `FIREBASE_SERVICE_ACCOUNT_JSON` in `.env` to point to your service account credentials
6. The backend writes to Firestore using the Firebase Admin SDK; the dashboard reads via the Firebase JS SDK

---

## Usage

### Signal Commands Reference

Send these messages from your iPhone to the bot's Signal number:

| Command | Description |
|---------|-------------|
| *Any natural language request* | Commander classifies and routes to the appropriate crew (research, coding, writing) or answers directly |
| `status` | Reports system health and pending proposal count |
| `learn <topic>` | Adds a topic to the learning queue for autonomous research |
| `show learning queue` | Displays all pending topics |
| `run self-improvement now` | Immediately processes up to 3 topics from the learning queue |
| `watch <youtube_url>` | Extracts transcript, distills into a skill file, stores in team memory |
| `improve` | Runs an improvement scan — analyzes capabilities and creates 1–3 proposals |
| `proposals` | Lists pending improvement proposals |
| `approve <id>` | Approves and applies a proposal |
| `reject <id>` | Rejects a proposal |
| `evolve` | Triggers one evolution cycle manually |
| `experiments` | Shows the evolution experiment journal |
| `errors` | Shows recent errors with diagnosis status and patterns |

Complex requests are automatically decomposed and dispatched to multiple crews in parallel. For example, "Research Docker security best practices and write a summary report" would dispatch both a Research crew and a Writing crew.

### Self-Improvement System

The self-improvement system operates in three modes:

1. **Learning Queue** — Send `learn <topic>` to add topics. At the configured cron time (default: 3 AM daily), the system processes up to 3 topics: researches 3+ web sources, extracts YouTube transcripts if relevant, and saves structured Markdown skill files to `workspace/skills/`. Skills are loaded into future agent contexts.

2. **YouTube Learning** — Send `watch <url>` to extract a video's transcript and distill it into a skill file. The knowledge is stored both as a file and in team memory for cross-agent access.

3. **Improvement Scanning** — Send `improve` to trigger an analyst agent that reviews current skills, tools, and crews, identifies capability gaps, and generates 1–3 structured proposals.

### Evolution Loop

Inspired by Karpathy's autoresearch, the evolution loop runs autonomously (default: every 6 hours) and follows these principles:

1. **Gather metrics** — error patterns, skill inventory, experiment history, pending proposals
2. **Identify highest-impact improvement** — recurring errors, missing skills, capability gaps
3. **Execute ONE change** — either a skill fix (applied immediately) or a code proposal (needs approval)
4. **Log the experiment** — with hypothesis, change type, and result
5. **Keep or discard** — skill fixes that improve capabilities are kept; failed experiments are discarded

The experiment journal is stored at `workspace/evolution_journal.json`. View it via `experiments` command.

### Self-Healing System

When any crew encounters an error:

1. The error is logged to a persistent journal (`workspace/error_journal.json`) with full context: crew name, user input, error type, traceback, timestamp
2. A background "System Doctor" agent is spawned to diagnose the failure
3. The doctor searches the web for solutions and creates one of:
   - **Knowledge fix** — a skill file teaching the team to handle the situation (applied immediately)
   - **Code fix proposal** — staged for user approval via Signal
   - **Transient note** — for temporary issues like network timeouts
4. Error frequency is tracked to detect recurring patterns

### Improvement Proposals

Proposals are the system's way of suggesting changes that require human approval:

- **Skill proposals** (`skill`) — new `.md` files for `workspace/skills/`. Low risk, applied immediately on approval.
- **Code proposals** (`code`) — new or modified Python files. Copied to `workspace/applied_code/` on approval and take effect after container restart.
- **Config proposals** (`config`) — changes to `.env` or `docker-compose.yml`. Require manual restart.

Proposals are stored in `workspace/proposals/<id>_<title>/` with a `proposal.md`, `status.json`, and `files/` directory.

### File Attachments

Send files via Signal for analysis. Supported formats:

| Format | Extraction Method |
|--------|-------------------|
| PDF | pypdf — text extraction, up to 50 pages |
| DOCX | python-docx — paragraphs and tables |
| XLSX | openpyxl — all sheets, up to 500 rows each |
| JPG/PNG/WebP/GIF | Pillow + optional pytesseract OCR |

Attachments are mounted read-only at `/app/attachments/` inside the container. The Commander includes extracted text as context for any crew it dispatches.

---

## Docker Architecture

### Services

The `docker-compose.yml` defines three services:

| Service | Image | Purpose |
|---------|-------|---------|
| `gateway` | Built from `./Dockerfile` | Main application: FastAPI, agents, scheduler |
| `chromadb` | `chromadb/chroma:0.5.23` | Vector database for agent memory |
| `docker-proxy` | `tecnativa/docker-socket-proxy:0.2` | Limits Docker API access for sandbox creation |

### Sandbox Security

The code execution sandbox (`sandbox/Dockerfile`) provides multi-layer isolation:

- **Network disabled** — `network_disabled=True` prevents all network access
- **Read-only filesystem** — `read_only=True` prevents writing to the container
- **All capabilities dropped** — `cap_drop=["ALL"]` removes all Linux capabilities
- **No new privileges** — `security_opt=["no-new-privileges:true"]`
- **Memory limit** — configurable, default 512 MB (hard cap: 2 GB)
- **CPU limit** — configurable, default 0.5 cores (hard cap: 4.0)
- **Execution timeout** — configurable, default 30 seconds (hard cap: 120)
- **Non-root user** — runs as `sandbox` user (UID 1000)
- **Code size limit** — max 512 KB per submission
- **Workspace mounted read-only** — sandbox can only read its input code

Supported languages: Python 3.11, Bash, Node.js (with 256 MB heap cap), Ruby.

### Network Isolation

```
┌─────────────────────────────────────────┐
│  external network (bridge)              │
│    └── gateway (internet access)        │
├─────────────────────────────────────────┤
│  internal network (bridge, no internet) │
│    ├── gateway ──► chromadb             │
│    └── gateway ──► docker-proxy         │
└─────────────────────────────────────────┘
```

- ChromaDB and docker-proxy have **no internet access** (internal-only network)
- The gateway spans both networks: external for API calls, internal for ChromaDB and Docker
- The gateway itself runs with `read_only: true` filesystem and `no-new-privileges`

---

## Security

### Network Security

- **Tailscale** provides an encrypted, zero-trust private network. No ports are exposed to the public internet.
- The gateway binds to `127.0.0.1` only. The application **refuses to start** if `GATEWAY_BIND` is set to anything else.
- CORS is restricted to `http://127.0.0.1`.

### Authentication & Authorization

- All inbound requests require a `Bearer` token matching `GATEWAY_SECRET` (HMAC-compared to prevent timing attacks).
- Only the configured `SIGNAL_OWNER_NUMBER` is authorized to send commands. All other senders are rejected with 403.
- The Signal client **refuses to send** messages to any number other than the owner's.

### Prompt Injection Defense

Multiple layers in `app/sanitize.py`:

- **Pattern filtering** — known injection patterns (e.g., "ignore previous instructions", "system:", "ADMIN OVERRIDE") are replaced with `[FILTERED]`
- **XML wrapping** — user input is enclosed in `<user_request>` tags with explicit instructions that the content is data, not instructions
- **Skill content isolation** — skill files are wrapped in `<skill_content>` tags with similar instructions
- **Fetched content labeling** — all web-fetched and attachment content is explicitly labeled as DATA
- **Input truncation** — user input capped at 4,000 chars; messages capped at 4,000 chars at the gateway level

### SSRF Protection

The `web_fetch` tool (`app/tools/web_fetch.py`) implements comprehensive SSRF protection:

- Blocks internal hostnames: `localhost`, `chromadb`, `gateway`, `docker-proxy`, metadata endpoints
- Resolves DNS and blocks private, loopback, link-local, and reserved IP addresses
- Only allows `http` and `https` schemes
- Validates final URL after redirects (prevents redirect-based SSRF)
- All blocked requests are audit-logged

### Sandbox Isolation

See [Sandbox Security](#sandbox-security) above. The Docker socket proxy (`tecnativa/docker-socket-proxy`) further limits exposure:

- Only `CONTAINERS` and `POST` operations allowed
- `IMAGES`, `NETWORKS`, `VOLUMES`, `AUTH`, `SECRETS`, `SWARM` all disabled
- Docker socket mounted read-only

### Audit Logging

Structured JSON audit log (`app/audit.py`) captures:

- `request_received` — inbound messages (sender redacted)
- `crew_dispatch` — crew dispatch decisions
- `tool_call` — agent tool invocations
- `tool_blocked` — blocked tool calls (SSRF, path traversal)
- `security_event` — unauthorized senders, rate limits, injection attempts
- `response_sent` — outbound responses

Logs are written to a rotating file (`workspace/audit.log`, 10 MB × 5 backups) and stdout for Docker log drivers. Phone numbers are always redacted (e.g., `+372***0500`).

---

## Memory & Persistence

### ChromaDB Vector Memory

Each agent crew has its own ChromaDB collection, plus a shared `team_shared` collection:

| Collection | Used By | Purpose |
|------------|---------|---------|
| `commander` | Commander | Routing context and history |
| `researcher` | Research crew | Research findings and sources |
| `coder` | Coding crew | Code patterns and solutions |
| `writer` | Writing crew | Writing context and references |
| `skills` | Self-improvement | Learned knowledge |
| `team_shared` | All agents | Cross-crew knowledge sharing |

Embeddings are generated locally using `all-MiniLM-L6-v2` (no external API calls). ChromaDB data persists in `workspace/memory/`.

### Conversation History

Recent message exchanges are stored in SQLite (`workspace/conversations.db`) using WAL mode for concurrent access. Sender phone numbers are stored as truncated HMAC-SHA256 hashes (using the gateway secret as the key) so raw numbers are never written to disk.

The last N exchanges (configurable, default 10) are injected into each new request so the LLM understands short/contextual replies like "yes", "try Python", or "what about last week?".

### Skill Files

Learned knowledge is saved as Markdown files in `workspace/skills/`. Each skill file follows a standard structure:

- **Key Concepts** — core ideas and definitions
- **Best Practices** — actionable guidance
- **Code Patterns** — reusable code examples (if applicable)
- **Sources** — URLs of referenced material

Skill file names (up to 20) are included in Commander routing prompts, and full content (up to 10 files, 1,500 chars each) is provided to crews.

### Cloud Backup

Optional git-based backup to a remote repository (e.g., GitHub):

- **Backed up:** `skills/`, `conversations.db`, `learning_queue.md`, `proposals/`, `error_journal.json`, `evolution_journal.json`
- **Not backed up:** `memory/` (ChromaDB — large, rebuilds over time), `output/` (transient), `audit.log` (operationally sensitive)

Set `WORKSPACE_BACKUP_REPO` in `.env` to enable. Supports HTTPS with PAT or SSH. On startup, the system restores from the remote. On the configured schedule (default: hourly), it commits and pushes changes.

---

## Monitoring Dashboard

The monitoring dashboard (`dashboard/public/index.html`) is a single-page application hosted on Firebase that provides real-time visibility into the agent team:

- **System status** — online/offline badge with heartbeat tracking
- **Crew cards** — live status for each crew (Commander, Research, Coding, Writing, Self-Improvement) with current task, ETA, and progress bars
- **Task table** — current and recent tasks with hierarchical sub-agent display
- **Improvement proposals** — pending, approved, and rejected proposals
- **Scheduled activities** — upcoming cron jobs (self-improvement, evolution, workspace sync)
- **Live activity feed** — real-time event stream (task starts, completions, failures)
- **Signal commands reference** — collapsible guide to all available commands

The dashboard connects directly to Firestore using the Firebase JS SDK with real-time `onSnapshot` listeners. Firestore security rules allow public reads but restrict writes to the backend (Admin SDK).

---

## API Rate Limiting

The system includes two layers of rate limiting:

1. **Signal message rate limit** (`app/security.py`) — max 30 messages per 10 minutes per sender. Prevents runaway loops or abuse. Thread-safe with a maximum of 1,000 tracked senders.

2. **LLM API rate throttle** (`app/rate_throttle.py`) — token bucket rate limiter that caps outgoing API requests (default: 3 RPM for Anthropic's free tier). Monkey-patches `litellm.completion` and `litellm.acompletion` to inject throttling before every API call. Also configures litellm's retry with exponential backoff (default: 5 retries, 15-second backoff) for transient 429 errors.

---

## Health Check

```bash
bash scripts/health_check.sh
```

Verifies:
- Gateway is responding on `127.0.0.1:8765/health`
- Sandbox Docker image is built (`crewai-sandbox:latest`)
- ChromaDB container is running
- signal-cli daemon is running
- `.env` is configured (not using placeholder values)
- Tailscale is connected

---

## Cost Estimate

Approximately **$20–70/month** for moderate use (10–20 tasks/day), primarily driven by LLM API costs:

- Commander routing uses `claude-opus-4-6` with a 512-token cap per classification
- Specialist crews use `claude-sonnet-4-6` with 4,096-token outputs
- Self-improvement and evolution loops add background API usage
- The rate throttle (default 3 RPM) naturally limits spend on lower-tier plans

---

## Development

### Key Source Files

| File | Responsibility |
|------|---------------|
| `app/main.py` | Application entry point — FastAPI app, lifespan hooks, scheduler, `/signal/inbound` endpoint |
| `app/config.py` | Pydantic settings with validation — all environment variables, scoped accessors |
| `app/agents/commander.py` | Core routing logic — LLM-based JSON classification, crew dispatch, special command handling |
| `app/crews/parallel_runner.py` | Shared thread pool — concurrent crew/sub-agent execution with error isolation |
| `app/tools/code_executor.py` | Docker sandbox — code execution with full security hardening |
| `app/self_heal.py` | Error journal, background diagnosis, auto-fix proposals |
| `app/evolution.py` | Autonomous evolution — metrics gathering, experiment execution, journal management |

### Adding a New Tool

1. Create a new file in `app/tools/`:
   ```python
   from crewai.tools import tool

   @tool("my_tool")
   def my_tool(arg1: str) -> str:
       """Tool description shown to the agent."""
       # Implementation
       return result
   ```

2. Import and add the tool to the relevant agent(s) in `app/agents/`:
   ```python
   from app.tools.my_tool import my_tool

   # In create_<agent>():
   tools=[my_tool, ...existing_tools...]
   ```

3. If the tool accesses external resources, add SSRF protection (see `web_fetch.py` for a pattern).
4. If the tool writes files, restrict paths to allowed directories (see `file_manager.py`).

### Adding a New Crew

1. Create `app/crews/my_crew.py`:
   ```python
   from crewai import Task, Crew, Process
   from app.agents.my_agent import create_my_agent
   from app.sanitize import wrap_user_input
   from app.firebase_reporter import crew_started, crew_completed, crew_failed
   from app.self_heal import diagnose_and_fix

   class MyCrew:
       def run(self, task_description: str, parent_task_id: str = None) -> str:
           task_id = crew_started("my_crew", f"My: {task_description[:100]}",
                                  eta_seconds=120, parent_task_id=parent_task_id)
           agent = create_my_agent()
           task = Task(
               description=f"...\n{wrap_user_input(task_description)}\n...",
               expected_output="...",
               agent=agent,
           )
           crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
           try:
               result = str(crew.kickoff())
               crew_completed("my_crew", task_id, result[:200])
               return result
           except Exception as exc:
               crew_failed("my_crew", task_id, str(exc)[:200])
               diagnose_and_fix("my_crew", task_description, exc)
               raise
   ```

2. Register the crew in `app/agents/commander.py`:
   - Add the crew name to the `ROUTING_PROMPT` (the `crew_name MUST be one of` list)
   - Add dispatch logic in `Commander._run_crew()`

3. Add a crew card to the dashboard by adding the name to the `CREWS` array in `dashboard/public/index.html`.

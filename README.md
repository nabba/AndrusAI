# CrewAI Agent Team

An autonomous, self-improving AI agent team built on CrewAI. Control it from your iPhone via Signal.

## Architecture

- **Commander** — Receives requests via Signal, decomposes tasks, delegates to specialist crews
- **Researcher** — Searches the web, reads articles, extracts YouTube transcripts
- **Coder** — Writes and executes code in a Docker sandbox
- **Writer** — Creates summaries, reports, and documentation
- **Self-Improver** — Learns new topics on a schedule, writes skill files

## Quick Start

```bash
cd ~/crewai-team
bash scripts/install.sh
```

The install script will:
1. Create a Python 3.11 virtual environment
2. Install all dependencies
3. Build the Docker sandbox image
4. Pull ChromaDB
5. Start Docker Compose services

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `BRAVE_API_KEY` | Brave Search API key (free at api.search.brave.com) |
| `SIGNAL_BOT_NUMBER` | Bot's dedicated phone number (+1XXXXXXXXXX) |
| `SIGNAL_OWNER_NUMBER` | Your iPhone number (+1XXXXXXXXXX) |
| `GATEWAY_SECRET` | Random 64-char string for gateway auth |
| `GATEWAY_PORT` | Gateway port (default: 8765) |
| `COMMANDER_MODEL` | LLM for Commander (default: claude-opus-4-6) |
| `SPECIALIST_MODEL` | LLM for specialists (default: claude-sonnet-4-6) |

## Signal Setup

1. Install signal-cli (requires Java 17+):
   ```bash
   signal-cli --version  # verify installed
   ```

2. Register your bot number:
   ```bash
   signal-cli -a +1XXXXXXXXXX register
   signal-cli -a +1XXXXXXXXXX verify YOUR_SMS_CODE
   ```

3. Start signal-cli daemon:
   ```bash
   signal-cli -a +1XXXXXXXXXX daemon --socket /tmp/signal-cli.sock
   ```

4. Start the forwarder:
   ```bash
   python signal/forwarder.py
   ```

## Tailscale Setup

1. Start and authenticate: `sudo tailscale up`
2. Install Tailscale on your iPhone (same account)
3. Expose gateway privately: `sudo tailscale serve --bg 8765`
4. **Never** use `tailscale funnel` (exposes to public internet)

## Usage from iPhone

Send Signal messages to the bot number:

| Command | What Happens |
|---------|-------------|
| Research AI agent frameworks | Commander dispatches Research Crew |
| Write a Python JSON parser | Commander dispatches Coding Crew |
| Summarize this YouTube video: URL | Researcher extracts transcript, Writer summarizes |
| learn Docker security | Adds topic to learning queue |
| show learning queue | Shows pending topics |
| run self-improvement now | Triggers immediate learning run |
| status | Reports system status |

## Security

- **Tailscale**: Zero public ports, encrypted private network
- **Sender auth**: Only your phone number is accepted
- **Rate limiting**: 30 messages per 10 minutes
- **Docker sandbox**: No network, read-only FS, dropped capabilities, non-root user
- **Prompt injection protection**: All fetched content treated as DATA, not instructions

## Health Check

```bash
bash scripts/health_check.sh
```

## Cost Estimate

~$20-70/month for moderate use (10-20 tasks/day), primarily LLM API costs.

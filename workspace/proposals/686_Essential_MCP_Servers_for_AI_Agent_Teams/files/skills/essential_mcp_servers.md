# Essential MCP Servers for AI Agent Teams

## Introduction
MCP (Model Context Protocol) servers extend AI agent capabilities by exposing external tools and data sources. Our platform includes `mcp_search_servers`, `mcp_add_server`, `mcp_list_servers`, and `mcp_remove_server` functions, but no servers are currently connected. Adding the following servers will enable critical workflows.

## Recommended Servers

| Server | Purpose | Install Command | Required Env Vars |
|---------|---------|----------------|-------------------|
| GitHub (e.g., `@anthropic/mcp-server-github`) | Access repos, issues, PRs; clone, commit, create PRs | `mcp_search_servers 'github' 1` then `mcp_add_server` | `GITHUB_TOKEN` |
| Slack (`@anthropic/mcp-server-slack`) | Send notifications, read channels, DM users | similar | `SLACK_BOT_TOKEN` |
| Google Calendar (`@anthropic/mcp-server-calendar`) | Create events, check schedules, send invites | similar | `GOOGLE_CALENDAR_CREDENTIALS` (JSON) |
| Notion (`@anthropic/mcp-server-notion`) | Read/write pages and databases for collaborative docs | similar | `NOTION_API_KEY` |
| PostgreSQL (`@anthropic/mcp-server-postgres`) | Run SQL queries, fetch data, execute transactions | similar | `POSTGRES_CONNECTION_STRING` |
| Redis (`@anthropic/mcp-server-redis`) | Cache lookups, pub/sub messaging | similar | `REDIS_URL` |
| Filesystem (`@anthropic/mcp-server-filesystem`) | Advanced file ops (beyond basic file_manager) | similar | none |
| Kubernetes (`@anthropic/mcp-server-kubernetes`) | Manage pods, deployments, services (if applicable) | similar | `KUBECONFIG` path |

## Installation Steps
1. Obtain API tokens/credentials for each service.
2. Run a search for the server name to get the exact package name: `mcp_search_servers '<service>' 1`.
3. Add the server via `mcp_add_server` with the name from search, original query, and env vars as `KEY=value` pairs.
4. Verify connection with `mcp_list_servers`.

## Usage by Crew
- **Research**: Use `web_search` + GitHub to find repo READMEs; use Notion to collect findings.
- **Coding**: Use GitHub MCP to clone repos, create branches, open PRs; use Database MCP for data analysis; use Filesystem for advanced file ops.
- **Writing**: Use Notion MCP to draft articles; use Slack MCP to send drafts for review.
- **Self-Improvement**: Use Calendar MCP to schedule retrospectives; use Slack for notifications.

## Security Notes
- Store all credentials in environment variables or a secrets manager. Never commit tokens.
- Use least-privilege tokens (e.g., fine-grained GitHub tokens with repo-only access).
- Rotate keys regularly.
- Remove unused MCP servers with `mcp_remove_server`.

## Example: Adding GitHub
```bash
mcp_search_servers 'github' 1  # returns name like '@anthropic/mcp-server-github'
mcp_add_server '@anthropic/mcp-server-github' 'github' 'GITHUB_TOKEN=ghp_xxx'
```
# MCP Server Integration Playbook

## Overview
MCP (Model Context Protocol) servers extend the team's capabilities by connecting to external services and APIs. The team has tools to search and add MCP servers but needs guidance on strategic integration.

## When to Add an MCP Server

### High-Value Additions for This Team

1. **Database Connectivity**
   - Server: `neon` or `Supabase`
   - Use when: Research requires querying structured data, storing findings, or building data pipelines
   - Adds: SQL execution, schema inspection, migrations

2. **PDF Generation**
   - Server: `gen-pdf/mcp` or `pdf-generator-api/mcp-server`
   - Use when: Delivering research reports, creating presentations, generating invoices
   - Adds: Markdown-to-PDF, templates, encryption

3. **Email & Notifications**
   - Server: `gmail` or `jtalk22/slack-mcp-server`
   - Use when: Sending deliverables, alerts, status updates to stakeholders
   - Adds: Send/read emails, Slack messaging, thread management

4. **Advanced Web Scraping**
   - Server: `browserless/browserless-mcp` or `Decodo/decodo-mcp-server`
   - Use when: JavaScript-heavy sites, anti-bot protections, geo-restricted content
   - Adds: Screenshot capture, PDF conversion, bypass protections

## Integration Workflow

### Step 1: Discovery
```
Use mcp_search_servers with relevant keywords:
- "database postgres" for data storage
- "email gmail" for communications
- "pdf" for document generation
- "slack" for team notifications
```

### Step 2: Evaluation
Check:
- Installation count (higher = more reliable)
- Remote availability (no local install needed)
- Required environment variables (API keys needed)

### Step 3: Addition
```
Use mcp_add_server:
- name: exact server name from search results
- query: original search query (for re-finding if needed)
- env_vars: required credentials as "KEY=value;KEY2=value2"
```

### Step 4: Verification
```
Use mcp_list_servers to confirm connection and see available tools
```

## Common Patterns

### Pattern 1: Research → Database → Report
1. Add `neon` MCP server with database credentials
2. Store research findings in PostgreSQL tables
3. Query aggregated data for analysis
4. Generate PDF report with `gen-pdf/mcp`

### Pattern 2: Automated Notifications
1. Add `gmail` or `slack` MCP server
2. After completing research tasks, send summary to stakeholders
3. Include links to findings and key insights

### Pattern 3: Web Scraping Pipeline
1. Use `browserless/browserless-mcp` for JS-heavy pages
2. Store extracted content in database via `neon`
3. Process and analyze with code_executor
4. Deliver as PDF via `gen-pdf/mcp`

## Credential Management

### Environment Variables Needed
- **Neon**: `NEON_API_KEY`, `NEON_PROJECT_ID`
- **Gmail**: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- **Slack**: `SLACK_BOT_TOKEN`
- **Supabase**: `SUPABASE_URL`, `SUPABASE_ANON_KEY`

### Security Notes
- Never commit credentials to version control
- Use environment variables exclusively
- Rotate keys periodically
- Limit scopes to minimum required permissions

## Troubleshooting

### Server Won't Connect
1. Verify API keys are correct
2. Check if service is experiencing outages
3. Ensure network allows outbound connections

### Tools Not Appearing
1. Run mcp_list_servers to check status
2. Remove and re-add the server if stuck
3. Check server documentation for prerequisites

## Recommended First Additions

Priority order for this team:
1. `neon` - Enable database-backed research storage
2. `gen-pdf/mcp` - Enable professional report delivery
3. `gmail` - Enable stakeholder communication

---
*This skill should be updated as new high-value MCP servers become available.*
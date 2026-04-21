# MCP Server Integration Pattern

## Purpose
Enable on-demand capability extension through MCP (Model Context Protocol) servers.

## Available MCP Functions
- `mcp_search_servers`: Find servers by capability keyword
- `mcp_add_server`: Connect a discovered server
- `mcp_list_servers`: View connected servers
- `mcp_remove_server`: Disconnect a server

## Discovery Workflow

### Step 1: Identify Needed Capability
Common capability keywords:
- **Data**: `database postgres`, `database mysql`, `mongodb`, `redis`
- **Documents**: `pdf`, `google docs`, `markdown`
- **Communication**: `slack`, `discord`, `email`, `twilio`
- **Productivity**: `google calendar`, `notion`, `trello`
- **Development**: `github`, `gitlab`, `docker`
- **AI/ML**: `huggingface`, `openai`, `langchain`
- **Finance**: `stripe`, `plaid`, `quickbooks`

### Step 2: Search for Servers
```
mcp_search_servers(query="slack communication", limit=5)
```

### Step 3: Evaluate Results
Check:
- ✓ Installation count (higher = more battle-tested)
- ✓ Remote availability (no local install needed)
- ✓ Documentation completeness
- ✓ Active maintenance

### Step 4: Add Server
```
mcp_add_server(
    name="slack",
    query="slack communication",
    env_vars="SLACK_BOT_TOKEN=xoxb-xxx;SLACK_APP_TOKEN=xapp-xxx"
)
```

### Step 5: Verify Connection
```
mcp_list_servers()
```

## Environment Variables
Most servers require API keys or tokens:
1. Obtain credentials from the service provider
2. Pass as semicolon-separated key=value pairs
3. Never hardcode - use environment injection

Common patterns:
- `API_KEY=sk-xxx`
- `ACCESS_TOKEN=ghp_xxx`
- `DATABASE_URL=postgres://user:pass@host/db`

## Recommended Servers by Crew

### Research Crew
- `googledocs`: Access Google Docs content
- `docfork/docfork`: Technical documentation search
- `text_to_pdf`: Convert findings to PDF

### Coding Crew
- `github`: Repository operations
- `docker`: Container management
- `prisma`: Database ORM operations

### Writing Crew
- `notion`: Publish to Notion workspaces
- `slack`: Share updates to channels
- `google calendar`: Schedule content

### Self-Improvement Crew
- `memory`: Persistent knowledge storage
- `sqlite`: Local database for patterns

## Error Handling
- **Connection failed**: Check env_vars format
- **Tool not found**: Server may not have loaded yet, wait and retry
- **Auth error**: Verify API keys have correct permissions
- **Rate limits**: Some servers have usage quotas

## Best Practices
1. Add servers only when needed (lazy loading)
2. Remove servers when task complete (resource cleanup)
3. Store API keys securely, not in code
4. Test with simple operations before complex workflows
5. Document which servers work well for future reference

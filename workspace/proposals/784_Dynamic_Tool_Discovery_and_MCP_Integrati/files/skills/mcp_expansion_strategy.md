# MCP Tool Expansion Strategy

## Trigger
When a task requires an external integration (e.g., GitHub, Slack, Postgres, Google Calendar) not present in `mcp_list_servers`.

## Protocol
1. **Analyze Gap**: Identify the specific missing capability (e.g., 'I need to read a GitHub Issue').
2. **Search**: Call `mcp_search_servers` using keywords related to the service.
3. **Evaluate**: Compare the returned server descriptions against the requirements.
4. **Implement**: Use `mcp_add_server` providing necessary API keys via `env_vars`.
5. **Verify**: Execute a test call to one of the new tools to confirm connectivity.
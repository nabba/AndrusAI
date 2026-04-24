# Database MCP Server Setup

## Why Needed
- Current agents cannot query internal databases directly
- Forces workarounds via web scraping or manual exports
- Blocks LLM-powered data analysis and BI automation

## Install
1. Search for servers: `mcp_search_servers("postgresql", 3)`
2. Add with credentials: `mcp_add_server(name, "postgresql", "DB_HOST=...")`

## Usage Examples
- Research crew: "Find all users created last month in the PostgreSQL DB"
- Coding crew: "Check SQLite schema and suggest index optimizations"
- Writing crew: "Generate SQL query documentation from actual database"

## Security Notes
- Use read-only credentials for research agents
- Store credentials in environment variables, not code
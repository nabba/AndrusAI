# Proposal #754: Add GitHub MCP Server for Version Control and Collaboration

**Type:** code  
**Created:** 2026-04-21T18:54:45.291476+00:00  

## Why this is useful

Problem: The team has Docker containerization skills but no integrated GitHub/Git tools to manage code repositories, handle pull requests, or collaborate on code. This creates a gap between development and deployment workflows. Solution: Add the official GitHub MCP server (https://server.smithery.ai/github/mcp) which provides tools for repository management, issue tracking, PR handling, webhook management, and Git operations. This enables the coding crew to commit work, review changes, and maintain proper version control. Requires GitHub personal access token with repo permissions.

## What will change

- Modifies `.env.example`

## Potential risks to other subsystems

- Uncategorised (.env.example): impact scope unclear
- Requires `docker compose up -d --build gateway` to take effect

## Files touched

- `.env.example`

## Original description

Problem: The team has Docker containerization skills but no integrated GitHub/Git tools to manage code repositories, handle pull requests, or collaborate on code. This creates a gap between development and deployment workflows. Solution: Add the official GitHub MCP server (https://server.smithery.ai/github/mcp) which provides tools for repository management, issue tracking, PR handling, webhook management, and Git operations. This enables the coding crew to commit work, review changes, and maintain proper version control. Requires GitHub personal access token with repo permissions.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 754` / `reject 754` via Signal.

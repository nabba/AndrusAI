# GitHub Integration for Version Control and Issue Tracking

## Problem
The team lacks version control for code, centralized task tracking, and a collaboration workflow. Improvements to tools and skills are not systematically recorded or reviewed.

## Solution: GitHub MCP Server
The GitHub MCP server provides full access to repository management, issues, pull requests, and actions.

## Setup
1. Search: `mcp_search_servers(query='github', limit=5)`
2. Add: `mcp_add_server(name='github', query='github', env_vars='GITHUB_TOKEN=your_personal_access_token')`
3. Ensure the token has `repo`, `issues`, `workflow` scopes.

## Key Workflows

### 1. Logging Tasks as Issues
When the self_improvement crew identifies a gap, create an issue:
```json
{
  'title': 'Add persistent data storage',
  'body': 'Current team lacks structured storage. Proposal: add Neon MCP server.',
  'labels': ['improvement', 'infrastructure']
}
```

### 2. Syncing Code Changes
Coding crew can push new tools automatically:
```bash
git add .
git commit -m 'feat: add Neon wrapper'
git push origin main
# Then open a PR via MCP for review
```

### 3. CI/CD Integration
Use GitHub Actions to run tests on new code before merging, ensuring stability.

## Best Practices
- Use a dedicated bot account with limited permissions.
- Protect the main branch; require PR reviews.
- Include clear templates for issues and PRs.
- Reference issues in commit messages (e.g., 'Closes #123').

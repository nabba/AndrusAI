# GitHub Repository Integration Skill

## Overview
This skill enables the team to interact with GitHub repositories for version control, issue tracking, pull request management, and collaborative development.

## Core Capabilities

### 1. Repository Operations
- Create, clone, and fork repositories
- List repository contents and metadata
- Search code across repositories
- Manage repository settings

### 2. Issue Management
```markdown
# Issue Template
**Title:** [Clear, concise description]
**Labels:** bug, enhancement, documentation
**Assignees:** @username
**Body:**
## Description
[What is the issue?]

## Steps to Reproduce (for bugs)
1. Step one
2. Step two

## Expected Behavior
[What should happen]

## Actual Behavior
[What actually happened]
```

### 3. Pull Request Workflow
- Create feature branches
- Open pull requests with descriptions
- Request reviews
- Merge PRs after approval
- Handle merge conflicts

### 4. Code Search Patterns
```
# Search syntax
filename:config.py language:python
extension:md "installation instructions"
repo:owner/name path:src/
```

## MCP Server Integration

### Connecting GitHub MCP Server
Use `mcp_add_server` with:
- Server name: `github`
- Query: `github git repository`
- Env vars: `GITHUB_TOKEN=ghp_your_token`

## Common Use Cases

### Create Issue from Research Finding
```json
{
  "title": "Research: Estonian Deforestation Trends 2024",
  "body": "## Summary\nKey findings from policy analysis...\n\n## Sources\n- [Source 1](url)\n- [Source 2](url)",
  "labels": ["research", "documentation"],
  "assignees": []
}
```

### Create Pull Request
```json
{
  "title": "Add new analysis module",
  "head": "feature/new-analysis",
  "base": "main",
  "body": "## Changes\n- Added analysis.py module\n- Updated documentation\n\n## Testing\n- All tests pass\n- Manual testing complete"
}
```

### Search Code Examples
```python
# Find all Python files with specific pattern
search_query = 'filename:*.py "def process_data"'
# Use MCP tools to execute search
```

## Workflow Patterns

### Feature Development Workflow
1. Create issue describing the feature
2. Create branch from main
3. Implement changes with commits
4. Open PR with description linking to issue
5. Request review
6. Address feedback
7. Merge when approved
8. Close issue

### Bug Fix Workflow
1. Create issue with reproduction steps
2. Create fix branch
3. Implement fix with test
4. Open PR
5. Verify fix resolves issue
6. Merge and close issue

## Best Practices
1. Write clear commit messages (imperative mood)
2. Keep PRs focused and small (<400 lines)
3. Link issues in PR descriptions (#issue-number)
4. Use descriptive branch names (feature/, fix/, docs/)
5. Request reviews from relevant team members
6. Don't force-push to shared branches

## Token Permissions Required
- `repo` - Full repository access
- `issues` - Issue management
- `pull_requests` - PR management
- `workflow` - GitHub Actions (if used)
# GitHub Integration for Agent Teams

## Overview
This skill enables AI agents to interact with GitHub repositories through the GitHub MCP server, providing version control capabilities essential for collaborative coding workflows.

## MCP Server Setup
Use `mcp_add_server` to connect:
```
Server: github
Query: github git version control
Env Vars: GITHUB_TOKEN=ghp_xxxx (Personal Access Token with repo, workflow scopes)
```

## Core Capabilities Unlocked
1. **Repository Management**
   - Create, clone, fork repositories
   - Manage branches and tags
   - View repository metadata and statistics

2. **File Operations**
   - Read files from any branch
   - Create/update/delete files with automatic commits
   - Search code across repositories

3. **Pull Request Workflow**
   - Create PRs with descriptions
   - List, review, and merge PRs
   - Add comments and reviews

4. **Issue Management**
   - Create, update, close issues
   - Add labels, assignees, milestones
   - Link issues to PRs

5. **Workflow Integration**
   - Trigger GitHub Actions
   - View workflow runs and logs
   - Manage secrets (with appropriate permissions)

## Agent Workflow Patterns

### Pattern 1: Code Contribution Flow
```
1. Fork target repository (if no write access)
2. Create feature branch
3. Make changes via file operations
4. Create pull request with description
5. Respond to review feedback
6. Merge when approved
```

### Pattern 2: Issue-Driven Development
```
1. Search issues by label/status
2. Assign self or team to issue
3. Create branch named after issue
4. Implement fix/feature
5. Create PR linking to issue (closes #123)
6. Update issue status
```

### Pattern 3: Code Review Automation
```
1. List open PRs
2. Fetch diff content
3. Analyze changes for issues
4. Post review comments
5. Approve or request changes
```

## Security Best Practices
- Use fine-grained personal access tokens with minimal required scopes
- Never expose tokens in code or commits
- Use repository secrets for sensitive CI/CD values
- Audit agent actions via GitHub audit log

## Common Use Cases for This Team

### Research Crew
- Clone research repositories
- Create issues for research tasks
- Store findings in repo wiki

### Coding Crew
- Full git workflow for code changes
- Automated testing via GitHub Actions
- Code review participation

### Writing Crew
- Manage documentation repositories
- Update README files
- Create release notes

### Self-Improvement Crew
- Track improvement proposals as issues
- Version control skill files
- Document team capabilities in repo

## Error Handling
- `403 Forbidden`: Token lacks required scope
- `404 Not Found`: Repository doesn't exist or no access
- `422 Validation Failed`: Branch name conflict or invalid input
- Rate limits: 5000 requests/hour for authenticated requests

## Integration with Existing Tools
- Use with `code_executor` to test before committing
- Use with `web_search` to research before PR creation
- Use with `file_manager` to prepare files for commit

## Example Commands
```python
# After adding MCP server, agents can:
# - List repos: github_list_repositories
# - Get file: github_get_file_contents(owner, repo, path, branch)
# - Create file: github_create_file(owner, repo, path, content, message, branch)
# - Create PR: github_create_pull_request(owner, repo, title, head, base, body)
# - Create issue: github_create_issue(owner, repo, title, body, labels)
```
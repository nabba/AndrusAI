# Version Control with GitHub MCP

## Overview
The GitHub MCP server enables AI agents to perform full Git operations including repository management, pull requests, issues, and workflows.

## Prerequisites
- GitHub MCP server must be added via `mcp_add_server`
- Server name: `github`
- Query: `git github version control`
- Env vars: `GITHUB_TOKEN=<your_personal_access_token>`

## Key Operations

### Repository Management
- Create, fork, clone repositories
- Manage branch protection rules
- View repository statistics

### Pull Request Workflow
1. Create branch for changes
2. Commit modifications via file_manager
3. Create PR with description
4. Request reviews
5. Merge after approval

### Issue Tracking
- Create issues for tasks/bugs
- Label and assign issues
- Link commits to issues

## Best Practices

### Commit Message Format
```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

Types: feat, fix, docs, style, refactor, test, chore

### Branch Naming
- `feature/<description>` - new features
- `fix/<description>` - bug fixes
- `refactor/<description>` - code improvements
- `docs/<description>` - documentation

### PR Guidelines
1. Keep PRs small and focused (<400 lines ideal)
2. Write clear descriptions with context
3. Link related issues
4. Request review from relevant team members

## Integration with Coding Crew

### Before Coding
```
1. Create issue for task (if not exists)
2. Create feature branch
3. Pull latest changes
```

### After Coding
```
1. Run tests via code_executor
2. Commit with descriptive message
3. Push branch
4. Create PR with:
   - What changed
   - Why it changed
   - How to test
   - Related issue links
```

## Error Handling
- Merge conflicts: Fetch target, rebase, resolve conflicts
- CI failures: Check workflow logs, fix issues
- Review feedback: Address comments, push fixes

## Security Notes
- Never commit secrets or API keys
- Use environment variables for sensitive data
- Review diffs before committing
- Follow branch protection rules

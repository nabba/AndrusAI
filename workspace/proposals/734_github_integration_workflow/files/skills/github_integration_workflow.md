# GitHub Integration Workflow

## Purpose
Enable coding crew to manage code repositories, track changes, and collaborate through version control.

## MCP Server Setup
```
Server: github (5,165 installations)
Remote: https://server.smithery.ai/github/mcp
Capabilities: repo management, issues, PRs, workflows, branch operations
```

## Workflow Steps

### 1. Repository Initialization
```
1. Check if repository exists via github MCP
2. If new project: create repo with appropriate .gitignore
3. Clone to workspace directory
4. Initialize branch strategy (main/develop)
```

### 2. Code Commit Workflow
```
1. Stage changes: git add equivalent via MCP
2. Generate meaningful commit message from diff analysis
3. Commit with author attribution
4. Push to remote branch
```

### 3. Pull Request Process
```
1. Create feature branch from develop
2. Implement changes with tests
3. Create PR with:
   - Auto-generated description from commits
   - Test coverage summary
   - Code review checklist
4. Request review (if team available)
```

### 4. Issue Tracking Integration
```
- Create issues for discovered bugs
- Link commits to issues
- Close issues automatically when fixed
- Tag with appropriate labels
```

## Agent Instructions
When coding crew completes a task:
1. Always commit working code before marking complete
2. Include tests in same commit when applicable
3. Update README/docs in separate commit
4. Reference any relevant issues in commit messages

## Error Handling
- Merge conflicts: Alert and provide diff analysis
- Auth failures: Prompt for credential refresh
- Rate limits: Implement backoff and queue

## Metrics to Track
- Commits per session
- PR merge rate
- Issue resolution time
- Code churn (files modified frequently)
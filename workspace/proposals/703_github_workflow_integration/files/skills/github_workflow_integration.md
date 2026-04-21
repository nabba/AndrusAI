# GitHub Workflow Integration Skill

## Purpose
Enable the team to interact with GitHub repositories for full software development lifecycle support.

## Gap Addressed
The coding crew has `code_executor` but no version control integration. Cannot push code, create PRs, manage issues, or participate in collaborative development.

## Required MCP Server
Use `mcp_add_server` with:
- name: `github`
- query: `github repository`

## Core Capabilities Gained
1. **Repository Management**
   - Create, fork, clone repositories
   - Manage branches and protections
   - View repository stats and insights

2. **Issue Tracking**
   - Create, update, close issues
   - Assign labels, milestones, assignees
   - Search issues across repositories

3. **Pull Request Workflow**
   - Create PRs from branches
   - Request reviews
   - Merge, close, or convert to draft
   - View diff and changes

4. **Code Review**
   - Add review comments
   - Approve or request changes
   - View PR status and checks

5. **Workflow Integration**
   - Trigger GitHub Actions
   - View workflow runs and logs
   - Manage secrets and variables

## Common Workflow Patterns

### Pattern 1: Code Contribution
```
1. Fork repository
2. Create feature branch
3. Write code using code_executor
4. Commit and push changes
5. Create pull request
6. Address review feedback
7. Merge when approved
```

### Pattern 2: Bug Triage
```
1. Search issues by label/status
2. Comment on issue with analysis
3. Create branch with fix
4. Link PR to issue (closes #123)
5. Request review from maintainers
```

### Pattern 3: Documentation Update
```
1. Clone wiki or docs folder
2. Edit markdown files
3. Commit with descriptive message
4. Push and verify rendering
```

## Authentication Requirements
- GitHub Personal Access Token (PAT)
- Required scopes: `repo`, `workflow`, `read:org`
- Set via env_vars when adding server

## Integration with Existing Tools
- Use `code_executor` to write/test code locally
- Use `file_manager` to stage changes
- Use GitHub MCP to push and manage PRs
- Use `web_search` to research issues before fixing

## Error Handling
- Handle rate limiting (5000 requests/hour)
- Check branch protection rules before pushing
- Verify PR mergeability before requesting review
- Handle merge conflicts with branch sync

## Best Practices
1. Always create feature branches (never push to main)
2. Write descriptive commit messages
3. Link issues to PRs using keywords
4. Request reviews from appropriate maintainers
5. Run tests locally before pushing
6. Keep PRs focused and small

# GitHub Integration Workflows

## Overview
This skill enables AI agents to interact with GitHub repositories for version control, collaboration, and project management.

## MCP Server Required
Add the GitHub MCP server:
```
mcp_add_server(name="github", query="github git version control", env_vars="GITHUB_TOKEN=your_token_here")
```

## Common Workflows

### 1. Repository Operations
- Clone or access existing repositories
- Create new repositories
- List branches and commits

### 2. Branch Management
- Create feature branches
- Push changes to branches
- Delete merged branches

### 3. Pull Request Workflow
- Create pull requests from branches
- Request reviews
- Merge PRs after approval
- Handle merge conflicts

### 4. Issue Management
- Create issues for bugs/features
- Label and assign issues
- Close issues when resolved
- Link issues to PRs

### 5. Code Review
- Review PR diffs
- Add comments to specific lines
- Approve or request changes

## Best Practices
1. Always create a feature branch before making changes
2. Write descriptive commit messages
3. Link PRs to relevant issues
4. Request reviews from appropriate team members
5. Delete branches after merging

## Error Handling
- Handle authentication errors gracefully
- Check for merge conflicts before PR creation
- Validate file paths before operations
- Rate limit awareness for API calls

## Integration with Coding Crew
The coding crew should use GitHub MCP for:
- Persisting generated code
- Versioning iterative improvements
- Collaborating on complex solutions
- Tracking changes to agent-generated projects
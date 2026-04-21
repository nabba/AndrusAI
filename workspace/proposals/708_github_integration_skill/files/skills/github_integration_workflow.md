# GitHub Integration Workflow

## Overview
This skill enables the team to interact with GitHub repositories, enabling version control operations without leaving the conversation.

## MCP Server Required
Add the GitHub MCP server:
```
mcp_add_server(name="github", query="github git repository", env_vars="GITHUB_TOKEN=<your-token>")
```

## Common Operations

### Repository Management
- List repositories for a user/org
- Create new repositories
- Fork existing repositories
- Get repository details and statistics

### Issue Operations
- Create issues with labels and assignees
- List issues by state (open/closed), labels, or assignee
- Update issue status and properties
- Add comments to issues

### Pull Request Workflow
- Create pull requests from branches
- List PRs by state, author, or reviewer
- Request reviews from team members
- Merge PRs when approved
- Add review comments

### Code Search
- Search code across repositories
- Find file contents by pattern
- Locate function/class definitions

## Workflow Patterns

### Pattern 1: Code Contribution
1. Fork target repository
2. Create feature branch
3. Make changes via code_executor
4. Create PR with description
5. Request review

### Pattern 2: Issue Triage
1. List open issues for repository
2. Filter by labels or priority
3. Add comments or close duplicates
4. Assign to appropriate team members

### Pattern 3: Code Investigation
1. Search for function/variable name
2. Navigate to file locations
3. Read file contents
4. Trace usage patterns across codebase

## Best Practices
- Always use descriptive commit messages
- Reference issue numbers in PR descriptions
- Use draft PRs for work-in-progress
- Request reviews from domain experts
- Keep PRs focused on single concerns

## Error Handling
- Check repository permissions before operations
- Verify branch exists before creating PR
- Handle merge conflicts by reading both versions
- Use drafts for incomplete work to avoid premature merges

# GitHub Version Control Workflow

## Problem Statement
The coding crew can execute code but lacks version control capabilities:
- No ability to commit code changes
- No branch management or pull request workflows
- No issue tracking integration
- No CI/CD pipeline interaction
- Cannot collaborate on codebases with version history

## When to Use This Skill
- User wants to commit code to a repository
- Task involves pull request review or creation
- Need to track issues or project management on GitHub
- CI/CD pipeline interaction required
- Collaborative coding with version history

## Available MCP Server

**GitHub MCP** - Connect to manage repos, issues, PRs, workflows
- Used by: 5,165+ installations
- Remote: Ready to add without local install

Add with:
```
mcp_add_server(
    name="github",
    query="github git version control",
    env_vars="GITHUB_TOKEN=ghp_xxx"
)
```

## Git Workflow Patterns

### Feature Branch Workflow
```
main ─────●─────●─────●─────●─────>
           \               /
feature     ●───●───●───●   (merge PR)
```

1. Create branch from main
2. Make commits on feature branch
3. Open pull request
4. Review and discuss
5. Merge to main

### Commit Message Convention
```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `refactor`: Code restructuring
- `test`: Adding tests
- `chore`: Maintenance

Example:
```
feat(api): add user authentication endpoint

- Implement JWT token generation
- Add login/logout routes
- Include rate limiting

Closes #123
```

## Workflow for Coding Tasks

### Without GitHub MCP (Local Only)
1. Use code_executor for development
2. Use file_manager to save files
3. Export code as downloadable files
4. User must manually commit

### With GitHub MCP Connected
1. Clone or create repository
2. Create feature branch
3. Commit changes with proper messages
4. Open pull request if needed
5. Respond to review feedback
6. Merge when approved

## Common Operations

### Creating a Repository
```
gh repo create project-name --public --description "Project description"
```

### Branch Management
```
git checkout -b feature/new-feature
git push -u origin feature/new-feature
```

### Pull Request Creation
```
gh pr create --title "feat: add new feature" --body "Description of changes"
```

### Issue Management
```
gh issue create --title "Bug in login" --body "Description"
gh issue list --state open
```

## CI/CD Integration

When GitHub MCP is connected:
1. View workflow runs
2. Check build status
3. Review deployment status
4. Trigger manual workflows

## Security Best Practices

1. **Never commit secrets** - Use environment variables
2. **Use .gitignore** - Exclude sensitive files
3. **Review PRs carefully** - Check for exposed credentials
4. **Use branch protection** - Require reviews for main branch

## Requesting GitHub MCP Addition

When a task requires version control:

1. Explain the capability gap
2. Ask user for GitHub token (classic or fine-grained)
3. Connect using `mcp_add_server`
4. Proceed with git operations

Example:
> "This task requires committing code to GitHub. I can connect our team to GitHub for repository management. Please provide a GitHub personal access token with appropriate permissions."

## Token Permissions Required

For full functionality, the token needs:
- `repo` - Full repository access
- `workflow` - CI/CD operations
- `read:org` - Organization access
- `write:discussion` - Discussions

## Current Limitations

Without GitHub MCP:
- Can only develop code locally
- Must export files for user to commit manually
- No issue tracking
- No PR workflow
- No CI/CD visibility

With GitHub MCP:
- Full repository management
- Automated commits and PRs
- Issue tracking
- CI/CD integration
- Team collaboration

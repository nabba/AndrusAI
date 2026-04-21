# GitHub Version Control Integration

## Problem
The coding crew lacks version control capabilities:
- Cannot commit code changes
- Cannot create pull requests
- Cannot manage issues or branches
- Cannot push to repositories
- Cannot review code changes

This severely limits software development tasks.

## Solution: MCP GitHub Server

The team can connect to the official GitHub MCP server which provides comprehensive GitHub API access.

### Adding the GitHub MCP Server
```
Use mcp_add_server with:
- name: 'github'
- query: 'github git version control'
- env_vars: 'GITHUB_TOKEN=ghp_your_personal_access_token'
```

The GitHub MCP server (5,165+ installations) provides tools for:
- Repository management (create, fork, clone)
- File operations (create, update, delete files)
- Pull requests (create, review, merge)
- Issues (create, comment, label, close)
- Branch management
- Workflow triggering

### Alternative: Git via code_executor

For Git operations without MCP, use command-line Git in code_executor:

```python
import subprocess

def git_command(cmd, cwd='/app/workspace'):
    result = subprocess.run(
        f'git {cmd}',
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    return result.stdout, result.stderr, result.returncode

# Clone repository
stdout, stderr, code = git_command('clone https://github.com/user/repo.git')

# Stage and commit
git_command('add .')
git_command('commit -m "Your commit message"')

# Push (requires configured credentials)
git_command('push origin main')
```

### Git Configuration for Commits
```python
# Set user identity (required for commits)
git_command('config user.name "AI Agent"')
git_command('config user.email "agent@example.com"')
```

### Common Workflows

#### 1. Create a Branch and PR
```python
# Create feature branch
git_command('checkout -b feature/new-feature')

# Make changes, then commit
git_command('add .')
git_command('commit -m "Add new feature"')

# Push branch
git_command('push -u origin feature/new-feature')

# PR creation requires GitHub API (use MCP or requests)
```

#### 2. Sync Fork
```python
git_command('fetch upstream')
git_command('checkout main')
git_command('merge upstream/main')
git_command('push origin main')
```

## Recommended Setup

1. **Primary**: Add GitHub MCP server for full API access
2. **Fallback**: Use Git CLI via code_executor for basic operations

## Required Credentials
- GitHub Personal Access Token (PAT) with appropriate scopes
- For SSH: SSH key configured in the environment

## Security Notes
- Never commit secrets or API keys
- Use environment variables for sensitive data
- Review changes before committing
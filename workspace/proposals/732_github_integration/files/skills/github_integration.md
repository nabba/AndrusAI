# GitHub Integration Skill

## Purpose
Enable version control, repository management, and collaborative coding through GitHub.

## MCP Server Required
Add the `github` MCP server for full GitHub API access.

## Core Capabilities

### 1. Repository Management
- Create new repositories for projects
- Clone/fork existing repositories
- Manage repository settings and branches

### 2. Code Version Control
- Commit code changes with meaningful messages
- Create and manage branches
- Handle merge conflicts resolution

### 3. Pull Request Workflow
- Create pull requests for code reviews
- Review and comment on PRs
- Merge approved changes

### 4. Issue Tracking
- Create issues for bugs and features
- Label and assign issues
- Link commits to issues

## Workflow Integration

### Coding Crew Workflow
1. **Before starting**: Check if repo exists, create if needed
2. **During development**: Create feature branch
3. **After completion**: Commit changes, create PR
4. **Documentation**: Update README and docs

### Self-Improvement Crew
1. Track improvement proposals as GitHub issues
2. Link skill development to commits
3. Maintain changelog through PR descriptions

### Research Crew
1. Store research code in versioned repositories
2. Share analysis scripts across team
3. Track methodology changes over time

## Example Use Cases

### Create Project Repository
```
Create new repository 'estonian-policy-analyzer'
with README.md and .gitignore for Python project
```

### Commit Analysis Code
```
Stage: analysis/deforestation_trends.py
Commit message: 'Add deforestation rate calculation with visualization'
Push to: main branch
```

### Create Pull Request
```
From: feature/vector-optimization
To: main
Title: 'Optimize vector database queries for faster retrieval'
Description: Include benchmark results and test coverage
```

## Best Practices

### Commit Message Format
```
<type>: <description>

[optional body]

Types: feat, fix, docs, refactor, test, chore
```

### Branch Naming
- `feature/<description>` - new features
- `fix/<description>` - bug fixes
- `research/<description>` - analysis code
- `skill/<description>` - new skills

## Security Considerations
- Never commit API keys or secrets
- Use .gitignore for sensitive files
- Review diffs before committing

## Recommended MCP Server
Use `github` MCP server for complete GitHub integration.
Command: `mcp_add_server` with name='github'

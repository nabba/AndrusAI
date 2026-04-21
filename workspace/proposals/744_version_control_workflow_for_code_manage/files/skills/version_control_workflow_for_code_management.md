# Version Control Workflow for Code Management

## Problem Statement
The coding crew lacks version control capabilities:
- Generated code exists only in ephemeral sandboxes
- No way to commit, branch, or manage code history
- Cannot create pull requests or participate in code review
- No collaboration with human developers via Git platforms
- Code improvements cannot be tracked over time

## Solution: GitHub MCP Server Integration

### Available MCP Servers
From MCP registry search:

1. **github** (Official) - 5,165 installations
   - Repository management
   - Issues and PRs
   - Workflows and Actions
   - Search functionality

2. **smithery-ai/github** - 2,859 installations
   - File operations
   - Repository management
   - Search functionality

3. **gitlab** - 1,308 installations
   - Full DevOps lifecycle
   - CI/CD pipelines
   - Wiki and issue tracking

### Recommended Implementation

```
mcp_add_server(name="github", query="github git version control", env_vars="GITHUB_TOKEN=ghp_...")
```

## Git Workflow Patterns

### 1. Feature Branch Workflow
```
main (protected)
├── feature/research-analysis
├── fix/container-timeout
└── refactor/code-optimizer
```

### 2. Commit Message Convention
```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

Types:
- `feat`: New capability
- `fix`: Bug fix
- `docs`: Documentation
- `refactor`: Code restructuring
- `test`: Adding tests
- `chore`: Maintenance

### 3. Pull Request Template
```markdown
## Changes
- Brief description of changes

## Testing
- How changes were tested

## Related Issues
- Closes #<issue-number>
```

## Usage Examples

### Initialize Repository for Generated Code
```python
# Create repository for agent-generated tools
gh repo create agent-tools --public --clone
git add skills/
git commit -m "feat(skills): add database integration skill"
git push origin main
```

### Create Feature Branch for New Tool
```python
git checkout -b feature/data-visualizer
git add tools/data_visualizer.py
git commit -m "feat(tools): add data visualization tool"
git push origin feature/data-visualizer
gh pr create --title "Add Data Visualizer Tool" --body "Enables chart generation from research data"
```

### Respond to Code Review
```python
git checkout feature/data-visualizer
# Make requested changes
git add .
git commit -m "refactor: improve error handling per review"
git push origin feature/data-visualizer
```

## Automation Opportunities

### 1. Auto-commit on Successful Execution
```python
# After code_executor runs successfully
if execution.success:
    git.add(changed_files)
    git.commit(f"auto: generated {tool_name}")
    git.push()
```

### 2. PR from Skill Generation
```python
# After generating new skill
git checkout -b skill/{skill_name}
git add(f"skills/{skill_name}.md")
git.commit(f"feat(skill): add {skill_name}")
gh.pr.create(
    title=f"Add {skill_name} skill",
    body=f"## Description\n{skill_description}"
)
```

### 3. Issue Tracking Integration
```python
# Create issue for identified gap
gh.issue.create(
    title="Missing database integration",
    body="The team lacks persistent storage capabilities",
    labels=["enhancement", "priority-high"]
)
```

## Workflow Integration with Crews

### Research Crew
- Commit research findings as JSON/Markdown
- Create issues for research gaps
- Tag releases with research milestones

### Coding Crew
- All generated code in Git repositories
- PRs for code review before deployment
- Branch per tool/feature

### Writing Crew
- Version-controlled document drafts
- PRs for human review
- Changelog generation

## Security Considerations

- Use fine-grained personal access tokens (PATs)
- Limit token scope to specific repositories
- Never commit secrets (use environment variables)
- Review PRs before merging to main

## Benefits

- **Traceability**: Full history of code changes
- **Collaboration**: Work with human developers
- **Rollback**: Revert problematic changes
- **Documentation**: Commits document intent
- **CI/CD Integration**: Trigger workflows on push

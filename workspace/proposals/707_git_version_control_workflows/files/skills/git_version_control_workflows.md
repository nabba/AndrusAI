# Git Version Control Workflows

## Overview
Git is essential for tracking code changes, collaborating, and maintaining code history. This skill covers common git operations, branching strategies, and collaborative workflows.

## Essential Commands

### Repository Setup
```bash
# Initialize new repository
git init

# Clone existing repository
git clone https://github.com/user/repo.git
git clone git@github.com:user/repo.git  # SSH

# Clone specific branch
git clone -b branch_name https://github.com/user/repo.git

# Add remote to existing repo
git remote add origin https://github.com/user/repo.git
git remote -v  # Verify
```

### Basic Workflow
```bash
# Check status
git status
git status -s  # Short format

# Stage changes
git add filename.py
git add .  # All changes
git add -A  # All changes including deletions
git add -p  # Interactive staging

# Commit
 git commit -m "feat: add user authentication module"
git commit -am "fix: resolve null pointer in login"  # Add and commit tracked files

# View history
git log --oneline -10
git log --graph --oneline --all
git show HEAD  # Show last commit
```

### Branching
```bash
# Create and switch branches
git branch feature-login  # Create
git checkout feature-login  # Switch
git checkout -b feature-login  # Create and switch

# List branches
git branch -a  # All branches (local and remote)
git branch -r  # Remote only

# Delete branches
git branch -d feature-login  # Safe delete (merged only)
git branch -D feature-login  # Force delete

# Push and track
git push -u origin feature-login  # Push and set upstream
```

### Merging and Rebasing
```bash
# Merge
git checkout main
git merge feature-login

# Merge with no fast-forward (preserves history)
git merge --no-ff feature-login

# Abort merge if conflicts
git merge --abort

# Rebase
git checkout feature-login
git rebase main

# Continue after resolving conflicts
git rebase --continue
git rebase --abort  # Cancel rebase
```

### Remote Operations
```bash
# Fetch (download without merging)
git fetch origin
git fetch --all

# Pull (fetch + merge)
git pull origin main
git pull --rebase origin main  # Rebase instead of merge

# Push
git push origin main
git push -f origin main  # Force push (use carefully!)
git push --set-upstream origin new-branch  # First push of new branch

# Delete remote branch
git push origin --delete old-branch
```

### Undo Operations
```bash
# Undo last commit (keep changes)
git reset --soft HEAD~1

# Undo last commit (discard changes)
git reset --hard HEAD~1

# Create undo commit (safe, preserves history)
git revert HEAD
git revert <commit-hash>

# Undo staged changes
git restore --staged filename.py
git reset HEAD filename.py  # Older syntax

# Discard working directory changes
git restore filename.py
git checkout -- filename.py  # Older syntax

# Stash changes
git stash
git stash push -m "work in progress on feature"
git stash list
git stash pop  # Apply and remove
git stash apply stash@{0}  # Apply without removing
```

### Diff and Inspection
```bash
# View differences
git diff  # Unstaged changes
git diff --staged  # Staged changes
git diff HEAD~1  # Compared to last commit
git diff main..feature-branch  # Between branches

# Show file at specific commit
git show HEAD~2:path/to/file.py

# Who changed what line
git blame filename.py
git blame -L 10,20 filename.py  # Specific lines

# Find commit that introduced bug
git bisect start
git bisect bad HEAD
git bisect good v1.0.0
# Git will checkout commits; mark each:
git bisect good  # or git bisect bad
```

## Commit Message Conventions

### Conventional Commits Format
```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Formatting, no code change
- `refactor`: Code restructuring
- `test`: Adding/updating tests
- `chore`: Build process, dependencies
- `perf`: Performance improvement
- `ci`: CI/CD configuration

### Examples
```bash
git commit -m "feat(auth): add OAuth2 login support"
git commit -m "fix(api): resolve timeout issue in user endpoint"
git commit -m "docs(readme): update installation instructions"
git commit -m "refactor(utils): simplify date parsing logic"
```

## Branching Strategies

### Git Flow
```
main (production)
├── develop (integration)
│   ├── feature/user-auth
│   ├── feature/dashboard
│   ├── release/1.0.0
│   └── hotfix/critical-bug
```

### GitHub Flow (simpler)
```
main (always deployable)
├── feature/login
├── feature/api-v2
└── bugfix/memory-leak
```

### Trunk-Based Development
```
main (all work happens here)
├── short-lived-feature-1 (deleted after merge)
└── short-lived-feature-2
```

## Collaborative Workflows

### Pull Request Workflow
```bash
# Create feature branch
git checkout -b feature/new-endpoint

# Make changes and commit
git add .
git commit -m "feat(api): add user profile endpoint"

# Push to remote
git push -u origin feature/new-endpoint

# Create PR via GitHub CLI (if available)
gh pr create --title "Add user profile endpoint" --body "Description..."

# After review, merge and cleanup
git checkout main
git pull
git branch -d feature/new-endpoint
git push origin --delete feature/new-endpoint
```

### Syncing Fork
```bash
# Add upstream remote
git remote add upstream https://github.com/original/repo.git

# Fetch and merge upstream
git fetch upstream
git checkout main
git merge upstream/main

# Push to fork
git push origin main
```

## Git Configuration

```bash
# Set identity
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Useful aliases
git config --global alias.co checkout
git config --global alias.br branch
git config --global alias.ci commit
git config --global alias.st status
git config --global alias.lg "log --oneline --graph --all"
git config --global alias.unstage "reset HEAD --"

# Default branch name
git config --global init.defaultBranch main

# Pull behavior
git config --global pull.rebase false  # Merge (default)
```

## Common Issues and Solutions

### Detached HEAD
```bash
# Create branch to save work
git checkout -b save-my-work

# Or discard and return to main
git checkout main
```

### Accidentally Committed to Wrong Branch
```bash
git reset --soft HEAD~1  # Undo commit, keep changes
git stash  # Save changes
git checkout correct-branch
git stash pop  # Restore changes
git add . && git commit -m "message"
```

### Merge Conflicts
```bash
# View conflicting files
git status

# Edit file to resolve conflicts (look for <<<<<<< ======= >>>>>>> markers)
# Then:
git add resolved-file.py
git commit  # Complete the merge

# Use specific version
 git checkout --ours file.py  # Keep our version
git checkout --theirs file.py  # Keep their version
```

## Best Practices

1. **Commit frequently** - Small, logical commits are easier to review and revert
2. **Write meaningful messages** - Future you will thank present you
3. **Pull before push** - Avoid unnecessary merge conflicts
4. **Don't commit sensitive data** - Use .gitignore for credentials
5. **Use branches** - Keep main stable, develop features in branches
6. **Review before committing** - Use `git diff --staged`
7. **Test before pushing** - Don't break the build for others

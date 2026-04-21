# Secure API Key Management

## Why It Matters
API keys and tokens are sensitive. If leaked, they can lead to data breaches, unauthorized access, and financial loss. All agents and tools must handle credentials securely.

## Core Principles
- **Never hardcode secrets** in source code or skill files.
- **Use environment variables**: Access via `os.getenv`.
- **Local development**: Use a `.env` file and `python-dotenv` to load variables.
- **Production**: Use a secrets manager (e.g., HashiCorp Vault, AWS Secrets Manager) or the platform's encrypted env store.
- **Least privilege**: Request only the permissions needed for the task.
- **Rotate regularly**: Change keys periodically and after any suspected compromise.

## Standard Pattern in Python
```python
import os
from dotenv import load_dotenv

# Load .env file in development; in production, env vars are already present
load_dotenv()

api_key = os.getenv('SERVICE_API_KEY')
if not api_key:
    raise RuntimeError('Missing required environment variable: SERVICE_API_KEY')

# Use api_key with the service...
```

## How to Document Required Variables
In any skill or tool that needs external credentials, include a Required Environment Variables section listing each variable and its purpose.

Example:
```markdown
## Required Environment Variables
- `GITHUB_TOKEN`: Personal access token with `repo` scope.
- `SLACK_BOT_TOKEN`: Bot token from Slack app with `chat:write` and `channels:read`.
```

## Adding MCP Servers Securely
When adding an MCP server via `mcp_add_server`, pass env vars as key=value pairs. Do not echo these values in logs.
```bash
mcp_add_server '@anthropic/mcp-server-github' 'github' 'GITHUB_TOKEN=$GITHUB_TOKEN'
```
Here the shell expands `$GITHUB_TOKEN` from the environment, avoiding plaintext exposure.

## Incident Response
If a secret is accidentally exposed:
1. Invalidate/revoke the compromised key immediately.
2. Generate a new key and update the environment.
3. Audit logs for unauthorized usage.
4. If committed to git, rotate and consider removing from history (git filter-branch).

## Additional Resources
- [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- Python `python-dotenv` docs
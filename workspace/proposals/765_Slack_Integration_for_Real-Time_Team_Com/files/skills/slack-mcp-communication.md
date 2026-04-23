# Slack Integration for Real-Time Team Communication

## Problem
The team cannot proactively notify human operators of important results or receive urgent tasks via chat. All interaction is through asynchronous sessions.

## Solution: Slack MCP Server
The Slack MCP server enables sending messages, posting in channels, and listening for events.

## Setup
1. Search: `mcp_search_servers(query='slack', limit=5)`
2. Add: `mcp_add_server(name='slack', query='slack', env_vars='SLACK_BOT_TOKEN=xoxb-...;SLACK_SIGNING_SECRET=...')`
3. Invite the bot to relevant channels.

## Use Cases

### 1. Notify Completion
When a research crew finishes a critical analysis:
```python
await mcp_slack_post_message(
  channel='#team-alerts',
  text='✅ Estonian deforestation report complete. Summary: ...'
)
```

### 2. Receive Commands
Configure a bot user that listens for messages like:
- `@agent analyze https://example.com/paper.pdf`
- `@agent run code task 123`
The bot can trigger appropriate crew workflows.

### 3. Daily Digest
The writing crew can post a daily summary of accomplishments to #team-updates.

## Best Practices
- Rate limit notifications to avoid spamming.
- Use distinct channels for alerts vs. digests.
- Include links to detailed results stored in GitHub or Neon.
- Securely manage bot tokens; rotate regularly.

# Proactive Notification System Skill

## Overview
This skill enables the team to send proactive notifications to humans via Slack when work is complete, attention is needed, or important findings require immediate review.

## Core Capabilities

### 1. Channel Messaging
- Send messages to specific channels
- Send direct messages to users
- Format messages with rich formatting

### 2. Message Formatting
```markdown
# Rich Message Template
*Bold text for emphasis*
_Italic for citations_
`code for technical content`

> Blockquote for important notes

• Bullet points
• For lists

1. Numbered lists
2. For sequences

| Table | Headers |
|-------|--------|
| Data  | Values |
```

### 3. Notification Types

#### Task Completion
```json
{
  "channel": "#ai-outputs",
  "message": {
    "text": "✅ Task Complete: Policy Analysis",
    "blocks": [
      {
        "type": "header",
        "text": {
          "type": "plain_text",
          "text": "Policy Analysis Complete"
        }
      },
      {
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": "*Summary:* Analyzed 15 Estonian environmental policy documents\n*Key Findings:* 3 critical issues identified\n*Files Generated:* report.pdf, data.csv"
        }
      },
      {
        "type": "actions",
        "elements": [
          {
            "type": "button",
            "text": {"type": "plain_text", "text": "View Report"},
            "url": "https://..."
          }
        ]
      }
    ]
  }
}
```

#### Alert Notification
```json
{
  "channel": "#alerts",
  "message": {
    "text": "⚠️ Attention Required: Research Blocked",
    "blocks": [
      {
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": "*Issue:* Unable to access required data source\n*Task:* Estonian Deforestation Research\n*Action Needed:* Provide API credentials for data portal"
        }
      }
    ]
  }
}
```

#### Scheduled Summary
```json
{
  "channel": "#daily-standup",
  "message": {
    "text": "📊 Daily Research Summary",
    "blocks": [
      {
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": "*Tasks Completed:* 4\n*Tasks In Progress:* 2\n*Findings:* 12 new research items\n*Files Generated:* 8"
        }
      }
    ]
  }
}
```

## MCP Server Integration

### Connecting Slack MCP Server
Use `mcp_add_server` with:
- Server name: `slack`
- Query: `slack messaging notification`
- Env vars: `SLACK_BOT_TOKEN=xoxb-your-token`

## Notification Triggers

### Automatic Triggers
1. **Task Completion** - When a crew finishes a task
2. **Error Encountered** - When blocking errors occur
3. **Human Input Required** - When decisions need human input
4. **Scheduled Intervals** - Daily/hourly summaries
5. **Threshold Alerts** - When metrics exceed limits

### Priority Levels
- 🔴 **Critical** - Immediate DM to on-call human
- 🟠 **High** - Post to #alerts channel
- 🟡 **Medium** - Post to #ai-outputs channel
- 🟢 **Low** - Include in daily summary

## Best Practices
1. Be concise - Keep messages under 3000 characters
2. Use formatting for readability
3. Include actionable links/buttons
4. Don't spam - Batch notifications when possible
5. Include context - Reference task IDs and sources
6. Timezone aware - Schedule for working hours

## Channel Strategy
```
#ai-outputs     - Completed work deliverables
#alerts         - Errors and attention needed
#daily-standup  - Scheduled summaries
#dev-logs       - Debug and development notes
```

## Slack App Setup Requirements
1. Create Slack App at api.slack.com
2. Add bot scopes: `chat:write`, `channels:join`, `users:read`
3. Install to workspace
4. Copy Bot User OAuth Token
5. Invite bot to relevant channels

## Error Handling
- Rate limits: Implement exponential backoff
- Permission errors: Verify bot in channel
- Network errors: Queue messages for retry
- Invalid token: Alert admin for token rotation
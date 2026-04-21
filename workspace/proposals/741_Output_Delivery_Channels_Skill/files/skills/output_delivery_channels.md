# Output Delivery Channels

## Overview
After the writing crew produces outputs, they need delivery mechanisms to get work to stakeholders. This skill documents available delivery channels and workflow patterns.

## Current Limitation
The team can create files using `file_manager` but cannot:
- Email reports to stakeholders
- Post updates to Slack channels
- Create shared Google Docs
- Schedule delivery for later

## Available MCP Delivery Servers

### 1. Gmail (45,694 installations)
**Best for:** Sending reports, summaries, and documents via email

**Setup:**
```
mcp_add_server(
    name="gmail",
    query="email send gmail",
    env_vars="GOOGLE_CLIENT_ID=xxx;GOOGLE_CLIENT_SECRET=xxx;GOOGLE_REFRESH_TOKEN=xxx"
)
```

**Available Actions:**
- Send emails with attachments
- Draft emails for review
- Reply to existing threads
- Forward messages
- Manage labels and organization

**Workflow Pattern:**
```
1. Writing crew completes report
2. Save report to /app/workspace/output/report.md
3. Connect Gmail MCP server
4. Send email with:
   - To: stakeholder@email.com
   - Subject: [Report] Analysis Complete
   - Body: Summary of findings
   - Attachment: report.md
5. Disconnect Gmail server
```

### 2. Slack (13,057 installations)
**Best for:** Team notifications, quick updates, channel announcements

**Setup:**
```
mcp_add_server(
    name="slack",
    query="slack communication messaging",
    env_vars="SLACK_BOT_TOKEN=xoxb-xxx;SLACK_TEAM_ID=Txxx"
)
```

**Available Actions:**
- Post messages to channels
- Send direct messages
- Upload files
- Add reactions
- Create threads

**Workflow Pattern:**
```
1. Task completed
2. Connect Slack MCP server
3. Post to #project-updates:
   "✅ Research complete: Estonian deforestation analysis
   📊 Key findings: [summary]
   📎 Full report: [link to file]"
4. Disconnect Slack server
```

### 3. Google Docs (1,992 installations)
**Best for:** Collaborative documents requiring stakeholder editing

**Setup:**
```
mcp_add_server(
    name="googledocs",
    query="pdf document processing",
    env_vars="GOOGLE_CLIENT_ID=xxx;GOOGLE_CLIENT_SECRET=xxx"
)
```

**Available Actions:**
- Create new documents
- Edit existing documents
- Share with specific users
- Export to various formats
- View revision history

**Workflow Pattern:**
```
1. Writing crew produces document
2. Connect Google Docs MCP server
3. Create document with title and content
4. Share with stakeholder emails
5. Post share link to team
6. Disconnect server
```

### 4. Outlook (2,641 installations)
**Best for:** Enterprise environments using Microsoft 365

**Setup:**
```
mcp_add_server(
    name="outlook",
    query="email send gmail",
    env_vars="MICROSOFT_CLIENT_ID=xxx;MICROSOFT_CLIENT_SECRET=xxx"
)
```

## Delivery Decision Matrix

| Output Type | Best Channel | Reason |
|-------------|--------------|--------|
| Final report | Gmail | Formal delivery with attachment |
| Quick update | Slack | Instant team visibility |
| Collaborative doc | Google Docs | Enables editing |
| Data analysis | Gmail + Slack | Email report, Slack summary |
| Code deliverable | GitHub PR | Version control + review |
| Policy document | Gmail | Formal stakeholder delivery |

## Delivery Workflow Checklist

Before delivering:
- [ ] Output file saved to /app/workspace/output/
- [ ] Summary/key findings extracted
- [ ] Recipient identified (email or channel)
- [ ] Appropriate channel selected
- [ ] MCP server connected
- [ ] Message sent with context
- [ ] Delivery confirmed
- [ ] MCP server disconnected (if one-time use)

## Security Best Practices

1. **Never hardcode credentials** in delivery commands
2. **Use environment variables** for all tokens
3. **Verify recipients** before sending sensitive data
4. **Clean up servers** after one-time deliveries
5. **Log deliveries** in team memory for audit trail:
   ```
   memory_store(
       text="Delivered Estonian policy report to stakeholder@env.gov.ee via Gmail",
       metadata="type=delivery, channel=gmail, timestamp=2024-01-15"
   )
   ```

## Example: Complete Delivery Flow

```
# Task: Deliver completed policy analysis

# 1. Verify output exists
file_manager("list", path="/app/workspace/output")

# 2. Connect Gmail
mcp_add_server(
    name="gmail",
    query="email send gmail",
    env_vars="GOOGLE_CLIENT_ID=" + os.environ["GOOGLE_CLIENT_ID"]
)

# 3. Send email (gmail_send_message now available)
gmail_send_message(
    to="policy-team@gov.ee",
    subject="[Complete] Estonian Environmental Policy Analysis",
    body="""
Dear Policy Team,

The requested analysis of Estonian environmental policy is complete.

Key Findings:
- Deforestation rate increased 12% YoY
- New protection zones proposed
- 5 policy recommendations identified

Full report attached.

Best regards,
AI Research Team
""",
    attachments=["/app/workspace/output/policy_analysis.md"]
)

# 4. Log delivery
memory_store(
    text="Delivered policy analysis to policy-team@gov.ee",
    metadata="type=delivery, output=policy_analysis.md, channel=gmail"
)

# 5. Cleanup
mcp_remove_server("gmail")
```

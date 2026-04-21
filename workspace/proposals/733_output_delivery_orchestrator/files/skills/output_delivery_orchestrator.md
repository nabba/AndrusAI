# Output Delivery Orchestrator Skill

## Purpose
Ensure research, analysis, and writing outputs reach stakeholders through appropriate channels.

## Problem Statement
Current team produces excellent work but has no delivery mechanism:
- Research findings sit in memory, unreachable by users
- Analysis results remain in sandbox files
- Reports exist but are never sent
- No notification when tasks complete

## Delivery Channels

### 1. File Export (Available Now)
Use file_manager to create organized output structure:
```
/workspace/output/
  ├── reports/
  │   └── YYYY-MM-DD_topic_report.md
  ├── data/
  │   └── YYYY-MM-DD_dataset.csv
  ├── analysis/
  │   └── YYYY-MM-DD_analysis.json
  └── exports/
      └── YYYY-MM-DD_bundle.zip
```

### 2. Slack Notifications (MCP Server Available)
Add `slack` MCP server for real-time notifications:
- Task completion alerts
- Research summary broadcasts
- Error notifications

### 3. Email Reports (MCP Server Available)
Add `gmail` MCP server for formal delivery:
- Daily digest emails
- Full report attachments
- Stakeholder notifications

## Workflow Integration

### Research Crew Delivery
1. Complete research synthesis
2. Create summary document in /output/reports/
3. Send Slack notification with key findings
4. Optionally email full report to stakeholders

### Coding Crew Delivery
1. Complete analysis or tool development
2. Export results to /output/analysis/ or /output/data/
3. Create documentation of outputs
4. Notify via Slack with file locations

### Writing Crew Delivery
1. Complete document/policy/report
2. Export to /output/reports/ with metadata
3. Send formatted preview via Slack
4. Email to designated recipients if requested

## Delivery Templates

### Slack Message Format
```
✅ Task Complete: [Task Name]

📊 Summary: [2-3 sentence summary]

📁 Output: /workspace/output/[filename]

⏱️ Duration: [time]

🔗 Key Links: [relevant URLs]
```

### Email Report Format
```
Subject: [Team Name] - [Report Type] - [Date]

Executive Summary: [paragraph]

Key Findings:
- Finding 1
- Finding 2
- Finding 3

Full Report: [attached or linked]

Prepared by: AI Research Team
Date: [timestamp]
```

### File Export Metadata
```json
{
  "output_type": "research_report",
  "created_at": "2024-01-15T10:30:00Z",
  "task_id": "task-123",
  "crew": "research",
  "files": ["report.md", "data.csv"],
  "summary": "2-3 sentence summary",
  "recipients": ["stakeholder@example.com"],
  "delivery_status": {
    "file_export": true,
    "slack_notification": true,
    "email_sent": false
  }
}
```

## Implementation Priority

### Phase 1 (Immediate - No MCP)
- Standardize output directory structure
- Create metadata files for all outputs
- Generate summary files automatically

### Phase 2 (Add MCP Servers)
- Add `slack` MCP server for notifications
- Configure channel routing for different output types

### Phase 3 (Full Integration)
- Add `gmail` MCP server for email delivery
- Implement delivery preferences per task type
- Create delivery confirmation tracking

## Recommended MCP Servers
- `slack` - for real-time notifications
- `gmail` - for email delivery

## Anti-Patterns
- Do NOT deliver partial/incomplete work
- Do NOT send sensitive data via Slack
- Do NOT skip summary generation
- Do NOT deliver without metadata

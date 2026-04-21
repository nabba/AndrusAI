# Research-to-PDF Pipeline

## Overview
This skill defines the complete workflow for transforming research findings into professional PDF reports. Use this when delivering research outputs to stakeholders.

## When to Use This Pipeline

- Final research deliverables
- Executive summaries
- Technical reports
- Policy briefs (like Estonian deforestation research)
- Data analysis reports

## Prerequisites

1. **Add PDF MCP Server** (if not already added):
```
mcp_add_server(
    name="gen-pdf/mcp",
    query="pdf generation markdown",
    env_vars=""
)
```

2. **Verify connection**:
```
mcp_list_servers()
```

## Pipeline Steps

### Step 1: Structure Research Content

Organize findings into this structure:

```markdown
# [Report Title]

## Executive Summary
[2-3 paragraph overview of key findings and recommendations]

## Introduction
[Context and objectives]

## Methodology
[How research was conducted]

## Findings
### [Finding Category 1]
[Content with citations]

### [Finding Category 2]
[Content with citations]

## Analysis
[Interpretation of findings]

## Recommendations
[Actionable next steps]

## Sources
[Formatted citations and links]

## Appendix
[Supplementary data, raw results]
```

### Step 2: Enhance with Formatting

Use GitHub Flavored Markdown features:

```markdown
**Bold** for emphasis
*Italic* for technical terms
- Bullet points for lists
1. Numbered lists for sequences
> Blockquotes for key insights
`inline code` for technical terms

| Table | Headers |
|-------|--------|
| Data  | Values |

## Headers for sections
### Subheaders for subsections
```

### Step 3: Generate PDF

Using gen-pdf/mcp tools:

1. **convert_markdown_to_pdf** - Main conversion function
   - Input: Markdown content
   - Options: dark_mode, page_size, margins

2. **Customize styling**:
   - Professional reports: Use default light mode
   - Technical docs: Consider dark mode
   - Page size: A4 for European audiences, Letter for US

### Step 4: Quality Check

Before delivery, verify:
- [ ] All sections present and complete
- [ ] Citations properly formatted
- [ ] Tables render correctly
- [ ] Links are clickable
- [ ] Page breaks at logical points
- [ ] File size reasonable (< 10MB typical)

## Report Templates

### Template 1: Policy Research Report

```markdown
# [Topic] Policy Analysis

**Date**: [Date]
**Prepared by**: AI Research Team
**Region**: [Geographic focus]

---

## Executive Summary
[High-level overview for decision-makers]

## Policy Context
[Current state, legal framework]

## Key Findings
### Finding 1
[Evidence and implications]

### Finding 2
[Evidence and implications]

## Comparative Analysis
[How this compares to similar situations elsewhere]

## Recommendations
1. [Action item]
2. [Action item]

## References
- [Source 1 with link]
- [Source 2 with link]
```

### Template 2: Technical Research Report

```markdown
# [Technical Topic] Analysis

**Version**: 1.0
**Last Updated**: [Date]

---

## Abstract
[Brief summary of the investigation and results]

## Problem Statement
[What question or issue is being addressed]

## Technical Background
[Context needed to understand the analysis]

## Approach
[Methods, tools, data sources used]

## Results
### [Result Category 1]
[Data, charts, findings]

### [Result Category 2]
[Data, charts, findings]

## Discussion
[Interpretation of results, limitations]

## Conclusion
[Summary and next steps]

## Technical Details
### Data Sources
[Specific APIs, databases, files]

### Code References
[Links to code, scripts used]
```

### Template 3: Executive Brief

```markdown
# [Topic] - Executive Brief

**Prepared for**: [Stakeholder]
**Date**: [Date]
**Priority**: [High/Medium/Low]

---

## Bottom Line
[One sentence summary]

## Key Points
- [Point 1]
- [Point 2]
- [Point 3]

## Why This Matters
[Business/strategic impact]

## Recommended Action
[What should be done]

## Supporting Data
[Charts, tables, key metrics]

## Questions?
Contact: [Team contact information]
```

## Integration with Team Workflows

### Research Crew
1. Gather information using web_search, web_fetch
2. Structure findings using templates above
3. Store structured content in team_memory
4. Trigger PDF generation

### Coding Crew
1. For data-heavy reports, use code_executor to generate visualizations
2. Export charts as base64 images for embedding
3. Create data tables from analysis

### Writing Crew
1. Receive structured research from Research crew
2. Apply templates and formatting
3. Generate final PDF
4. Store PDF reference in team_memory

## Alternative: Without MCP Server

If MCP server is unavailable, use Python fallback:

```python
# In code_executor
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

def create_pdf(content, filename="report.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    for line in content.split('\n'):
        if line.startswith('# '):
            story.append(Paragraph(line[2:], styles['Heading1']))
        elif line.startswith('## '):
            story.append(Paragraph(line[3:], styles['Heading2']))
        else:
            story.append(Paragraph(line, styles['Normal']))
        story.append(Spacer(1, 6))
    
    doc.build(story)
    return filename
```

## Best Practices

1. **Start with structure** - Define sections before writing content
2. **Write for scannability** - Use headers, bullets, bold text
3. **Include sources** - Always cite where information came from
4. **Add timestamps** - Reports should show when they were created
5. **Version control** - Use version numbers for iterative reports

---
*This skill works best when combined with the MCP Server Integration Playbook for PDF server setup.*
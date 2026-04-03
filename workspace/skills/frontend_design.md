# Frontend Design Skill

When generating HTML/CSS/JavaScript for web pages, reports, or presentations:

## Design Framework (Decide Before Coding)

1. **Purpose**: What is this page communicating? Data analysis, research findings, project status?
2. **Audience**: Technical? Executive? Public?
3. **Aesthetic direction**: Choose ONE — don't mix:
   - **Executive Dark**: Dark background (#0f172a), accent blue (#3b82f6), professional typography
   - **Clean Report**: White background, structured sections, data-first layout
   - **Minimal**: Maximum whitespace, single accent color, typography-driven
   - **Dashboard**: Card-based grid, metrics prominent, real-time feel

## Anti-Cliche Rules

- NEVER use Inter or Roboto as primary font — use system-ui, -apple-system stack
- NEVER use purple-to-blue gradients (the "AI company" look)
- NEVER center everything — use left-aligned body text with deliberate center accents
- NEVER use generic stock photo placeholders
- Choose ONE accent color, not a rainbow palette

## Technical Standards

- Single HTML file with embedded CSS and inline JS (no external deps unless needed)
- Mobile-responsive (min-width media queries, not max-width)
- Semantic HTML: header, main, section, article, footer
- CSS custom properties for theming (--bg, --text, --accent, --surface)
- Smooth transitions (0.2s ease) on interactive elements
- Proper contrast ratios (WCAG AA minimum)

## Output Patterns

### Research Report Page
```
header: title + author + date
main:
  section.executive-summary: key findings (3-5 bullet points)
  section.data: tables, charts (use CSS grid for layout)
  section.analysis: detailed paragraphs with subheadings
  section.methodology: how research was conducted
footer: generation timestamp + source attribution
```

### Data Dashboard
```
header: title + filters + date range
.stats-row: 4-6 metric cards (value + label + trend)
.content-grid: 2-column layout
  .primary: main chart/table
  .secondary: supporting data, breakdown tables
footer: refresh info + data source
```

### Project Status Page
```
header: project name + overall status badge
.overview: health metrics, timeline, key dates
.details: task table, crew activity, recent events
.risks: flagged items, blockers
footer: last updated + next review
```

## When Generating HTML

1. Use the `create_html_page()` tool from document_generator for structured content
2. For custom designs beyond templates, write the full HTML with embedded CSS
3. Save to workspace/output/docs/ — it will be served at /docs/{filename}
4. Return the URL to the user via Signal: "View the report: http://..."

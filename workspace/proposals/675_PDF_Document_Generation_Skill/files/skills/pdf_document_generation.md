# PDF Document Generation

## Problem
The team lacks native ability to convert reports, markdown, or text content into PDF documents. Past tasks requiring PDF output were slow or failed.

## Solution: Python-based PDF Generation

### Option 1: Markdown to PDF (Recommended for reports)
```python
import subprocess
import os

def markdown_to_pdf(md_content: str, output_path: str) -> str:
    """Convert markdown content to PDF using available tools."""
    md_path = output_path.replace('.pdf', '.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    # Try weasyprint first (best quality)
    try:
        from weasyprint import HTML
        import markdown
        html_content = markdown.markdown(md_content, extensions=['tables', 'fenced_code'])
        full_html = f'<html><head><style>body{{font-family:sans-serif;margin:40px;}}table{{border-collapse:collapse;}}th,td{{border:1px solid #ddd;padding:8px;}}</style></head><body>{html_content}</body></html>'
        HTML(string=full_html).write_pdf(output_path)
        return output_path
    except ImportError:
        pass
    
    # Fallback: use reportlab for basic text-to-PDF
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
        doc = SimpleDocTemplate(output_path, pagesize=A4)
        styles = getSampleStyleSheet()
        paragraphs = [Paragraph(line, styles['Normal']) for line in md_content.split('\n') if line.strip()]
        doc.build(paragraphs)
        return output_path
    except ImportError:
        pass
    
    # Last resort: install and use pandoc via subprocess
    subprocess.run(['pip', 'install', 'markdown', 'weasyprint'], capture_output=True)
    import markdown
    from weasyprint import HTML
    html_content = markdown.markdown(md_content, extensions=['tables', 'fenced_code'])
    full_html = f'<html><head><style>body{{font-family:sans-serif;margin:40px;}}</style></head><body>{html_content}</body></html>'
    HTML(string=full_html).write_pdf(output_path)
    return output_path
```

### Option 2: Install dependencies first
```bash
pip install weasyprint markdown reportlab
```

### Option 3: MCP Server Fallback
An MCP server `gen-pdf/mcp` is available for remote PDF generation from markdown. Use `mcp_add_server` with name `gen-pdf/mcp` if Python libraries are unavailable.

## Usage Pattern
1. Generate report content as markdown
2. Call `markdown_to_pdf(content, '/app/workspace/output/report.pdf')`
3. Return the file path to the user

## Key Notes
- Always save to `/app/workspace/output/` directory
- Use UTF-8 encoding for international character support
- For complex layouts, weasyprint + HTML/CSS gives best control

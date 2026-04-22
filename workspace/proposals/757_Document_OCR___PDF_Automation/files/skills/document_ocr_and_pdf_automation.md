# Document OCR & PDF Automation

## Overview
Comprehensive document processing including OCR, text extraction, form recognition, and PDF generation.

## Core Competencies
- OCR (Optical Character Recognition) for scanned documents
- PDF text and metadata extraction
- Form field detection and data extraction
- Structured data extraction from invoices/contracts
- PDF generation from templates and markdown
- Document conversion between formats
- Batch document processing
- Quality validation and error handling

## MCP Integration
- **Primary Server**: `JigsawStack/vocr` (AI-powered OCR)
- **Secondary Server**: `hello-3ubk/docu-scan` (structured extraction)
- **Alternative**: `pdf-generator-api/mcp-server` for creation

## Usage Patterns
```
# Extract text from scanned PDF
ocr_result = extract_document_text(pdf_url, language='et')

# Parse invoice data
invoice_data = extract_invoice_fields(scanned_invoice)
{
    "vendor": "...",
    "amount": 123.45,
    "date": "2024-01-15"
}

# Generate PDF from research
pdf_content = markdown_to_pdf(
    content=white_paper_summary,
    styling="professional_report"
)
```

## Estonian Document Processing
- Handles Estonian characters (õ, ä, ö, ü) in OCR
- Processes Estonian-specific document formats
- Supports Estonian language detection
- Extracts data from Estonian invoices/contracts

## Workflows
1. **Research Pipeline**: Extract text from policy PDFs, translate if needed, analyze content
2. **Invoice Processing**: Automatically extract vendor, amounts, dates from receipts
3. **Contract Analysis**: Identify key clauses, dates, parties from scanned contracts
4. **Report Generation**: Create formatted PDF reports from markdown research summaries
5. **Document Archiving**: Convert physical documents to searchable digital archives

## Best Practices
- Validate OCR confidence scores
- Implement fallback to manual review for low confidence
- Use language-specific OCR models
- Maintain document version history
- Ensure data privacy for sensitive documents

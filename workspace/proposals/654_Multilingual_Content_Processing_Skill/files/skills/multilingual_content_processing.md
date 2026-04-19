# Multilingual Content Processing

## Problem
The writing crew fails when asked to analyze or improve documents in non-English languages (observed: Estonian white paper analysis failed twice).

## Guidelines

### 1. Language Detection
- First identify the document's language before processing
- Note any mixed-language content (e.g., English technical terms in a non-English document)

### 2. Analysis Strategy for Non-English Documents
- **Do NOT translate first then analyze** — analyze in the original language when possible
- Preserve original terminology and cultural context
- If the LLM supports the language, work directly in it
- If the LLM struggles, use a two-pass approach:
  1. Pass 1: Extract structure, key arguments, and factual claims
  2. Pass 2: Provide feedback in the user's preferred response language

### 3. Common Non-English Document Tasks
- **Summarization**: Summarize in the original language, then optionally translate the summary
- **Improvement suggestions**: Provide suggestions in the document's language OR the user's language (ask if unclear)
- **Proofreading**: Must be done in the original language — do not auto-translate
- **Translation**: Preserve tone, formality level, and domain-specific terminology

### 4. Supported Language Families
- **Baltic-Finnic**: Estonian, Finnish — agglutinative, 14 grammatical cases
- **Germanic**: German, Dutch, Swedish, Norwegian, Danish
- **Romance**: French, Spanish, Italian, Portuguese, Romanian
- **Slavic**: Russian, Polish, Czech, Ukrainian
- **Other**: Turkish, Japanese, Chinese, Korean, Arabic, Hindi

### 5. Quality Checks
- Verify character encoding is correct (UTF-8)
- Check for garbled text that may indicate encoding issues
- If output quality is uncertain, flag it explicitly to the user
- Never silently fail — if the language is unsupported, say so clearly

### 6. Error Recovery
- If first attempt produces poor results, retry with explicit language instructions
- Consider using code_executor to call translation APIs as fallback
- Always return partial results rather than empty output

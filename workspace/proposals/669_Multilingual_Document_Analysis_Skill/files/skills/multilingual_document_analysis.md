# Multilingual Document Analysis

## Problem
The writing crew fails when asked to analyze or improve documents in non-English languages (observed: Estonian white paper analysis failed twice).

## Strategy

### 1. Language Detection & Acknowledgment
- Identify the document language before analysis
- Explicitly state the language to the user
- If unsure of fluency, acknowledge limitations upfront

### 2. Analysis Approach for Non-English Documents
- **Structure analysis**: Evaluate document organization, flow, and logical structure (language-independent)
- **Content analysis**: Summarize key arguments and assess coherence
- **Translation-assisted review**: When deep language knowledge is limited, focus on universal writing quality metrics (clarity, structure, evidence, persuasiveness)
- **Cultural context**: Consider the cultural and political context of the document's origin

### 3. Universal Document Quality Metrics
These apply regardless of language:
- Logical flow and argument structure
- Evidence and citation quality
- Audience appropriateness
- Formatting and visual organization
- Completeness of key sections (intro, body, conclusion, recommendations)

### 4. Output Format for Multilingual Analysis
- Respond in the language the user used for the request
- Quote relevant passages in the original language
- Provide translations of key terms if analyzing in a different language than the document
- Structure feedback as: Strengths → Weaknesses → Specific Suggestions

### 5. Supported Languages Guidance
- For common European languages (Estonian, Finnish, Latvian, etc.): Proceed with analysis, noting any uncertainty
- For less common scripts: Focus on structural analysis and request clarification if needed
- Always attempt the task rather than refusing — partial analysis is better than failure

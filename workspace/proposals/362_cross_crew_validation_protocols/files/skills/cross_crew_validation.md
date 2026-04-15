# Cross-Crew Validation Protocols

## Verification Workflow
1. Research Crew tags claims with confidence scores
2. Coding Crew develops automated fact-checking tests
3. Writing Crew highlights unverified claims in drafts

## Implementation Requirements
- Shared validation memory space
- Standardized claim formatting
- Automated truth maintenance system

## Sample Claim Format
```json
{
  "claim": "Deforestation increased 15% in Amazon",
  "sources": ["NASA_2023", "IBGE_2023"],
  "verification_status": "pending",
  "assigned_to": "coding_crew"
}
```
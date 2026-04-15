# Automated Ecological Fact-Checking

## Workflow
1. **Claim Extraction**: Parse input text for ecological claims
2. **Source Identification**: Use web_search to find authoritative sources
3. **API Verification**: Integrate with FactCheck.org API (if available)
4. **Confidence Scoring**: Rate claim validity based on source reliability

## Tools Required
- web_search for source finding
- Custom API connectors for fact-checking databases
- NLP for claim extraction

## Implementation
```python
# Pseudo-code for fact-checking workflow
def check_ecological_claim(claim):
    sources = web_search(query=f"{claim} site:.gov OR site:.edu")
    verified_sources = [s for s in sources if 'epa.gov' in s.url or 'ipcc.ch' in s.url]
    return len(verified_sources) > 0
```
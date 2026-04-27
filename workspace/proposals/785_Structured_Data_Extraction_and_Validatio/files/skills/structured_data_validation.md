# Structured Data Extraction & Validation

## Objective
Ensure all lead generation and research datasets are normalized, deduplicated, and validated before delivery.

## Protocol
1. **Schema Enforcement**: Define a strict JSON schema for every extraction task (e.g., `company_name`, `contact_email`, `linkedin_url`, `region`).
2. **Deduplication Logic**: 
   - Primary Key: Email address.
   - Secondary Key: Domain + Person Name.
3. **Validation Steps**:
   - Verify email syntax using regex.
   - Validate URL accessibility via `web_fetch`.
   - Cross-reference LinkedIn profiles against company domains.
4. **Quality Scoring**: Assign a confidence score (1-5) based on the number of independent sources verifying the contact.
# Lead Management & CRM Formatting Workflow

## Objective
Transform raw research data into high-quality, deduplicated, and formatted lead lists ready for CRM import.

## Step-by-Step Process
1. **Data Normalization**: Convert all extracted contact data into a standardized schema (First Name, Last Name, Company, Email, LinkedIn URL, Region).
2. **Deduplication**: Use a case-insensitive lookup of emails and LinkedIn URLs to remove duplicate entries across different research batches.
3. **Lead Scoring**: Assign a priority score (1-5) based on:
    - Direct match with target persona (e.g., 'Head of Sales').
    - Verified email status.
    - Regional relevance (e.g., CEE/Baltics).
4. **CSV Formatting**: Export the final list in UTF-8 CSV format with headers mapping exactly to common CRM fields (e.g., HubSpot, Salesforce).
5. **Validation Audit**: Perform a final spot-check on 5% of the list to ensure the 'Verification' skill was applied correctly.
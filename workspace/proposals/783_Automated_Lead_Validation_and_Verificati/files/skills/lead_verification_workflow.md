# Lead Verification Workflow

## Objective
Ensure 95%+ deliverability of identified B2B sales contacts.

## Steps
1. **Identification**: Use `web_search` to find the target individual and their role.
2. **Pattern Discovery**: Identify the company's email pattern (e.g., first.last@company.com).
3. **Verification**: Use external API tools to verify the mailbox existence (SMTP check).
4. **Cross-Reference**: Match the LinkedIn profile URL with the verified email to ensure identity alignment.
5. **Documentation**: Store verified leads in `team_memory_store` with metadata `status=verified`.
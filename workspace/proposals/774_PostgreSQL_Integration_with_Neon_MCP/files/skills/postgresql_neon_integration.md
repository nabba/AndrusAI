# PostgreSQL Integration with Neon MCP

## 1. Why PostgreSQL?
PostgreSQL is a powerful, open-source relational database. It allows structured storage, complex queries, and ACID transactions—ideal for storing research data like verified contacts, company profiles, and policy metadata.

## 2. What is Neon?
Neon is a serverless PostgreSQL platform with branching and automatic scaling. Its MCP server gives the AI team direct SQL execution without managing connection strings in code.

## 3. Setup

### Prerequisites
- A Neon account (https://neon.tech)
- An API key or connection string (we'll use `DATABASE_URL`).

### Connect the MCP Server
Use the `mcp_add_server` tool:
```
mcp_add_server(
  name='neon',
  query='postgres',
  env_vars='DATABASE_URL=your-neon-connection-string'
)
```
This makes tools like `neon_execute_sql` available to all agents.

## 4. Designing Schemas for Research

### Example: Sales Contacts
```sql
CREATE TABLE contacts (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  email VARCHAR(255) UNIQUE,
  company VARCHAR(255),
  region VARCHAR(100),
  verified BOOLEAN DEFAULT false,
  source_url TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);
```

### Example: Payment Service Providers (PSPs)
```sql
CREATE TABLE psps (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  countries JSONB,  -- e.g., ['EE', 'LV', 'LT']
  direct_sales BOOLEAN,
  integration_apis JSONB, -- e.g., ['REST', 'SOAP']
  contact_email VARCHAR(255),
  website TEXT,
  last_updated TIMESTAMP DEFAULT NOW()
);
```

### Example: Policy Papers
```sql
CREATE TABLE policy_papers (
  id SERIAL PRIMARY KEY,
  title VARCHAR(500),
  source_url TEXT,
  publish_date DATE,
  region VARCHAR(100),
  summary TEXT,
  full_text TEXT,
  embedding VECTOR(1536)  -- if using pgvector for similarity search
);
```

## 5. Basic CRUD Operations

### Insert Data
```sql
INSERT INTO contacts (name, email, company, region, verified, source_url)
VALUES ('Jane Smith', 'jane@company.ee', 'TechOÜ', 'Estonia', true, 'https://example.com/contact');
```

### Query Data
```sql
SELECT name, email FROM contacts WHERE verified = true AND region = 'CEE';
```

### Update Data
```sql
UPDATE contacts SET verified = true WHERE email = 'john@example.com';
```

### Delete Data
```sql
DELETE FROM contacts WHERE id = 123;
```

## 6. Parameterized Queries (Safety)
Always use parameterized queries to avoid SQL injection. The Neon MCP tools support passing parameters separately. Example:
```
neon_execute_sql(
  query='INSERT INTO contacts (name, email) VALUES ($1, $2)',
  params=['Alice', 'alice@example.com']
)
```

## 7. Integrating with Crews

### Research Crew
After identifying contacts via web search and scraping, the crew should:
1. Validate email format and company info.
2. Insert into `contacts` table using parameterized queries.
3. Store the source URL for traceability.

### Writing Crew
To produce a report of verified PSPs in the Baltics:
```sql
SELECT p.name, p.contact_email, c.name AS contact_name
FROM psps p
LEFT JOIN contacts c ON p.contact_email = c.email
WHERE p.direct_sales = true AND 'EE' = ANY(p.countries::text[]);
```
The crew can also join with `policy_papers` to add policy context.

## 8. Performance & Maintenance

- Create indexes: `CREATE INDEX idx_contacts_region ON contacts(region);` and `CREATE INDEX idx_contacts_verified ON contacts(verified);`.
- Use `EXPLAIN ANALYZE` to understand query performance.
- Neon provides branching: create a branch for experimental data cleaning without affecting production.
- Set up automatic backups via Neon's retention settings.
- Monitor query duration and optimize slow queries.

## 9. Troubleshooting
- If `neon_execute_sql` returns an error, check syntax and table existence.
- Use `neon_list_tables` (if available) to inspect the schema.
- Ensure the `DATABASE_URL` has the correct permissions.

## 10. Next Steps
- Explore JSONB fields for flexible attributes.
- Consider adding `pgvector` extension for embedding storage and similarity search (useful for matching policy papers).
- Automate daily exports to data warehouse if needed.

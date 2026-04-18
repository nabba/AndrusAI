# Ecological Data Management with PostgreSQL

## Overview
This skill enables the team to store, query, and analyze structured ecological data using the Supabase MCP server (PostgreSQL). It addresses the gap where research outputs, stakeholder information, and monitoring data are ephemeral and hard to aggregate.

## Prerequisites
- Add Supabase MCP server via `mcp_add_server` with name "Supabase"
- Obtain Supabase API key and project URL (free tier available)
- Basic SQL knowledge

## Core Data Models for Ecology

### 1. Study Sites
```sql
CREATE TABLE sites (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  latitude DECIMAL(9,6),
  longitude DECIMAL(9,6),
  ecosystem_type TEXT,
  area_ha DECIMAL,
  protected_status TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);
```

### 2. Species Observations (Biodiversity Monitoring)
```sql
CREATE TABLE observations (
  id UUID PRIMARY KEY,
  site_id UUID REFERENCES sites(id),
  species_name TEXT NOT NULL,
  count INTEGER,
  observation_date DATE,
  observer TEXT,
  method TEXT,
  notes TEXT,
  UNIQUE(site_id, species_name, observation_date)
);
```

### 3. Stakeholder Engagement Records
```sql
CREATE TABLE stakeholders (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  organization TEXT,
  role TEXT,
  email TEXT,
  latitude DECIMAL(9,6),
  longitude DECIMAL(9,6),
  engagement_level TEXT,
  last_contact DATE,
  notes TEXT
);
```

### 4. Ecological Impact Assessments
```sql
CREATE TABLE impact_assessments (
  id UUID PRIMARY KEY,
  project_name TEXT NOT NULL,
  site_id UUID REFERENCES sites(id),
  assessment_date DATE,
  impact_score INTEGER CHECK (impact_score >= -10 AND impact_score <= 10),
  impact_category TEXT,
  confidence_level TEXT,
  assessor TEXT,
  recommendations TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);
```

## Common Query Patterns

### Temporal Analysis: Species trends over time
```sql
SELECT 
  observation_date,
  species_name,
  SUM(count) as total_count
FROM observations
WHERE site_id = '...'
GROUP BY observation_date, species_name
ORDER BY observation_date;
```

### Spatial Query: Sites within radius
```sql
SELECT 
  name,
  ecosystem_type,
  latitude,
  longitude,
  ( 6371 * acos( cos( radians(?) ) * cos( radians( latitude ) ) * cos( radians( longitude ) - radians(?) ) + sin( radians(?) ) * sin( radians( latitude ) ) ) ) AS distance
FROM sites
HAVING distance < ?
ORDER BY distance;
```

### Stakeholder Engagement Effectiveness
```sql
SELECT 
  s.engagement_level,
  COUNT(*) as count,
  AVG(DATE_PART('day', NOW() - s.last_contact)) as avg_days_since_contact
FROM stakeholders s
GROUP BY s.engagement_level;
```

### Impact Assessment Summary
```sql
SELECT 
  impact_category,
  AVG(impact_score) as avg_impact,
  COUNT(*) as assessment_count,
  MIN(assessment_date) as first_assessed,
  MAX(assessment_date) as latest_assessed
FROM impact_assessments
GROUP BY impact_category
ORDER BY avg_impact;
```

## Integration with Existing Crews

### Research Crew
1. Store rapid literature review findings in `research_sources` table
2. Track data sources and confidence scores
3. Link observations to published papers

### Writing Crew
1. Query stakeholder data for targeted communication drafts
2. Retrieve historical impact assessments for report context
3. Aggregate species data for environmental impact statements

### Self-Improvement Crew
1. Log task completion times in `task_metrics` table
2. Analyze which ecological analysis patterns are most/least successful
3. Store skill mastery assessments

## Batch Operations & Idempotency

Use `INSERT ... ON CONFLICT` for upserts to avoid duplicates:
```sql
INSERT INTO observations (site_id, species_name, count, observation_date, observer)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (site_id, species_name, observation_date)
DO UPDATE SET count = EXCLUDED.count, observer = EXCLUDED.observer;
```

## Security & Compliance
- Never store sensitive personal data without encryption
- Anonymize stakeholder data when sharing
- Use row-level security policies for multi-organization access
- Regular backups of critical ecological datasets

## Troubleshooting
- Connection errors: Verify SUPABASE_URL and SUPABASE_ANON_KEY
- Permission denied: Check table permissions in Supabase dashboard
- Query slow: Add indexes on frequently filtered columns (site_id, observation_date, species_name)

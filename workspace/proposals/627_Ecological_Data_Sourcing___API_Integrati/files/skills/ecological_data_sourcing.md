# Ecological Data Sourcing & API Integration

## Objective
Transition from unstructured web scraping to structured API data retrieval for higher reproducibility in ecological reports.

## Key Data Sources
1. **GBIF (Global Biodiversity Information Facility)**: For species occurrence data.
2. **IUCN Red List API**: For conservation status and threat levels.
3. **Copernicus / Sentinel Hub**: For satellite-based land cover and vegetation indices (NDVI).
4. **World Bank Open Data**: For socio-economic ecological indicators.

## Integration Workflow
1. **Identification**: Research crew identifies the required metric (e.g., 'Species Population Trend').
2. **API Discovery**: Locate the REST endpoint and required authentication (API Keys).
3. **Extraction**: Coding crew implements a Python client to fetch JSON/CSV data.
4. **Validation**: Use the `EcologicalStats` tool to validate data distributions before writing.

## Best Practices
- Always cache API responses to avoid rate limiting.
- Document API versioning in the final report for reproducibility.
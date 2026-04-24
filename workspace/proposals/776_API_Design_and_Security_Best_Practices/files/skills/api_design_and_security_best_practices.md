# API Design and Security Best Practices

## Overview
This skill equips agents with knowledge to design, implement, and secure robust APIs following industry best practices.

## RESTful Principles
- Resources and URIs
- HTTP verbs (GET, POST, PUT, PATCH, DELETE)
- Statelessness
- HATEOAS
- Status codes (200, 201, 400, 401, 403, 404, 409, 500, etc.)

## OpenAPI/Swagger
- Specifying API contracts
- Generating documentation
- Client SDK generation

## GraphQL
- Schema definition
- Queries, mutations, subscriptions
- Resolvers
- Performance considerations (N+1, batching, DataLoader)

## Authentication
- API keys
- Basic Auth
- OAuth 2.0 (Authorization Code, Client Credentials, Implicit, PKCE)
- OpenID Connect
- JWT (JSON Web Tokens) - structure, signing, verification

## Authorization
- Role-Based Access Control (RBAC)
- Attribute-Based Access Control (ABAC)
- Policy languages (OPA, Rego)
- Scope-based permissions

## Input Validation and Sanitization
- Schema validation (JSON Schema, Pydantic)
- Sanitizing inputs to prevent injection attacks
- Rate limiting (token bucket, leaky bucket)

## CORS (Cross-Origin Resource Sharing)
- Configuring Allowed Origins
- Preflight requests

## Caching
- HTTP caching headers (Cache-Control, ETag, Last-Modified)
- CDN caching

## Versioning
- URL versioning (/v1/resource)
- Header versioning
- Query parameter versioning

## Error Handling
- Standardized error response format
- Logging errors securely (avoid leaking stack traces)

## Testing APIs
- Contract testing (Pact)
- Integration tests with tools like Postman/Newman
- Load testing (Locust, k6)

## Security Best Practices
- Using HTTPS/TLS
- Secure storage of secrets (vaults, environment variables)
- Regular security audits
- Penetration testing

## Monitoring and Observability
- API metrics (latency, error rates, throughput)
- Distributed tracing (OpenTelemetry)
- Alerting on anomalies

## Tools and Libraries
- FastAPI, Django REST framework, Flask
- Authentication libraries (OAuthlib, python-jose)
- Validation (pydantic, jsonschema)
- Rate limiting (slowapi, django-ratelimit)

## Further Learning
- OWASP API Security Top 10
- 'API Design Patterns' by JJ Geewax
- 'REST API Design Rulebook' by Mark Masse
# Proposal #770: API Integration Best Practices

**Type:** skill  
**Created:** 2026-04-23T01:40:41.017035+00:00  

## Why this is useful

Standardize API interaction to improve reliability, maintainability, and security.

## What will change

Ensure secure authentication and rate limiting.

## Potential risks to other subsystems

Potential for fragile integrations and repeated work.

## Files touched

- `skills/api_integration_best_practices.md`

## Original description

The team lacks a standardized approach for integrating with external APIs, leading to fragile code, inconsistent error handling, and security risks. This skill defines patterns for authentication, rate limiting, retries, pagination, validation, logging, and async usage. It includes a reusable client class and examples. It will improve reliability, maintainability, and security of all API interactions.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 770` / `reject 770` via Signal.

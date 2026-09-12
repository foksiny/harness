---
name: api_designer
description: REST, OpenAPI 3.1, and GraphQL schema specification, endpoint scaffolding, and API contract design.
triggers: [api, rest, openapi, swagger, graphql, endpoint, schema, route]
---
# API Designer for Harness

Principles for designing clean, predictable, and robust APIs.

## API Design Standards
1. **Resource Naming**:
   - Use plural nouns for collections: `/api/v1/users`, `/api/v1/projects`.
   - Nest hierarchical relationships logically: `/api/v1/projects/{id}/tasks`.
2. **HTTP Status Codes**:
   - `200 OK`: Successful retrieval / update.
   - `201 Created`: Resource successfully created (include `Location` header or resource payload).
   - `204 No Content`: Successful deletion or action without return body.
   - `400 Bad Request`: Validation failure. Return structured error details.
   - `401 Unauthorized`: Missing or invalid authentication token.
   - `403 Forbidden`: Authenticated, but lacking permission.
   - `404 Not Found`: Resource does not exist.
   - `422 Unprocessable Entity`: Semantic validation errors.
3. **Idempotency & Pagination**:
   - Use cursor-based or limit/offset pagination with `total_count` and `next_cursor`.

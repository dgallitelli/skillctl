---
name: api-design-reviewer
description: Reviews REST API designs for consistency, naming conventions, versioning strategy, and error handling patterns
skillctl:
  version: 1.2.0
  category: code-review
  tags:
    - api
    - rest
    - code-review
    - design
  capabilities:
    - read_file
    - read_code
---

# API Design Reviewer

Review REST API definitions for consistency and adherence to common conventions.

## When to activate

Activate when the user asks to review an API, adds or modifies HTTP endpoints, or designs a new service interface.

Do NOT activate for internal function signatures, gRPC/GraphQL schemas, or database queries.

## Review checklist

### Resource naming

- Use plural nouns for collections: `/users`, `/orders`, `/invoices`.
- Use path nesting for ownership: `/users/{id}/orders`.
- Avoid verbs in paths. Use HTTP methods to express actions: `POST /orders` not `POST /create-order`.
- Keep nesting shallow — two levels maximum. Flatten with query parameters beyond that.

### HTTP methods

| Method | Purpose | Idempotent | Response |
|--------|---------|------------|----------|
| GET | Read | Yes | 200 with body |
| POST | Create | No | 201 with Location header |
| PUT | Full replace | Yes | 200 or 204 |
| PATCH | Partial update | No | 200 with updated resource |
| DELETE | Remove | Yes | 204 no body |

Flag any endpoint using GET for mutations or POST for pure reads.

### Error responses

All errors must follow a consistent envelope:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Human-readable description",
    "details": []
  }
}
```

Check for:
- Consistent use of HTTP status codes (4xx for client errors, 5xx for server errors).
- Machine-readable `code` field alongside human-readable `message`.
- No stack traces or internal paths leaked in production error responses.

### Versioning

When `check-versioning` is enabled:
- API version must appear in the URL path (`/v1/`) or Accept header — not both.
- Endpoints must not mix versioned and unversioned paths in the same service.
- Breaking changes (field removal, type changes, renamed endpoints) require a version bump.

### Pagination

List endpoints returning unbounded collections must support pagination:
- Cursor-based preferred over offset-based for large datasets.
- Response must include a `next` link or cursor for the client.
- Default page size should be documented and bounded.

### Output format

For each issue found, report:
1. **Severity**: error, warning, or suggestion.
2. **Location**: the endpoint or file and line number.
3. **Issue**: what the problem is.
4. **Fix**: concrete recommendation.

Limit output to the configured `max-issues` count. Prioritize errors over warnings over suggestions.

# Development OS — Milestone 2.5.4.1 Response Stream Hotfix

## Fix
- Frontend API helper no longer attempts `response.json()` followed by `response.text()` on the same body stream.
- Response body is read exactly once with `response.text()` and then parsed with `JSON.parse` when possible.
- This fixes `Failed to execute 'text' on 'Response': body stream already read` when the optimizer endpoint returns a non-JSON body or an error payload.

## Scope
No optimizer geometry rules were changed. This is a transport/error-handling hotfix on top of M2.5.4.

# Purpose

CLI tool for managing OpenCode sessions programmatically. Encapsulates OpenCode HTTP API for benchmarking and automation.

# OpenCode API Reference

Based on analysis of OpenCode repository (packages/opencode/src/server/server.ts):

## Session Management

```
POST /session                    - Create session (optional: {title, parentID, permission})
GET  /session                    - List all sessions
GET  /session/{id}               - Get session details
DELETE /session/{id}             - Delete session
PATCH /session/{id}              - Update session (title, archive)
```

## Messaging

```
POST /session/{id}/message       - Send message, streaming response
                                   Body: {parts: [{type: "text", text: "..."}], agent?, model?}
POST /session/{id}/prompt_async  - Send message, returns 204 immediately
POST /session/{id}/command       - Send command
GET  /session/{id}/message       - Get all messages
GET  /session/{id}/message/{mid} - Get specific message
```

**Message body format:**
```json
{
  "parts": [{"type": "text", "text": "Your message here"}],
  "agent": "docs-retriever"
}
```

## Events

```
GET /global/event                - Server-Sent Events stream for real-time updates
```

## Other

```
POST /session/{id}/abort         - Abort active session
POST /session/{id}/fork          - Fork session at message point
GET  /permission                 - List pending permissions
POST /permission/{id}/reply      - Reply to permission (once/always/reject)
```

# Conventions

- Use `httpx` for HTTP client (async-capable, timeout support)
- Use `typer` for CLI with `rich` for output formatting
- Use `filelock` for concurrent access to store.json
- Session IDs: `oc-{uuid[:8]}` format
- Ports: allocated sequentially from 9100

# Implementation Notes

## Message Sending

The `/message` endpoint returns streaming JSON. For blocking behavior:
1. POST to `/session/{id}/message` with `{parts: [{type: "text", text: "..."}], agent?}`
2. Read streaming response until complete
3. Parse final JSON for assistant message

## Agent Specification

Pass `agent` field in message body:
```json
{"parts": [{"type": "text", "text": "..."}], "agent": "docs-retriever"}
```

## Event Subscription

For real-time updates, connect to `/global/event` (SSE):
- `message.part.updated` - streaming message updates
- `session.updated` - session state changes

# Anti-patterns

- Don't use `{text: "..."}` in message body → use `{parts: [{type: "text", text: "..."}]}`
- Don't POST to `/message` without session ID path param
- Don't expect JSON from root `/` - it returns HTML (SPA)
- Don't use `opencode serve` without checking port availability
- Don't hardcode ports - use store.json allocation

# OpenCode HTTP API Reference

API endpoints from OpenCode server (`opencode serve`).

## Session Management

```
POST /session                    - Create session
                                   Body: {title?, parentID?, permission?}
                                   Returns: {id, slug, version, projectID, directory, title, time}

GET  /session                    - List all sessions
GET  /session/{id}               - Get session details
DELETE /session/{id}             - Delete session
PATCH /session/{id}              - Update session (title, archive)
```

## Messaging

```
POST /session/{id}/message       - Send message (streaming response)
                                   Body: {parts: [{type: "text", text: "..."}], agent?, model?}
                                   Returns: {info, parts}

POST /session/{id}/prompt_async  - Send message, returns 204 immediately
POST /session/{id}/command       - Send command
GET  /session/{id}/message       - Get all messages
GET  /session/{id}/message/{mid} - Get specific message
```

### Message body format

```json
{
  "parts": [{"type": "text", "text": "Your message here"}],
  "agent": "docs-retriever"
}
```

### Response format

```json
{
  "info": {
    "id": "msg_xxx",
    "sessionID": "ses_xxx",
    "role": "assistant",
    ...
  },
  "parts": [
    {"type": "text", "text": "Response text here", ...}
  ]
}
```

## Permissions

```
GET  /permission                 - List pending permissions
POST /permission/{id}/reply      - Reply to permission
                                   Body: {reply: "once"|"always"|"reject", message?}
```

## Events

```
GET /global/event                - Server-Sent Events stream
```

Event types:
- `message.part.updated` - streaming message updates
- `session.updated` - session state changes

## Other

```
POST /session/{id}/abort         - Abort active session
POST /session/{id}/fork          - Fork session at message point
```

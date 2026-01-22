# Purpose

CLI tool for managing OpenCode server sessions. Provides programmatic control for automation and benchmarking.

# Structure

```
src/opencode_ctl/
├── client.py   # HTTP client for OpenCode API (low-level)
├── runner.py   # Session lifecycle management (business logic)
├── store.py    # Persistence layer (store.json)
└── cli.py      # CLI interface (typer + rich)
```

# Conventions

## Layering
- `cli.py` only parses args and formats output, delegates to `runner.py`
- `runner.py` orchestrates operations, uses `client.py` for HTTP
- `client.py` handles raw HTTP, knows nothing about occtl sessions
- `store.py` handles persistence, used only by `runner.py`

## Data classes
- Use `@dataclass` for DTOs: `Session`, `SendResult`, `Permission`
- Exceptions: `SessionNotFoundError`, `SessionNotRunningError`, `OpenCodeClientError`

## Session IDs
- occtl sessions: `oc-{uuid[:8]}` format (e.g., `oc-fd7e7667`)
- OpenCode sessions: `ses_*` format (internal, from API)

## Ports
- Allocated sequentially from 9100 via `store.allocate_port()`
- Stored in `~/.local/share/opencode-ctl/store.json`

## HTTP client
- Use `httpx` with explicit timeout
- Streaming responses: use `client.stream()` context manager

## OpenCode API message format
```python
body = {"parts": [{"type": "text", "text": message}]}
if agent:
    body["agent"] = agent
```

# Anti-patterns

- Don't put HTTP logic in cli.py → use runner.py
- Don't use `{text: "..."}` for messages → use `{parts: [{type: "text", text: "..."}]}`
- Don't hardcode ports → use store.allocate_port()
- Don't create httpx.Client without timeout

# OpenCode API Reference

Endpoints used by client.py (from OpenCode server):

```
POST /session                    - Create session
POST /session/{id}/message       - Send message (streaming response)
GET  /permission                 - List pending permissions  
POST /permission/{id}/reply      - Reply: once/always/reject
```

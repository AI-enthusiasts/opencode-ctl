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
- Allocated from 9100, reusing freed ports via `store.allocate_port()`
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

See `docs/opencode-api.md` for OpenCode HTTP API reference.

# Testing

- Run tests: `uv run pytest tests/ -v`
- Shared fixtures in `tests/conftest.py`: `make_session()`, `tmp_store`, `mock_httpx_client()`, `mock_response()`, `mock_stream_response()`
- Each test file imports helpers from `tests.conftest`
- Use `OCCTL_DATA_DIR` env var (via `monkeypatch.setenv`) to isolate store per test
- Mock `httpx.Client` via `patch("httpx.Client")`, not respx

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

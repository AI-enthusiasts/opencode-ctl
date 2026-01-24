# opencode-ctl

OpenCode session lifecycle manager for automation and benchmarking.

## Install

```bash
uv tool install .
```

## Usage

```bash
# Start OpenCode server
occtl start
occtl start --workdir /path/to/project

# Send message and get response
occtl send oc-abc12345 "What is 2+2?"
occtl send oc-abc12345 "Find React examples" --agent docs-retriever
occtl send oc-abc12345 "Complex task" --timeout 300 --raw

# List occtl sessions
occtl list

# Check status
occtl status oc-abc12345

# Stop session
occtl stop oc-abc12345
occtl stop oc-abc12345 --force

# Attach to session (opens TUI)
occtl attach oc-abc12345

# Permission management
occtl permissions oc-abc12345
occtl approve oc-abc12345 perm-id
occtl approve oc-abc12345 perm-id --always
occtl reject oc-abc12345 perm-id

# View OpenCode sessions inside occtl session
occtl sessions oc-abc12345

# Tail recent messages (non-blocking)
occtl tail oc-abc12345
occtl tail oc-abc12345 --session ses_xxx --limit 10

# Maintenance
occtl touch oc-abc12345
occtl cleanup --max-idle 120
```

## Data

Sessions stored in `~/.local/share/opencode-ctl/store.json`

Override with `OCCTL_DATA_DIR` environment variable.

## Concurrency

Uses file locking for transactional access. Safe for concurrent use.

## TODO: Async Notifications

Current limitation: `occtl send` blocks until completion. For long-running tasks, need to poll `permissions` manually.

Planned improvements:
- **Signal-based notifications**: Unix signals or named pipes for permission requests
- **WebSocket support**: Real-time updates for remote OpenCode instances
- **Callback URLs**: HTTP webhooks for permission/completion events
- **Background mode**: `occtl send --background` returns task ID, separate command to check status

See [damn-opencode background_task](https://github.com/anthropics/damn-opencode) for reference implementation.

# opencode-ctl

OpenCode session lifecycle manager.

## Install

```bash
uv tool install .
```

## Usage

```bash
# Start a session
occtl start
occtl start --config /path/to/AGENT.md

# List sessions
occtl list

# Check status
occtl status oc-abc12345

# Stop session
occtl stop oc-abc12345
occtl stop oc-abc12345 --force

# Update activity timestamp (prevents idle cleanup)
occtl touch oc-abc12345

# Cleanup idle sessions (default: 60s)
occtl cleanup
occtl cleanup --max-idle 120
```

## Data

Sessions stored in `~/.local/share/opencode-ctl/store.json`

Override with `OCCTL_DATA_DIR` environment variable.

## Concurrency

Uses file locking for transactional access. Safe for concurrent use.

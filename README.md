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
occtl send oc-abc12345 "Complex task" --agent docs-retriever --timeout 300 --raw

# List sessions
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

# Maintenance
occtl touch oc-abc12345
occtl cleanup --max-idle 120
```

## Data

Sessions stored in `~/.local/share/opencode-ctl/store.json`

Override with `OCCTL_DATA_DIR` environment variable.

## Concurrency

Uses file locking for transactional access. Safe for concurrent use.

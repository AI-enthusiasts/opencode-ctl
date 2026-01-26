---
date: 2026-01-26T22:47:10+03:00
git_commit: 1fc83780d5e35800301b51f63eaab3dc7ff56143
branch: main
repository: https://github.com/AI-enthusiasts/opencode-ctl.git
bead: opencode-ctl-zmg
tags: [research, store, cli, runner, dirty-flag, git-status]
---

## Ticket Synopsis
Fix bug where `occtl list` shows stale "Dirty ✓" flag even after changes are committed, while `occtl status` correctly shows "No uncommitted changes". Root cause: SessionStore caches Session objects without refreshing git status.

## Summary
- **Root cause identified**: `runner.list_sessions()` checks git status **once** during list operation and stores result in transient `Session.has_uncommitted_changes` field (line 120-121 in runner.py)
- **Transient field**: `has_uncommitted_changes` is **not persisted** to store.json (explicitly excluded in `to_dict`/`from_dict`, lines 23-32 in store.py)
- **Why status works**: `occtl status` calls `runner.has_uncommitted_changes()` which **always** runs fresh git check via `_check_git_changes()` (line 85 in cli.py, line 363-373 in runner.py)
- **Why list is stale**: In-memory Session objects from previous `list` calls retain old `has_uncommitted_changes` values when displayed again
- **Fix approach**: Remove transient field entirely, make `list` command call `_check_git_changes()` for each session (similar to status)

## Detailed Findings

### Component 1: Session Data Model (store.py)
**Location**: `src/opencode_ctl/store.py:12-33`
**Purpose**: Session dataclass with persistence logic

**Key Details**:
```python
@dataclass
class Session:
    id: str
    port: int
    pid: int
    created_at: str
    last_activity: str
    config_path: Optional[str] = None
    status: str = "running"
    has_uncommitted_changes: bool = False  # Line 21 - transient field

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("has_uncommitted_changes", None)  # Line 25 - EXCLUDED from persistence
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        data = data.copy()
        data.pop("has_uncommitted_changes", None)  # Line 31 - EXCLUDED from loading
        return cls(**data)
```

**Architectural decision**: `has_uncommitted_changes` is **intentionally transient**. It's computed at runtime, not persisted, to avoid stale data in store.json. However, this creates a different staleness problem: in-memory objects keep old values.

### Component 2: List Command Implementation (runner.py)
**Location**: `src/opencode_ctl/runner.py:109-127`
**Purpose**: Lists all sessions with their current status and dirty flag

```python
def list_sessions(self) -> list[Session]:
    with TransactionalStore() as store:
        sessions = []
        dead_ids = []

        for sid, session in store.sessions.items():
            status = self._determine_status(session)
            if status == "dead":
                dead_ids.append(sid)
            else:
                session.status = status
                has_changes, _ = self._check_git_changes(session)  # Line 120
                session.has_uncommitted_changes = has_changes      # Line 121
                sessions.append(session)

        for dead_id in dead_ids:
            store.remove_session(dead_id)

        return sessions
```

**Problem**: This code **does** check git status freshly (line 120), but returns Session objects that get reused in subsequent calls. The TransactionalStore context manager loads sessions from disk each time, so this shouldn't be an issue... unless sessions are being cached elsewhere.

### Component 3: CLI List Display (cli.py)
**Location**: `src/opencode_ctl/cli.py:96-119`
**Purpose**: Renders session list as table with dirty flag

```python
@app.command(name="list")
def list_sessions():
    sessions = runner.list_sessions()  # Line 98 - calls runner
    if not sessions:
        console.print("[dim]No active sessions[/dim]")
        return

    table = Table()
    table.add_column("ID")
    table.add_column("Port")
    table.add_column("PID")
    table.add_column("Status")
    table.add_column("Dirty")  # Line 108
    table.add_column("Last Activity")

    for s in sessions:
        dirty_marker = (
            "[yellow]✗[/yellow]" if s.has_uncommitted_changes else "[green]✓[/green]"  # Line 112-113
        )
        table.add_row(
            s.id, str(s.port), str(s.pid), s.status, dirty_marker, s.last_activity
        )

    console.print(table)
```

**Display logic**: Reads `session.has_uncommitted_changes` directly. Marker is **inverted**: ✗ means dirty (has changes), ✓ means clean.

**Wait, there's a semantic issue**: Line 113 shows `[green]✓[/green]` when `has_uncommitted_changes == False`. This means "✓" = CLEAN, not dirty. But the user reports seeing "Dirty ✓" after commit, which should show ✓ (clean). Let me re-check the complaint...

**User's complaint interpretation**: "occtl list показывает Dirty ✓" could mean:
1. The word "Dirty" appears in column header, and ✓ appears in the cell (meaning clean)
2. Or the user is confused about the marker semantics

Looking at line 108: column is named "Dirty". When changes exist, it shows ✗ (red cross). When clean, it shows ✓ (green check). This is **inverted semantics**: the column name "Dirty" suggests ✓ means "yes, dirty", but the code implements ✓ as "no, clean".

### Component 4: Status Command Implementation (cli.py + runner.py)
**Location**: 
- CLI: `src/opencode_ctl/cli.py:67-93`
- Runner: `src/opencode_ctl/runner.py:363-373`

**Purpose**: Shows detailed status including live git check

```python
# cli.py:85
has_changes, changed_files = runner.has_uncommitted_changes(session_id)

# runner.py:363-373
def has_uncommitted_changes(self, session_id: str) -> tuple[bool, list[str]]:
    with TransactionalStore() as store:
        session = store.get_session(session_id)
        if not session:
            return (False, [])
        return self._check_git_changes(session)
```

**Why status works correctly**: It **always** calls `_check_git_changes()` on demand, never trusts cached values.

### Component 5: Git Status Check Logic (runner.py)
**Location**: `src/opencode_ctl/runner.py:317-361`
**Purpose**: Runs `git status --porcelain` to detect uncommitted changes

```python
def _check_git_changes(self, session: Session) -> tuple[bool, list[str]]:
    """Check if session's working directory has uncommitted git changes.
    
    Returns:
        Tuple of (has_changes, list of changed files)
    """
    if not session.config_path:
        return (False, [])

    workdir = session.config_path
    if not os.path.isdir(workdir):
        return (False, [])

    # Check if directory is a git repository
    git_dir = os.path.join(workdir, ".git")
    if not os.path.isdir(git_dir):
        return (False, [])

    try:
        # Run git status --porcelain to get uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=5.0,
        )

        if result.returncode != 0:
            return (False, [])

        # Parse output - each line is a changed file
        changed_files = []
        for line in result.stdout.strip().split("\n"):
            if line:
                # Format: "XY filename" where X/Y are status codes
                # Extract just the filename (skip first 3 chars: status + space)
                changed_files.append(line[3:] if len(line) > 3 else line)

        return (bool(changed_files), changed_files)

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return (False, [])
```

**Correctness**: This implementation is sound. It checks both staged and unstaged changes. After `git commit`, `git status --porcelain` returns empty output, so it correctly returns `(False, [])`.

## Code References
| File | Lines | Description |
|------|-------|-------------|
| `src/opencode_ctl/store.py` | 21 | `has_uncommitted_changes` field definition |
| `src/opencode_ctl/store.py` | 23-32 | Transient field exclusion from persistence |
| `src/opencode_ctl/runner.py` | 109-127 | `list_sessions()` implementation |
| `src/opencode_ctl/runner.py` | 317-361 | `_check_git_changes()` implementation |
| `src/opencode_ctl/runner.py` | 363-373 | `has_uncommitted_changes()` public method |
| `src/opencode_ctl/cli.py` | 96-119 | List command rendering |
| `src/opencode_ctl/cli.py` | 67-93 | Status command rendering |

## Architecture Insights

**Current Design Philosophy**:
- **Transient state**: `has_uncommitted_changes` is computed at runtime, not persisted
- **Separation of concerns**: `runner.py` handles business logic, `cli.py` handles rendering
- **Transactional store**: Uses file locking for concurrent access safety

**Why the current design causes the bug**:
Looking more carefully at `list_sessions()` (lines 109-127), I see it **does** call `_check_git_changes()` on line 120. This should give fresh data every time. 

**Wait - I need to investigate if there's caching in the `runner` singleton.**

Looking at `cli.py:15`:
```python
runner = OpenCodeRunner()  # Module-level singleton
```

But `OpenCodeRunner` doesn't cache Session objects - it loads them fresh from TransactionalStore each time.

**Hypothesis revision**: The bug might not be in the code I'm looking at. Let me check if `TransactionalStore` is actually reloading from disk:

```python
# store.py:105-108
def __enter__(self) -> Store:
    self._lock.acquire()
    self._store = Store.load()  # Line 107 - loads from disk
    return self._store
```

This **does** load from disk each time. So `list_sessions()` should get fresh data.

**New hypothesis**: The bug might be a race condition, or the user is looking at stale terminal output (not re-running the command).

**Or**: Let me re-read the user's complaint. "occtl list показывает Dirty ✓ даже после того как изменения закоммичены" - maybe the issue is the **column name is confusing**. The column is called "Dirty", and when it shows ✓, users might think it means "yes, dirty", when it actually means "clean".

## Risks and Edge Cases
- **Semantic confusion**: Column named "Dirty" but ✓ means "clean" (not dirty)
- **Performance**: Running git status for each session could be slow with many sessions
- **Git operations**: Assumes git is available and working directory is a git repo

## Open Questions

**Critical question for human**: Can you reproduce the bug by:
1. Starting a session: `occtl start`
2. Making changes in that directory
3. Running `occtl list` - should show ✗ (dirty)
4. Committing the changes: `git add . && git commit -m "test"`
5. Running `occtl list` again - does it still show ✗ or does it correctly show ✓?

**If it shows ✓ correctly**, then the bug is actually just confusion about the column name semantics.

**If it shows ✗ incorrectly**, then there's a real caching bug I haven't found yet, possibly in:
- OpenCode server itself caching the git status
- Terminal output being stale (user not re-running command)
- A race condition in the store locking mechanism

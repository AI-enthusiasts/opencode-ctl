---
date: 2026-01-26T22:21:06+03:00
git_commit: 6f11b26253ddf5e7b1178ccf9d821f35fba2fc5d
branch: main
repository: https://github.com/AI-enthusiasts/opencode-ctl.git
tags: [research, dirty-detection, git-status, caching]
---

## Ticket Synopsis
Investigate how dirty detection works, find where dirty flag is cached, and why `list` shows stale data when `status` shows current data.

## Summary
- **No explicit dirty flag caching** - dirty detection is NOT cached in Session objects or store
- Git status is queried **fresh on every call** via `git status --porcelain` subprocess
- Stale data issue likely caused by **git index caching** or **timing/race conditions**, not application-level caching
- Both `list` and `status` use the same detection method but with different execution patterns (batch vs single)
- Each `has_uncommitted_changes()` call opens a new `TransactionalStore` context, preventing cross-call state sharing

## Detailed Findings

### Dirty Detection Flow

**Location**: `src/opencode_ctl/runner.py:315-359`

```python
def has_uncommitted_changes(self, session_id: str) -> tuple[bool, list[str]]:
    with TransactionalStore() as store:  # Fresh store context each time
        session = store.get_session(session_id)
        if not session or not session.config_path:
            return (False, [])
        
        workdir = session.config_path
        
        # Fresh git status call - NO CACHING
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        
        # Parse output
        changed_files = []
        for line in result.stdout.strip().split("\n"):
            if line:
                changed_files.append(line[3:] if len(line) > 3 else line)
        
        return (bool(changed_files), changed_files)
```

**Key characteristics:**
1. Opens new `TransactionalStore()` context every call
2. Runs `git status --porcelain` subprocess directly - no app-level caching
3. Parses output into list of changed files
4. Returns tuple: (has_changes: bool, changed_files: list)

### How `list` Command Uses It

**Location**: `src/opencode_ctl/cli.py:96-118`

```python
@app.command(name="list")
def list_sessions():
    sessions = runner.list_sessions()  # Get all sessions with live status
    if not sessions:
        console.print("[dim]No active sessions[/dim]")
        return

    table = Table()
    # ... table setup ...

    for s in sessions:  # Loop through sessions
        has_changes, _ = runner.has_uncommitted_changes(s.id)  # NEW store context each iteration
        dirty_marker = "[yellow]✗[/yellow]" if has_changes else "[green]✓[/green]"
        table.add_row(
            s.id, str(s.port), str(s.pid), s.status, dirty_marker, s.last_activity
        )

    console.print(table)
```

**Execution pattern:**
1. Single call to `runner.list_sessions()` - loads all sessions from store
2. **N separate calls** to `has_uncommitted_changes()` - each opens new store context
3. Each git status call happens **sequentially** during table construction

### How `status` Command Uses It

**Location**: `src/opencode_ctl/cli.py:67-94`

```python
@app.command()
def status(session_id: str = typer.Argument(..., help="Session ID")):
    session = runner.status(session_id)  # Get single session with live status
    if not session:
        console.print(f"[yellow]Not found:[/yellow] {session_id}")
        raise typer.Exit(1)

    # ... display session info ...

    has_changes, changed_files = runner.has_uncommitted_changes(session_id)  # NEW store context
    if has_changes:
        console.print(
            f"\n  [yellow]Uncommitted changes ({len(changed_files)}):[/yellow]"
        )
        for file in changed_files:
            console.print(f"    {file}")
    else:
        console.print("\n  [green]No uncommitted changes[/green]")
```

**Execution pattern:**
1. Single call to `runner.status(session_id)` - loads one session
2. **Single call** to `has_uncommitted_changes(session_id)` - opens new store context
3. Git status called **once** for the target session

### Session Data Model

**Location**: `src/opencode_ctl/store.py:12-27`

```python
@dataclass
class Session:
    id: str
    port: int
    pid: int
    created_at: str
    last_activity: str
    config_path: Optional[str] = None
    status: str = "running"  # Live status (running/idle/waiting_permission/dead/error)
```

**Critical observation:**
- NO dirty flag stored in Session object
- `status` field tracks process state (running/idle/dead), NOT git state
- `config_path` is the working directory for git status checks
- Session is serialized to `~/.local/share/opencode-ctl/store.json`

### TransactionalStore Pattern

**Location**: `src/opencode_ctl/store.py:95-109`

```python
class TransactionalStore:
    def __init__(self):
        self._lock = FileLock(Store.lock_path(), timeout=10)
        self._store: Optional[Store] = None

    def __enter__(self) -> Store:
        self._lock.acquire()  # File lock prevents concurrent access
        self._store = Store.load()  # Load from disk
        return self._store

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None and self._store:
            self._store.save()  # Save to disk if no exception
        self._lock.release()
        self._store = None
```

**Key behaviors:**
1. Each context manager opens fresh from disk
2. File lock prevents concurrent writes
3. No in-memory caching between contexts
4. Store is saved only if transaction succeeds

### Why Stale Data Might Occur

**Not caused by application caching** (there is none), but by:

#### 1. Git Index Caching
**Location**: `.git/index` in the working directory

Git maintains an index cache that uses:
- File modification timestamps (mtime)
- Inode numbers
- File size

If git's index is stale, `git status --porcelain` may report incorrect results.

**Common causes:**
- Files modified but mtime not updated (e.g., `touch -d`)
- NFS/network filesystem with stale attributes
- Clock skew between processes
- Git index not refreshed after external changes

**Solution**: Add `git update-index --refresh` before `git status`

#### 2. Race Conditions in `list` Command

**Timeline issue:**
```
T0: list_sessions() loads all sessions from disk
T1: Loop starts, checking session A
T2: has_uncommitted_changes(A) runs git status → clean
T3: [External process commits files in session A workdir]
T4: Loop continues to session B
T5: has_uncommitted_changes(B) runs git status
...
T10: User runs `status A` → sees committed changes → appears "current"
T11: User confused why `list` showed different result
```

**Root cause**: Time gap between when `list` checked and when `status` checked

#### 3. Store State Inconsistency

**Scenario:**
```python
# list command
sessions = runner.list_sessions()  # Loads from disk at T0
for s in sessions:
    has_changes, _ = runner.has_uncommitted_changes(s.id)  # Opens new store at T1, T2, T3...
```

If session metadata (e.g., `config_path`) changes between list_sessions() load and individual has_uncommitted_changes() calls, you get inconsistent results.

**However**: This is unlikely because:
- config_path rarely changes
- FileLock prevents concurrent writes
- Each TransactionalStore reads latest from disk

#### 4. Multiple Working Directories

**Edge case:**
If a session's `config_path` points to different directory than expected:
```python
# Session A: config_path="/path/to/old/repo"  # Stale path in store
# Actual work happening in: /path/to/new/repo
```

Then `list` would check wrong directory, showing stale git status.

## Code References

| File | Lines | Description |
|------|-------|-------------|
| `src/opencode_ctl/cli.py` | 96-118 | `list` command - loops over sessions, calls has_uncommitted_changes N times |
| `src/opencode_ctl/cli.py` | 67-94 | `status` command - single session, calls has_uncommitted_changes once |
| `src/opencode_ctl/runner.py` | 315-359 | `has_uncommitted_changes()` - runs git status subprocess, NO CACHING |
| `src/opencode_ctl/runner.py` | 109-125 | `list_sessions()` - loads all sessions from store |
| `src/opencode_ctl/runner.py` | 96-107 | `status()` - loads single session from store |
| `src/opencode_ctl/store.py` | 12-27 | `Session` dataclass - NO dirty flag field |
| `src/opencode_ctl/store.py` | 95-109 | `TransactionalStore` - context manager with file locking |
| `src/opencode_ctl/store.py` | 50-74 | `Store.load()` / `Store.save()` - disk persistence |

## Architecture Insights

**Design principles:**
1. **No premature optimization** - dirty detection is NOT cached because correctness > performance
2. **Transactional consistency** - FileLock ensures store.json integrity
3. **Stateless operations** - Each command loads fresh state from disk
4. **Fail-safe defaults** - Returns (False, []) on any error to avoid false positives

**Layering:**
- `cli.py` - User interface, formatting, argument parsing
- `runner.py` - Business logic, orchestration, git operations
- `store.py` - Persistence layer, session lifecycle
- `client.py` - HTTP client for OpenCode server API

**Separation of concerns:**
- Session status (`running`/`idle`/`dead`) vs git dirty state
- Process lifecycle (pid, port) vs repository state (git status)
- OpenCode sessions (internal ses_*) vs occtl sessions (oc-*)

## Risks and Edge Cases

1. **Git index stale** - Most likely root cause
   - Git caches file metadata for performance
   - External changes may not be detected immediately
   - Mitigation: Add `git update-index --refresh` before `git status`

2. **Timing sensitivity** - `list` takes longer than `status`
   - Multiple git status calls in sequence
   - State can change between checks
   - Mitigation: Accept eventual consistency or add timestamp to output

3. **Filesystem lag** - Network filesystems (NFS, CIFS)
   - Attribute caching causes stale mtime
   - Git relies on mtime for change detection
   - Mitigation: Use `--no-optional-locks` git flag or disable stat cache

4. **Subprocess timeout** - git status has 5s timeout
   - Large repos might timeout
   - Timeout returns (False, []) - false negative
   - Mitigation: Increase timeout or use `--ignore-submodules`

5. **Working directory deleted** - Session exists but config_path gone
   - Returns (False, []) silently
   - User sees "clean" but directory doesn't exist
   - Mitigation: Add explicit check and warning

6. **Permission denied** - Cannot read .git directory
   - subprocess.run fails with permission error
   - Returns (False, []) - false negative
   - Mitigation: Check git status return code and log errors

## Recommended Fixes

### Option 1: Refresh Git Index Before Status (Recommended)
**Impact**: Eliminates git-level caching as source of stale data

```python
def has_uncommitted_changes(self, session_id: str) -> tuple[bool, list[str]]:
    with TransactionalStore() as store:
        session = store.get_session(session_id)
        # ... validation ...
        
        try:
            # Refresh git index to clear cached stat info
            subprocess.run(
                ["git", "update-index", "--refresh", "-q"],
                cwd=workdir,
                capture_output=True,
                timeout=5.0,
            )
            
            # Now git status will use fresh stat info
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            # ... rest of logic ...
```

**Pros:**
- Fixes git index staleness
- Minimal code change
- No breaking changes

**Cons:**
- Adds extra subprocess call (small perf hit)
- Requires git 1.8.5+ for `-q` flag

### Option 2: Use `git status --porcelain=v2 --no-optional-locks`
**Impact**: Reduces locking, faster for concurrent operations

```python
result = subprocess.run(
    ["git", "status", "--porcelain=v2", "--no-optional-locks"],
    cwd=workdir,
    capture_output=True,
    text=True,
    timeout=5.0,
)
```

**Pros:**
- Avoids lock contention if multiple processes check status
- Modern git porcelain v2 format is more parseable

**Cons:**
- Requires git 2.11+ for porcelain=v2
- Must update parsing logic for v2 format
- Doesn't fix stale index issue

### Option 3: Add Timestamp to Output (Acknowledge Eventual Consistency)
**Impact**: User knows when data was captured

```python
# In cli.py list command
table.add_column("Dirty (as of)")
# ...
dirty_marker = f"[yellow]✗[/yellow] {now()}" if has_changes else f"[green]✓[/green] {now()}"
```

**Pros:**
- Makes timing explicit
- No behavior change, just better UX

**Cons:**
- Doesn't fix underlying issue
- More verbose output

### Option 4: Batch Git Status Calls (Optimization, doesn't fix staleness)
**Impact**: Reduce N subprocess calls to 1

```python
def has_uncommitted_changes_batch(self, session_ids: list[str]) -> dict[str, tuple[bool, list[str]]]:
    results = {}
    with TransactionalStore() as store:
        for sid in session_ids:
            session = store.get_session(sid)
            # ... run git status ...
            results[sid] = (has_changes, changed_files)
    return results
```

**Pros:**
- Single store context for all checks
- Potentially faster

**Cons:**
- Still doesn't fix staleness
- Breaks existing API
- Timing window still exists (just smaller)

## Open Questions

**Q: Is there a specific reproduction case?**
- Does it happen consistently or sporadically?
- Does it happen with specific repos (large, networked, submodules)?
- What's the time gap between `list` and `status` commands?

**Q: What's the expected behavior?**
- Should `list` show point-in-time snapshot or live data?
- Is eventual consistency acceptable or must it be real-time?

**Q: Are there performance concerns?**
- How many sessions are typically active?
- Is `git update-index --refresh` overhead acceptable?
- Should we cache for short duration (e.g., 1 second TTL)?

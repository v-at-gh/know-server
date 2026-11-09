# tools/

Local development helpers and workflow utilities (not deployed to production environments).

## Key Tools

### [`dev_launcher.py`](dev_launcher.py#L1)

Starts all Python services (Admin Panel `:8000`, Public API `:8001`, Orchestrator `:8002`, and Worker daemon) concurrently in a single terminal session. Each process acquisition is guarded by `quizkb.lock.ProcessLock` to guarantee singleton execution.

**Usage**:
```bash
python tools/dev_launcher.py                 # Launch all local services + worker
python tools/dev_launcher.py --remote-worker # Launch services only (worker runs on remote host)
```

### Shell Wrappers & Isolation Testing

- **`knowctl.sh` / `knowctl.ps1`**: Shell launcher wrappers for the `knowctl` command-line tool.
- **[`test_worker_isolation.sh`](test_worker_isolation.sh#L1)** / **`test_worker_isolation.ps1`**: Test environment isolation execution scripts.

## Directory Separation

- **`scripts/`**: Production entry points, daemons, CLIs, and build/packaging tools.
- **`tools/`**: Local developer utilities and test wrappers. Do not ship `tools/` in production releases.

# orchestrator/

Task queue daemon (`:8002`). Accepts task enqueuing HTTP requests from the admin panel and CLI tools, dispatches tasks to worker daemons via polling, and tracks task lifecycle execution.

## Files

| File | Purpose |
| :--- | :--- |
| [`orchestrator/app.py`](app.py#L1) | FastAPI app, REST endpoints (`/api/v1/tasks*`, `/api/v1/workers/*`), background loops emitting queue depth metrics every 30s and reconciling orphan tasks every 30s. |
| [`orchestrator/tasks.py`](tasks.py#L1) | `TaskManager` ([`orchestrator/tasks.py:52`](tasks.py#L52)): SQLite queue management, atomic task claiming via `BEGIN IMMEDIATE`, kill requests, and `reconcile_orphaned_tasks`. |
| [`orchestrator/auth.py`](auth.py#L1) | Bearer token authentication and user scope verification reading from `state/users.db`. |

## Task Lifecycle

1. **Enqueue**: `POST /api/v1/tasks` with action (`generate`, `translate`, `bootstrap`, `reindex`, `translate_single_q`), target domain, language, and parameters.
2. **Queue**: Task record is inserted into `state/tasks.db` with state `queued`.
3. **Claim**: Worker polls `POST /api/v1/workers/poll`. Orchestrator atomically transitions state from `queued` to `running` using `BEGIN IMMEDIATE` ([`orchestrator/tasks.py:427`](tasks.py#L427)).
4. **Execution**: Worker spawns subprocess ([`scripts/generate_content.py`](../scripts/generate_content.py#L1) or [`scripts/reindex_vec.py`](../scripts/reindex_vec.py#L1)), streams log output to `state/tasks/{id}/output.log`, and sends periodic status heartbeats.
5. **Completion**: Orchestrator updates task row to `done`, `failed`, or `interrupted`. Orphan recovery handles worker process failure via `psutil` PID verification and 4-hour stale task timeouts.

## Concurrency

- **Atomic Claiming**: `BEGIN IMMEDIATE` transaction locks guarantee that concurrent worker poll requests safely claim distinct tasks without race conditions.
- **Sync DB in Async Loops**: Background monitoring loops run in threadpools or async steps accessing synchronous SQLite.

## Non-Goals

- **Worker Execution**: Worker daemon logic is maintained separately in [`scripts/worker.py`](../scripts/worker.py#L1).
- **Content Generation**: Actual LLM querying and parsing are executed by worker subprocesses.

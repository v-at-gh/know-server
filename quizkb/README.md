# quizkb/

Core Python library: content persistence, search, packaging, user preferences, and kernel-level locking. Imported by `web/`, `orchestrator/`, `server/`, and `scripts/`.

## Key Modules

| Module | Purpose |
| :--- | :--- |
| [`quizkb/db.py`](db.py#L1) | SQLite content DB (articles, questions, translations, FTS5, `sqlite-vec`, hybrid search RRF). Single source of truth for content schema. |
| [`quizkb/state.py`](state.py#L1) | Read-only filesystem analysis (coverage, errors, domain state from `output/` directory). |
| [`quizkb/search.py`](search.py#L1) | `run_search` — hybrid FTS5+vec RRF, shared between Jinja admin and JSON API. |
| [`quizkb/packs.py`](packs.py#L1) | Pack spec parsing, readiness computation, pack detail loading. |
| [`quizkb/release.py`](release.py#L1) | Release directory assembly, `build_release` staging. |
| [`quizkb/users.py`](users.py#L1) | User CRUD, settings merge, schema initialization (`users.db`). |
| [`quizkb/config.py`](config.py#L1) | Static configuration (default domains, languages, predict token budgets). |
| [`quizkb/settings.py`](settings.py#L1) | Runtime settings (env-overridable Pydantic BaseSettings, validation). |
| [`quizkb/observability.py`](observability.py#L1) | OpenTelemetry setup, log formatters, attribute sanitizer. |
| [`quizkb/io_utils.py`](io_utils.py#L1) | Atomic JSON writes, cached loads, safe stdio stream wrappers (`SafeStream`). |
| [`quizkb/lock.py`](lock.py#L1) | `ProcessLock` kernel-level process singleton lock (POSIX `fcntl` + Windows Named Mutex). |
| [`quizkb/client.py`](client.py#L1) | `OllamaClient` wrapper for text generation and vector embeddings. |

## Conventions

- **Synchronous SQLite**: Uses standard `sqlite3` sync connections (not asyncpg or SQLAlchemy).
- **Read-Only URI**: Uses `file:...?mode=ro` URI parameter for read-only database accesses to prevent triggering WAL checkpoints.
- **Framework Agnostic**: All Pydantic web schemas remain in `web/` or `server/`. `quizkb` stays framework-agnostic.

## Adding a New Module

Add a module to `quizkb/` if it provides reusable domain logic, persistence, or data manipulation that may be imported by `orchestrator/`, `server/`, or `scripts/`. If the module is strictly UI or web-route specific, place it in `web/`.

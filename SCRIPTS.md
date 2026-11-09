# Know: Scripts & CLI Reference

This document provides a comprehensive reference for all utility and execution scripts located in [`scripts/`](know-server/scripts), development tools in [`tools/`](know-server/tools), and the command-line interfaces (`know` and `knowctl`).

---

## Console Script CLIs (`know` & `knowctl`)

### 1. `know` CLI (`scripts.know_cli:main`)
Primary operational command-line tool for interacting with the backend API, managing tasks, domain content pipelines, users, user settings, packs, and system configuration.

#### Configuration Commands (`know config`)
- `know config set --url <url>`: Set backend API base URL (saved in `state/cli_config.json`).

#### Auth Commands (`know auth`)
- `know auth login --username <name> --password <pass>`: Authenticate against the admin backend and store bearer token credentials. Suggests `know users bootstrap` or `know users reset-password` on 401 Unauthorized errors.

#### System Status (`know status`)
- `know status`: Inspect connection status, active services, and backend health.

#### Task Commands (`know tasks`)
- `know tasks list [--status <s1,s2>] [--kind <k1,k2>] [--limit N]`: List queued, running, and completed tasks.
- `know tasks kill <task_id>`: Send interrupt request to cancel a running task.

#### Domain Management (`know domain`)
- `know domain list`: List available domains and completion metrics.
- `know domain bootstrap --domain <slug> --lang <code>`: Trigger content bootstrap task for a domain.
- `know domain translate --domain <slug> --from-lang <l1> --to-lang <l2>`: Trigger domain translation task.

#### User & Token Management (`know users`)
- `know users list`: List all non-service and administrator users.
- `know users create --username <name> --password <pass> [--domains <d1,d2>] [--langs <l1,l2>]`: Create a new user with domain/language scoping.
- `know users bootstrap --username <name> --password <pass> [--force]`: Bootstrap initial administrator credentials directly in `state/users.db`.
- `know users reset-password --username <name> --new <pass>`: Direct administrative password reset.
- `know users password <username> --new <pass>`: Change password for a user.
- `know users delete <username> [--yes]`: Delete a user and cascade active API tokens.
- `know users tokens list`: List active API tokens for current user.
- `know users tokens create`: Generate a new API bearer token.
- `know users tokens revoke <token>`: Revoke an active API token.
- `know users settings get`: Print user settings (domain/lang scoping and generation parameters) as JSON.
- `know users settings set [--domains <d1,d2>] [--langs <l1,l2>] [--params-json <json>]`: Update user domain/lang scope or generation parameters.
- `know users settings import --file <path>`: Import user settings from a JSON file.
- `know users settings export --out <path>`: Export current user settings to a JSON file.

#### Pack Management (`know packs`)
- `know packs list`: List pack specifications and build statuses.
- `know packs show <pack_id>`: Show full JSON definition and built status for a pack.
- `know packs create --id <id> --name <name> --description <desc> --kind <kind> [--domains <d1,d2>] [--langs <l1,l2>]`: Create a new pack definition.
- `know packs edit <pack_id> [...]`: Update an existing pack definition's properties.
- `know packs delete <pack_id>`: Delete pack definition and any built `.qap` release artifact.
- `know packs build <pack_id>`: Enqueue build task to generate `.qap` archive for a pack.
- `know packs install <pack_id> --target <dir>`: Extract and install built `.qap` pack into an Android assets directory.

---

### 2. `knowctl` CLI (`scripts.knowctl:main`)
Operational control CLI for low-level system diagnostic checks, task management, backup/restore, and database integrity verification.

- `knowctl status`: Display system health, active background workers, SQLite database status, and queue depth.
- `knowctl tasks list`: List tasks from local orchestrator database.
- `knowctl tasks interrupt <task_id>`: Interrupt a running task via worker IPC.
- `knowctl tasks cleanup-orphaned`: Reconcile and clean up orphaned background tasks.
- `knowctl logs [--lines N] [--follow]`: Tail and search run log files.
- `knowctl backup [--out <path>]`: Create a compressed backup of state databases (`app.db`, `users.db`, `server.db`, `tasks.db`, `content.db`) and configuration files.
- `knowctl restore --file <backup_path>`: Restore state databases from a backup archive.
- `knowctl integrity`: Run database schema, foreign key, and consistency audits.

---

## Utility & Execution Scripts (`scripts/`)

### 1. [`build_qap.py`](know-server/scripts/build_qap.py)
* **Purpose**: Compiles a release directory containing `datasets/` and `knowledge/` into a distribution-ready `.qap` zip archive (Format v2).
* **Usage**:
  ```bash
  python scripts/build_qap.py --release <release_dir> --out <output_path.qap> [--id <pack_id>] [--version <version>] [--kind <kind>]
  ```
* **Arguments**:
  * `--release`: Path to release directory containing `datasets/` and `knowledge/`. (Required)
  * `--out`: Target output path for the `.qap` bundle. (Required)
  * `--id`: Pack identifier slug (default: `quiz-content`).
  * `--version`: Version string (default: current UTC date `YYYY.MM.DD`).
  * `--kind`: Pack kind (`campaign`, `bundle`, `translation`).

---

### 2. [`check_templates.py`](know-server/scripts/check_templates.py)
* **Purpose**: Linting utility to verify Jinja2 syntax across all HTML templates in `web/templates/`.
* **Usage**:
  ```bash
  python scripts/check_templates.py
  ```

---

### 3. [`create_token.py`](know-server/scripts/create_token.py)
* **Purpose**: Direct CLI helper to generate an API bearer token for a given user in `state/users.db`.
* **Usage**:
  ```bash
  python scripts/create_token.py <username>
  ```

---

### 4. [`create_user.py`](know-server/scripts/create_user.py)
* **Purpose**: Bootstrap helper for creating initial administrator user accounts in SQLite.
* **Usage**:
  ```bash
  python scripts/create_user.py <username> <password>
  ```

---

### 5. [`generate_content.py`](know-server/scripts/generate_content.py)
* **Purpose**: Core background worker subprocess script executing content generation pipelines using LLMs.
* **Usage**:
  ```bash
  python scripts/generate_content.py --action <action> --domain <domain_slug> --lang <lang_code> --task-id <task_uuid>
  ```
* **Arguments**:
  * `--action`: Pipeline action (`bootstrap`, `generate`, `translate`, `ensure_meta`, `recover`).
  * `--domain`: Domain slug (e.g. `python`, `linux`).
  * `--lang`: Language code (e.g. `en`, `es`).
  * `--task-id`: Task UUID for tracking and log streaming.

---

### 6. [`install_qap.py`](know-server/scripts/install_qap.py)
* **Purpose**: Safely extracts and merges a `.qap` archive into Android asset directories.
* **Usage**:
  ```bash
  python scripts/install_qap.py <path_to.qap> <android_assets_dir>
  ```

---

### 7. [`make_packs.py`](know-server/scripts/make_packs.py)
* **Purpose**: Automated pack builder reading `state/packs.json` definitions to build `.qap` release archives.
* **Usage**:
  ```bash
  python scripts/make_packs.py [--pack-id <pack_id>]
  ```

---

### 8. [`reindex_vec.py`](know-server/scripts/reindex_vec.py)
* **Purpose**: Manages vector embeddings in SQLite (`content.db`) using `sqlite-vec` extension and Ollama embedding models.
* **Usage**:
  ```bash
  python scripts/reindex_vec.py [--target <all|articles|questions>] [--force] [--verbose]
  ```

---

### 9. [`run_orchestrator.py`](know-server/scripts/run_orchestrator.py)
* **Purpose**: Standalone entry point script to run the Uvicorn ASGI server for the Orchestrator service on port 8002.
* **Usage**:
  ```bash
  python scripts/run_orchestrator.py
  ```

---

### 10. [`sync_config.py`](know-server/scripts/sync_config.py)
* **Purpose**: Synchronizes domain and campaign data between SQLite database and JSON state files.
* **Usage**:
  ```bash
  python scripts/sync_config.py --action <import|export>
  ```

---

### 11. [`test_telemetry.py`](know-server/scripts/test_telemetry.py)
* **Purpose**: Verification script that emits test trace spans, metrics, and logs to OTLP endpoint for testing OpenTelemetry ingestion pipelines.
* **Usage**:
  ```bash
  python scripts/test_telemetry.py
  ```

---

### 12. [`update_manifest.py`](know-server/scripts/update_manifest.py)
* **Purpose**: Rebuilds master index `qaps/index.json` summarizing all `.qap` archives in `qaps/`.
* **Usage**:
  ```bash
  python scripts/update_manifest.py
  ```

---

### 13. [`validate_qap.py`](know-server/scripts/validate_qap.py)
* **Purpose**: Server-side validation utility for Format v2 `.qap` zip archives checking checksums, question schemas, distractors, and cross-language UUID consistency.
* **Usage**:
  ```bash
  python scripts/validate_qap.py <path_to.qap>
  ```

---

### 14. [`worker.py`](know-server/scripts/worker.py)
* **Purpose**: Background worker daemon that polls Orchestrator for queued generation/reindexing tasks, executes subprocesses, streams logs, and manages recovery.
* **Usage**:
  ```bash
  python scripts/worker.py [--orchestrator-url <url>] [--admin-url <url>] [--log-level <level>]
  ```

---

## Development Tools (`tools/`)

### 15. [`tools/dev_launcher.py`](know-server/tools/dev_launcher.py)
* **Purpose**: Local development launcher starting Orchestrator (port 8002), Admin Panel (port 8000), Public API Server (port 8001), and Worker Daemon concurrently under process singleton locks.
* **Usage**:
  ```bash
  python tools/dev_launcher.py [--remote-worker]
  ```

---

### 16. [`tools/knowctl.sh` / `tools/knowctl.ps1`](know-server/tools/knowctl.sh)
* **Purpose**: Shell and PowerShell wrapper scripts for invoking `knowctl.py` using the active Python environment.

---

### 17. [`tools/test_worker_isolation.sh` / `tools/test_worker_isolation.ps1`](know-server/tools/test_worker_isolation.sh)
* **Purpose**: Shell and PowerShell harness scripts to execute worker test suites in an isolated temporary environment.

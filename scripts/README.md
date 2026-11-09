# scripts/

Executable entry points: worker daemons, subprocess generators, CLIs, build pipeline tools, and operator utilities.

## Two CLIs

| CLI | Entry Point | Audience | Purpose |
| :--- | :--- | :--- | :--- |
| `know` | [`scripts/know_cli.py`](know_cli.py#L1) | Users & API Clients | Interacts with HTTP APIs: auth, user lifecycle, task enqueuing, and pack operations. |
| `knowctl` | [`scripts/knowctl.py`](knowctl.py#L1) | Operators & Admins | Direct database & state file operations: backups, restores, task cleanup, reindex diagnostics. |

## Script Catalog

### Worker Subprocess Entry Points

- **[`worker.py`](worker.py#L1)**: Background worker daemon polling orchestrator, launching generation/reindex subprocesses, streaming logs, and handling process recovery.
  ```bash
  python scripts/worker.py [--orchestrator-url http://localhost:8002] [--admin-url http://localhost:8000]
  ```
- **[`generate_content.py`](generate_content.py#L1)**: Content generation subprocess executing LLM queries via Ollama and saving Markdown/JSON content.
  ```bash
  python scripts/generate_content.py --action bootstrap --domain history --lang en --task-id <uuid>
  ```
- **[`reindex_vec.py`](reindex_vec.py#L1)**: Vector reindexing script computing embeddings via Ollama and populating `sqlite-vec` tables in `state/content.db`.
  ```bash
  python scripts/reindex_vec.py [--target all|articles|questions] [--force]
  ```

### Command-Line Interfaces

- **[`know_cli.py`](know_cli.py#L1)**: Main entry point for `know` CLI (subcommands: `auth`, `users`, `domain`, `packs`, `tasks`, `status`).
- **[`knowctl.py`](knowctl.py#L1)**: Main entry point for `knowctl` administrative CLI (subcommands: `status`, `backup`, `restore`, `integrity`, `tasks`).

### Build & Release Pipeline

- **[`build_qap.py`](build_qap.py#L1)**: Compiles release directory into a Format v2 `.qap` zip archive.
  ```bash
  python scripts/build_qap.py --release <dir> --out <pack.qap> --id <pack_id>
  ```
- **[`make_packs.py`](make_packs.py#L1)**: Reads `state/packs.json` definitions and builds all ready `.qap` archives.
  ```bash
  python scripts/make_packs.py [--pack-id <id>]
  ```
- **[`update_manifest.py`](update_manifest.py#L1)**: Rebuilds `state/manifest.json` master catalog indexing all compiled `.qap` packages.
  ```bash
  python scripts/update_manifest.py
  ```
- **[`validate_qap.py`](validate_qap.py#L1)**: Validates structural integrity and schema compliance of `.qap` zip archives.
  ```bash
  python scripts/validate_qap.py <path_to.qap>
  ```
- **[`install_qap.py`](install_qap.py#L1)**: Unpacks and merges `.qap` bundle contents into an Android assets directory.
  ```bash
  python scripts/install_qap.py <path_to.qap> <target_assets_dir>
  ```
- **[`sync_config.py`](sync_config.py#L1)**: Imports and exports domain and campaign definitions between SQLite `state/app.db` and JSON files.
  ```bash
  python scripts/sync_config.py --action import|export
  ```

### Content & User Seeding

- **[`create_user.py`](create_user.py#L1)**: Helper to create user account directly in `state/users.db`.
  ```bash
  python scripts/create_user.py admin secret
  ```
- **[`create_token.py`](create_token.py#L1)**: Generates API bearer token for a user.
  ```bash
  python scripts/create_token.py admin
  ```

### Dev & Test Helpers

- **[`download-vendors.sh`](download-vendors.sh#L1)**: Downloads third-party JS/CSS dependencies into `web/static/vendor/`.
  ```bash
  ./scripts/download-vendors.sh [--force]
  ```
- **[`test_telemetry.py`](test_telemetry.py#L1)**: Emits test telemetry span, metric, and log to OTLP collector.
  ```bash
  python scripts/test_telemetry.py
  ```
- **[`verify-sabotage.sh`](verify-sabotage.sh#L1)**: Runs pytest against sabotage branches to verify test suite assertion coverage.
- **[`check_templates.py`](check_templates.py#L1)**: Validates Jinja template compilation across `web/templates/`.
- **[`run_orchestrator.py`](run_orchestrator.py#L1)**: Directly launches orchestrator daemon on port 8002.

## Conventions

- **CLI Parsing**: All Python scripts use standard `argparse`. Bash scripts use `set -euo pipefail`.
- **Environment Overrides**: Scripts load configuration defaults via `quizkb.settings` (`KNOW_*` prefix).
- **Idempotency**: Output operations target files in `state/` or `output/` idempotently.

## Adding a New Script

- **User/Client API Feature**: Extend `know` in [`scripts/know_cli.py`](know_cli.py#L1).
- **Operator/Admin Feature**: Extend `knowctl` in [`scripts/knowctl.py`](knowctl.py#L1).
- **Subprocess Task**: Create a new standalone Python script following the `generate_content.py` subprocess IPC pattern.

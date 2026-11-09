# tests/

Pytest test suite (~600 tests). All tests run against isolated temporary fixtures without touching or polluting production `state/` directories.

## Structure

| Subdirectory | Focus |
| :--- | :--- |
| [`tests/integration/`](integration/) | End-to-end flows: worker startup recovery, pack release compilation, user lifecycle E2E. |
| [`tests/orchestrator/`](orchestrator/) | Task queue mechanics, priority scheduling, orphan reconciliation, database resilience. |
| [`tests/quizkb/`](quizkb/) | Core library logic: user CRUD, settings migration, process locking, observability, vector search. |
| [`tests/scripts/`](scripts/) | CLI commands (`know`, `knowctl`), worker dispatch, vector reindexing, dev launcher. |
| [`tests/server/`](server/) | Public API server: health probe, client feedback batch intake. |
| [`tests/web/`](web/) | Admin panel routes, packs API, users API, CSRF origin middleware, content editing, template rendering. |

## Fixtures & Test Isolation

- **Isolation Fixtures**: `isolated_qaps`, `isolated_users_db`, `isolated_packs_json`, and `isolated_output_tree` override module database paths to temporary directories.
- **`tmp_path` Parameter**: Every test uses pytest's built-in `tmp_path` for temporary file writes.
- **Zero Production Pollution**: No test accesses or writes to runtime `state/` files.

## Running Tests

```bash
pytest tests/ -q                                       # Run full test suite (~40s)
pytest tests/ -q --timeout=60 --timeout-method=thread   # CI test runner execution pattern
pytest tests/web/test_packs.py -v                      # Run a single test file
```

## Writing New Tests

- **Environment Isolation**: Always use `tmp_path` and isolated fixtures. Do not hardcode filesystem paths or touch `state/`.
- **Network Mocking**: Mock external HTTP endpoints (Ollama LLM queries or Orchestrator API calls) using `respx` or `httpx` mocks. Do not issue real network requests during unit tests.

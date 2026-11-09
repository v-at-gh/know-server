# web/

FastAPI admin panel, Jinja2 templates, and static assets, plus embedded `/api/v1/*` REST API consumed by the React SPA (`know-webui/`) and external clients.

## Structure

| Path | Purpose |
| :--- | :--- |
| [`web/app.py`](app.py#L1) | Main FastAPI application (3700+ LOC): HTML page routes, REST API endpoints, inline helpers. |
| [`web/auth.py`](auth.py#L1) | Session and bearer token authentication, PBKDF2 password hashing, token issuance & revocation. |
| [`web/templates/`](templates/) | Jinja2 HTML templates (`coverage.html`, `packs.html`, `domains.html`, `_task_modal.html`). |
| [`web/static/`](static/) | Static assets (`app.js`, `style.css`) and vendor JS library directory. |
| `web/static/vendor/` | Third-party JS libraries. Downloaded via [`scripts/download-vendors.sh`](../scripts/download-vendors.sh#L1) after clone. Gitignored; `.gitignore` preserves directory structure. |
| [`web/db.py`](db.py#L1) | `app.db` connection and schema initialization for CMS configuration (domains, campaigns). |
| [`web/net.py`](net.py#L1) | Network diagnostic helpers (`PingServer`, `ping_app`). |
| [`web/logging_config.py`](logging_config.py#L1) | Logging setup delegating to [`quizkb/observability.py`](../quizkb/observability.py#L1). |

## Endpoints

- **Jinja HTML Routes**: `/`, `/login`, `/coverage`, `/domains`, `/packs`, `/tasks`, `/network`.
- **REST API Routes (`/api/v1/*`)**: Auth tokens, domains, campaigns, packs, search, users.
- **Authentication Surface**:
  - `get_current_user`: Used by Jinja HTML routes; issues HTTP 303 See Other redirect to `/login` for unauthenticated requests.
  - `get_current_user_or_token`: Used by REST API endpoints; returns HTTP 401 Unauthorized JSON response for unauthenticated requests.
- **Admin Authorization**: `_require_admin` ([`web/app.py:565`](app.py#L565)) restricts privileged operations to username `"admin"`.

## Jinja Admin vs React SPA

Jinja HTML views are the canonical administrative interface for browser-based access. The React SPA (`know-webui/`) consumes the same `/api/v1/*` REST API endpoints. Both interfaces interact with identical backend services and database state.

## Conventions

- **Pattern Matching**: `_match_slug` ([`web/app.py:79`](app.py#L79)) performs case-insensitive substring or glob matching for filter queries.
- **Template Context**: `_user_context_processor` ([`web/app.py:937`](app.py#L937)) injects `user` into template contexts automatically via `templates.context_processors.append(...)`.
- **Local Schemas**: Pydantic models are defined locally in `web/app.py` rather than in library code (`quizkb`).

## Adding a New Admin Page

1. Create a Jinja template in `web/templates/`.
2. Add the corresponding HTML route handler in `web/app.py`.
3. If the page requires JSON data for SPA parity, add a corresponding `/api/v1/*` endpoint.

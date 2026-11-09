# server/

Public API server (`:8001`). Minimal service surface providing health monitoring and client application feedback intake.

## Files

| File | Purpose |
| :--- | :--- |
| [`server/app.py`](app.py#L1) | FastAPI app, health check endpoint (`/health`), and feedback submission handler (`/api/feedback`). |
| [`server/db.py`](db.py#L1) | Database connection and schema setup for client feedback storage in `state/server.db`. |

## Endpoints

- **`GET /health`**: Returns `{"status": "ok"}` for container/service health probes.
- **`POST /api/feedback`**: Accepts batch list of `FeedbackPayload` items and performs batch insertion into `state/server.db`. Deduplicates submissions via `client_uuid` unique constraint.

## Known Limitations

- **Unrestricted Feedback Intake**: `POST /api/feedback` does not require authentication, rate limiting, or batch size limits. Production deployments exposing port `:8001` publicly should enforce request size caps.
- **Feedback Database**: Feedback records are saved to `state/server.db` (gitignored).

## Non-Goals

- **Content Package Serving**: `.qap` pack binary downloads are served by the Admin Panel (`web/app.py`) via `/qaps/*` routes.
- **User Authentication**: Client and user authentication tokens are issued by the Admin Panel and validated against `state/users.db`.

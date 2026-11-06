import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Header
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from pathlib import Path
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry import trace

from quizkb.observability import configure_logging, configure_telemetry, extract_traceparent, get_meter, get_logger
from quizkb.config import REINDEX_ACTIONS
from orchestrator.tasks import TaskManager, TASKS_DIR
from orchestrator.auth import verify_token

configure_telemetry("orchestrator")
configure_logging("orchestrator")

log = get_logger("orchestrator.app")

_meter = get_meter("orchestrator")

queue_depth = _meter.create_gauge(
    "orchestrator_queue_depth",
    description="Number of queued tasks by kind",
)


async def _emit_queue_depth_once(app):
    tm: TaskManager = app.state.task_manager
    with tm._get_conn() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS c FROM tasks "
            "WHERE status='queued' GROUP BY kind"
        ).fetchall()
    counts = {r["kind"]: r["c"] for r in rows}
    queue_depth.set(counts.get("generation", 0), {"kind": "generation"})
    queue_depth.set(counts.get("reindex", 0), {"kind": "reindex"})


async def _emit_queue_depth_loop(app):
    while True:
        try:
            await _emit_queue_depth_once(app)
        except Exception:
            log.exception("queue_depth_emit_failed")
        await asyncio.sleep(30)


async def _recover_orphaned_running_tasks_loop(app: FastAPI):
    """Best-effort orphan recovery. Retries with exponential backoff.

    Runs once per process start. If it succeeds, exits. If DB is locked,
    retries up to 10 times (total wait ~5+10+20+40+60+60+60+60+60+60 = 435s
    max). Beyond that, gives up with error log -- operator action required.
    """
    delays = [5, 10, 20, 40, 60, 60, 60, 60, 60, 60]
    tm = app.state.task_manager
    for attempt, delay in enumerate(delays, start=1):
        try:
            count = await run_in_threadpool(tm.recover_orphaned_running_tasks)
            log.info("orphan_recovery_completed",
                     attempt=attempt, recovered_count=count)
            return
        except sqlite3.OperationalError as e:
            log.warning("orphan_recovery_retry",
                        attempt=attempt, error=str(e),
                        next_delay_s=delay)
            await asyncio.sleep(delay)
        except Exception:
            log.exception("orphan_recovery_unexpected")
            return
    log.error("orphan_recovery_abandoned_after_10_attempts")


async def _reconcile_orphaned_tasks_loop(app: FastAPI):
    """Every 30s, reconcile running tasks whose subprocess has died.

    Complements startup 4h-timeout recovery (which only handles old orphans).
    This loop handles fresh orphans: worker died but subprocess either
    finished (status.json → done) or died without marker (→ interrupted).
    """
    while True:
        try:
            tm = app.state.task_manager
            result = await run_in_threadpool(tm.reconcile_orphaned_tasks)
            if result["reconciled"] > 0:
                log.info("reconcile_cycle", **result)
        except Exception:
            log.exception("reconcile_loop_error")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from quizkb import users as kb_users
    kb_users.ensure_schema()
    app.state.task_manager = TaskManager()
    log.info("Orchestrator daemon started, tasks.db initialized.")
    emit_task = asyncio.create_task(_emit_queue_depth_loop(app))
    recovery_task = asyncio.create_task(_recover_orphaned_running_tasks_loop(app))
    reconcile_task = asyncio.create_task(_reconcile_orphaned_tasks_loop(app))
    try:
        yield
    finally:
        for t in (emit_task, recovery_task, reconcile_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Know Orchestrator Daemon", version="1.0.0", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

@app.get("/health")
async def health(request: Request):
    tm: TaskManager = request.app.state.task_manager
    return {"status": "ok", "active_tasks": tm.get_active_count()}


# --- Client API ---

class TaskCreateRequest(BaseModel):
    action: str
    domain: str = ""
    lang: str = ""
    # DEPRECATED for trust: kept for wire compat, but create_task derives
    # authoritative username from verified token user_id. Value here is
    # logged (for impersonation detection) but never persisted to tasks.db.
    username: str = "system"
    kind: str = "generation"
    priority: int | None = None
    target_uuid: str | None = None
    target_article_key: str | None = None
    target_count: int | None = None
    generation_params: dict | str | None = None
    force: bool = False


@app.post("/api/v1/tasks", status_code=201)
async def create_task(
    request: Request,
    body: TaskCreateRequest,
    user_id: str = Depends(verify_token),
):
    if body.kind not in ("generation", "reindex"):
        raise HTTPException(400, f"invalid kind: {body.kind}")

    if body.kind == "reindex" and body.action not in REINDEX_ACTIONS:
        raise HTTPException(400, f"invalid reindex action: {body.action}")

    if body.kind == "generation" and (not body.domain or not body.lang):
        raise HTTPException(400, "domain and lang required for generation tasks")

    if body.kind == "reindex" and body.action == "reindex_domain" and not body.domain:
        raise HTTPException(400, "domain required for reindex_domain")

    from quizkb import users as kb_users

    resolved_priority = (
        None if body.kind == "reindex"
        else body.priority
    )

    # Resolve authoritative identity from verified token.
    user_id_int = int(user_id)
    resolved_username = kb_users.get_username(user_id_int)
    if resolved_username is None:
        raise HTTPException(
            status_code=500,
            detail=f"Authenticated user_id {user_id_int} not found in users table",
        )

    # 5-tier merge: defaults -> user global -> per_domain -> per_lang -> request_override.
    try:
        resolved_gp = kb_users.resolve_effective_generation_params(
            user_id_int,
            domain=body.domain or None,
            lang=body.lang or None,
            request_override=body.generation_params,
        )
    except Exception as e:
        log.exception("generation_params_resolution_failed", user_id=user_id_int)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid generation params: {e}",
        )

    tm: TaskManager = request.app.state.task_manager
    tp = extract_traceparent()
    task_id = tm.enqueue(
        action=body.action,
        domain=body.domain,
        lang=body.lang,
        username=resolved_username,
        kind=body.kind,
        priority=resolved_priority,
        force=body.force,
        target_uuid=body.target_uuid,
        target_article_key=body.target_article_key,
        target_count=body.target_count,
        trace_parent=tp,
        generation_params=resolved_gp
    )
    log.info(
        "task_enqueued",
        task_id=task_id,
        kind=body.kind,
        action=body.action,
        domain=body.domain,
        lang=body.lang,
        priority=resolved_priority,
        user_id=user_id_int,
        username=resolved_username,
        body_username_ignored=(
            body.username if body.username != resolved_username else None
        ),
    )
    return {"task_id": task_id, "status": "queued", "action": body.action, "domain": body.domain, "lang": body.lang}


@app.get("/api/v1/tasks", dependencies=[Depends(verify_token)])
async def list_tasks(
    request: Request,
    sort: str = "started_at",
    direction: str = "DESC",
    page: int = 1,
    limit: int = 50,
    status: str | None = None,
    domain: str | None = None,
    lang: str | None = None
):
    tm: TaskManager = request.app.state.task_manager
    tasks, total = tm.list_tasks(sort=sort, direction=direction, page=page, limit=limit, status=status, domain=domain, lang=lang)
    return {"total": total, "page": page, "limit": limit, "tasks": tasks}


@app.get("/api/v1/tasks/active", dependencies=[Depends(verify_token)])
async def active_tasks_count(request: Request):
    tm: TaskManager = request.app.state.task_manager
    return {"count": tm.get_active_count()}


@app.get("/api/v1/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def get_task_detail(request: Request, task_id: str):
    tm: TaskManager = request.app.state.task_manager
    task = tm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/v1/tasks/{task_id}/log", dependencies=[Depends(verify_token)])
async def get_task_log(request: Request, task_id: str, tail: int = 100):
    tm: TaskManager = request.app.state.task_manager
    task = tm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    log_path = TASKS_DIR / task_id / "output.log"
    log_text = ""
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if tail > 0:
            lines = log_text.splitlines()
            log_text = "\n".join(lines[-tail:])
    return {"task_id": task_id, "log": log_text}


@app.post("/api/v1/tasks/{task_id}/kill", dependencies=[Depends(verify_token)])
async def kill_task(request: Request, task_id: str):
    tm: TaskManager = request.app.state.task_manager
    success = tm.kill(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task not running or not found")
    return {"status": "ok"}


@app.post("/api/v1/tasks/{task_id}/force_interrupt", dependencies=[Depends(verify_token)])
async def force_interrupt_task(request: Request, task_id: str):
    tm: TaskManager = request.app.state.task_manager
    task = tm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    tm.update_task_status(task_id, "interrupted", error_message="Manually interrupted by user")
    return {"status": "ok"}


@app.post("/api/v1/tasks/{task_id}/restart", dependencies=[Depends(verify_token)])
async def restart_task(request: Request, task_id: str):
    tm: TaskManager = request.app.state.task_manager
    tp = extract_traceparent()
    try:
        new_id = await run_in_threadpool(tm.restart, task_id, trace_parent=tp)
        if not new_id:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"status": "ok", "new_id": new_id, "new_task_id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Worker API (Unauthenticated for trusted network) ---

@app.get("/api/v1/workers/poll")
async def worker_poll(request: Request):
    tm: TaskManager = request.app.state.task_manager
    task = tm.claim_next_task()
    if not task:
        return Response(status_code=204)
    return {"task": task}


@app.get("/api/v1/workers/tasks/running")
async def worker_running_tasks(request: Request):
    tm: TaskManager = request.app.state.task_manager
    with tm._get_conn() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status = 'running'").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/v1/workers/{task_id}/progress")
async def worker_progress(request: Request, task_id: str):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        status = body.get("status")
        pid = body.get("pid")
        error_message = body.get("error_message")
    else:
        form = await request.form()
        status = form.get("status")
        pid = int(form.get("pid")) if form.get("pid") else None
        error_message = form.get("error_message")

    if not status:
        raise HTTPException(status_code=400, detail="status is required")

    tm: TaskManager = request.app.state.task_manager
    tm.update_task_status(task_id, status, pid=pid, error_message=error_message)
    return {"status": "acknowledged"}


@app.post("/api/v1/workers/{task_id}/log")
async def worker_append_log(request: Request, task_id: str):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        lines = body.get("lines", "")
    else:
        form = await request.form()
        lines = form.get("lines", "")

    log_path = TASKS_DIR / task_id / "output.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(lines + "\n")
    return {"status": "appended"}


@app.get("/api/v1/workers/{task_id}/status")
async def worker_get_status(request: Request, task_id: str):
    tm: TaskManager = request.app.state.task_manager
    task = tm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"kill_requested": bool(task.get("kill_requested", 0))}

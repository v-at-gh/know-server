from __future__ import annotations

import argparse
import logging
import os
import subprocess
import time
import sys
import json
import requests
import signal
import psutil
from pathlib import Path
from threading import Thread
import threading
from typing import Any
import structlog.contextvars

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quizkb.observability import (
    configure_logging,
    configure_telemetry,
    parse_traceparent,
    extract_traceparent,
    force_flush_telemetry,
    get_meter,
)
from quizkb.settings import get_settings
from quizkb.lock import acquire as acquire_lock, LockAlreadyHeld
from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan, SpanKind, Link,
    Status, StatusCode
)


worker_meter = get_meter("worker")

worker_active = worker_meter.create_gauge(
    "worker_active_tasks",
    description="Whether this worker is currently processing a task (0/1) by kind",
)
tasks_started = worker_meter.create_counter(
    "worker_tasks_started",
    description="Tasks started by worker",
)
tasks_finished = worker_meter.create_counter(
    "worker_tasks_finished",
    description="Tasks finished by worker",
)

def _emit_active_tasks(current_kind: str | None, *, gauge=None) -> None:
    """Set worker_active_tasks gauge for both kinds. `gauge` kwarg is a
    test-only escape hatch -- defaults to module-level `worker_active`."""
    g = gauge if gauge is not None else worker_active
    g.set(1 if current_kind == "generation" else 0, {"kind": "generation"})
    g.set(1 if current_kind == "reindex" else 0, {"kind": "reindex"})



# Global event to signal shutdown across threads
shutdown_event = threading.Event()

def _handle_exit_signal(signum, _frame):
    log = logging.getLogger("worker")
    sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    log.warning("Received %s. Graceful shutdown initiated...", sig_name)
    shutdown_event.set()

def _terminate_process_tree(process: subprocess.Popen):
    """Safely terminates the subprocess and all its children."""
    log = logging.getLogger("worker")
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except Exception as e:
        log.warning("Failed to kill process tree: %s", e)

def _tail(path: Path, n: int = 30) -> str:
    """Returns the last N lines of a file without loading it entirely into memory."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
            lines = data.decode("utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
    except Exception:
        return ""

def _build_subprocess_cmd(task: dict, task_id: str) -> list[str]:
    kind = task.get("kind", "generation")
    action = task.get("action")
    domain = task.get("domain", "")
    lang = task.get("lang", "")
    target_uuid = task.get("target_uuid")
    target_article_key = task.get("target_article_key")
    target_count = task.get("target_count")
    generation_params = task.get("generation_params")

    if kind == "reindex":
        script = REPO_ROOT / "scripts" / "reindex_vec.py"
        cmd = [sys.executable, "-u", str(script), "--task-id", task_id]
        if action == "reindex_full":
            cmd.append("--full")
        elif action == "reindex_missing":
            cmd.append("--missing")
        elif action == "reindex_prune":
            cmd.append("--prune")
        elif action == "reindex_domain":
            cmd.extend(["--domain", domain])
            if lang:
                cmd.extend(["--lang", lang])
        else:
            raise ValueError(f"Unknown reindex action: {action}")
        return cmd
    else:
        script = REPO_ROOT / "scripts" / "generate_content.py"
        cmd = [
            sys.executable, "-u", str(script),
            "--action", action,
            "--domain", domain,
            "--lang", lang,
            "--task-id", task_id,
        ]
        if target_uuid:
            cmd.extend(["--target-uuid", target_uuid])
        if target_article_key:
            cmd.extend(["--target-article-key", target_article_key])
        if target_count is not None:
            cmd.extend(["--target-count", str(target_count)])
        if generation_params:
            if isinstance(generation_params, dict):
                generation_params = json.dumps(generation_params)
            cmd.extend(["--generation-json", generation_params])
        return cmd

def _enrich_task_span(span: trace.Span, task: dict, current_task_kind: str) -> None:
    task_id = task["id"]
    action = task["action"]
    domain = task["domain"]
    lang = task["lang"]
    target_uuid = task.get("target_uuid")
    target_article_key = task.get("target_article_key")
    target_count = task.get("target_count")

    span.set_attribute("task.id", task_id)
    span.set_attribute("task.kind", current_task_kind)
    span.set_attribute("task.action", action)
    span.set_attribute("task.domain", domain)
    span.set_attribute("task.lang", lang)
    if target_uuid:
        span.set_attribute("task.target_uuid", target_uuid)
    if target_article_key is not None:
        span.set_attribute("task.target_article_key", target_article_key)
    if target_count is not None:
        span.set_attribute("task.target_count", target_count)

def _build_subprocess_env(child_traceparent: str | None) -> tuple[dict[str, str], dict[str, Any]]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if child_traceparent:
        env["OTEL_TRACEPARENT"] = child_traceparent
    else:
        env.pop("OTEL_TRACEPARENT", None)

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return env, kwargs

_worker_session: requests.Session | None = None

def _get_session() -> requests.Session:
    """Lazy-init worker HTTP session. Thread-safe for connection-pool use
    (no per-request mutation of session.headers/cookies). Adapter configured
    for connection reuse with pool sized for 2 threads (main + log streamer)."""
    global _worker_session
    if _worker_session is None:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=2,
            pool_maxsize=4,
            max_retries=0,  # existing backoff loop handles retries
            pool_block=False,
        )
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _worker_session = s
    assert _worker_session is not None
    return _worker_session

def _report_progress(
    orchestrator_url: str,
    task_id: str,
    status: str,
    *,
    pid: int | None = None,
    error_message: str | None = None,
    timeout: int = 5,
) -> requests.Response:
    data: dict[str, Any] = {"status": status}
    if pid is not None:
        data["pid"] = pid
    if error_message is not None:
        data["error_message"] = error_message
    return _get_session().post(
        f"{orchestrator_url}/api/v1/workers/{task_id}/progress",
        data=data,
        timeout=timeout,
    )

def _spawn_task_process(
    task: dict,
    task_id: str,
    child_traceparent: str | None,
    orchestrator_url: str,
    task_log: logging.LoggerAdapter,
    span: trace.Span,
) -> subprocess.Popen | None:
    env, kwargs = _build_subprocess_env(child_traceparent)

    try:
        cmd = _build_subprocess_cmd(task, task_id)
    except ValueError as e:
        task_log.error("%s", e)
        try:
            _report_progress(orchestrator_url, task_id, "error", error_message=str(e), timeout=5)
        except Exception:
            pass
        return None

    try:
        task_log.debug("Spawning: %s", " ".join(cmd))
        process = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs
        )
    except Exception as e:
        task_log.exception("Failed to spawn subprocess")
        try:
            _report_progress(orchestrator_url, task_id, "error", error_message=str(e), timeout=5)
        except Exception:
            pass
        return None

    task_log.info("Spawned process with PID %d", process.pid)
    span.add_event("subprocess_spawned", {"pid": process.pid})

    try:
        _report_progress(orchestrator_url, task_id, "running", pid=process.pid, timeout=5)
    except Exception:
        pass

    return process

def _finalize_task(
    span: trace.Span,
    task_id: str,
    current_task_kind: str | None,
    final_status: str,
    err_msg: str | None,
    returncode: int,
    task_log: logging.LoggerAdapter,
    orchestrator_url: str,
) -> None:
    if final_status == "done":
        span.set_status(Status(StatusCode.OK))
    else:
        span.set_status(Status(StatusCode.ERROR, err_msg or final_status))
    span.set_attribute("task.final_status", final_status)

    tasks_finished.add(1, {
        "kind": current_task_kind or "unknown",
        "status": final_status,
    })

    task_log.info("task_finished", extra={"status": final_status, "exit_code": returncode, "error": err_msg})
    span.add_event("subprocess_finished", {
        "returncode": returncode,
        "final_status": final_status,
    })

    try:
        _report_progress(orchestrator_url, task_id, final_status, error_message=err_msg, timeout=3)
    except Exception as e:
        task_log.error("Failed to report final status: %s", e)

def _monitor_and_wait_process(
    process: subprocess.Popen,
    task_id: str,
    orchestrator_url: str,
    task_log: logging.LoggerAdapter,
) -> tuple[str, str | None, int]:
    killed_by_signal = False
    while process.poll() is None:
        if shutdown_event.is_set():
            task_log.warning("Shutdown requested during task %s. Killing process...", task_id)
            _terminate_process_tree(process)
            killed_by_signal = True
            break

        try:
            status_resp = _get_session().get(f"{orchestrator_url}/api/v1/workers/{task_id}/status", timeout=3)
            if status_resp.status_code == 200:
                if status_resp.json().get("kill_requested"):
                    task_log.info("Kill requested for task %s", task_id)
                    _terminate_process_tree(process)
                    killed_by_signal = True
                    break
        except Exception:
            pass

        for _ in range(10):
            if shutdown_event.is_set():
                break
            time.sleep(0.1)

    process.wait()
    returncode = process.returncode

    if killed_by_signal:
        if shutdown_event.is_set():
            final_status = "interrupted"
            err_msg = "Worker shut down gracefully"
        else:
            final_status = "killed"
            err_msg = "Manually killed by user"
    elif returncode == 0:
        final_status = "done"
        err_msg = None
    else:
        final_status = "error"
        err_msg = f"Process exited with code {returncode}"

    return final_status, err_msg, returncode

def _is_our_process(pid: int, task_id: str) -> bool:
    """Checks if a given PID is alive AND belongs to our specific task."""
    if not pid:
        return False
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        cmdline = " ".join(proc.cmdline())
        is_target = "generate_content.py" in cmdline or "reindex_vec.py" in cmdline
        return is_target and task_id in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

def _recover_running_tasks(orchestrator_url: str) -> bool:
    """Executes startup recovery for orphaned tasks left running across worker restarts."""
    log = logging.getLogger("worker")
    log.info("Checking for orphaned running tasks on startup...")
    try:
        resp = _get_session().get(f"{orchestrator_url}/api/v1/workers/tasks/running", timeout=5)
        if resp.status_code != 200:
            log.warning("Recovery endpoint returned %d", resp.status_code)
            return False

        tasks = resp.json()
        if not tasks:
            log.info("No orphaned running tasks found.")
            return True

        for t in tasks:
            tid = t["id"]
            pid = t.get("pid")
            if _is_our_process(pid, tid):
                log.info("Recovery: task %s (pid %d) is still running normally.", tid, pid)
                continue
            status_json_path = REPO_ROOT / "state" / "tasks" / tid / "status.json"
            if status_json_path.exists():
                try:
                    data = json.loads(status_json_path.read_text(encoding="utf-8"))
                    file_status = data.get("status")
                    if file_status == "done":
                        status = "done"
                        err = None
                    elif file_status == "error":
                        status = "error"
                        err = data.get("error") or "Subprocess reported error"
                    else:
                        status = "interrupted"
                        err = f"Worker restarted, unexpected marker status={file_status!r}"
                except Exception:
                    status = "interrupted"
                    err = "Worker restarted, status.json unreadable"
            else:
                log_path = REPO_ROOT / "state" / "tasks" / tid / "output.log"
                status = "interrupted"
                err = "Worker restarted, PID no longer belongs to this task"
                if log_path.exists():
                    tail_content = _tail(log_path, n=50)
                    if ("=== Task Completed Successfully ===" in tail_content
                            or "=== Reindex Completed Successfully ===" in tail_content):
                        status = "done"
                        err = None
            log.info("Recovery: marking orphaned task %s as %s", tid, status)
            try:
                _report_progress(orchestrator_url, tid, status, error_message=err or "", timeout=5)
            except Exception as e:
                log.error("Recovery: failed to update task %s: %s", tid, e)
        return True
    except Exception as e:
        log.warning("Waiting for orchestrator to come online for recovery: %s", e)
        return False

def process_task(task: dict, args: argparse.Namespace, log: logging.Logger, current_task_kind: str) -> None:
    task_id = task["id"]
    action = task["action"]
    domain = task["domain"]
    lang = task["lang"]
    trace_parent_str = task.get("trace_parent")

    parent_ctx = None
    if trace_parent_str:
        parent_span_ctx = parse_traceparent(trace_parent_str)
        if parent_span_ctx:
            parent_ctx = trace.set_span_in_context(NonRecordingSpan(parent_span_ctx))

    tracer = trace.get_tracer("worker")

    structlog.contextvars.bind_contextvars(task_id=task_id)
    try:
        with tracer.start_as_current_span(
            "task.process",
            context=parent_ctx,
            kind=SpanKind.CONSUMER,
            links=[Link(parent_span_ctx)] if parent_ctx else None,
        ) as span:
            _enrich_task_span(span, task, current_task_kind)

            tasks_started.add(1, {"kind": current_task_kind, "action": action})

            child_traceparent = extract_traceparent()

            task_log = logging.LoggerAdapter(log, {"task_id": task_id})
            task_log.info(
                "task_claimed",
                extra={"action": action, "domain": domain, "lang": lang, "kind": current_task_kind},
            )

            # Report running status
            try:
                _report_progress(args.orchestrator_url, task_id, "running", timeout=5)
            except Exception as e:
                task_log.error("Failed to report running status: %s", e)

            process = _spawn_task_process(task, task_id, child_traceparent, args.orchestrator_url, task_log, span)
            if not process:
                return

            def stream_output():
                buffer = []
                last_send = time.time()
                try:
                    for line in process.stdout:
                        cleaned = line.rstrip("\r\n")
                        if not cleaned: continue

                        buffer.append(cleaned)

                        if len(buffer) >= 50 or (time.time() - last_send) > 1.0:
                            try:
                                res = _get_session().post(
                                    f"{args.orchestrator_url}/api/v1/workers/{task_id}/log",
                                    data={"lines": "\n".join(buffer)},
                                    timeout=3
                                )
                                if res.status_code != 200:
                                    task_log.warning("Failed to stream logs: HTTP %d", res.status_code)
                                buffer.clear()
                                last_send = time.time()
                            except Exception as net_ex:
                                task_log.warning("Failed to stream logs (network): %s", net_ex)
                except Exception as stream_ex:
                    task_log.error("Log streaming thread crashed: %s", stream_ex)
                finally:
                    if buffer:
                        try:
                            _get_session().post(
                                f"{args.orchestrator_url}/api/v1/workers/{task_id}/log",
                                data={"lines": "\n".join(buffer)},
                                timeout=3
                            )
                        except Exception:
                            pass

            t = Thread(target=stream_output, daemon=True)
            t.start()

            final_status, err_msg, returncode = _monitor_and_wait_process(
                process, task_id, args.orchestrator_url, task_log
            )

            _finalize_task(span, task_id, current_task_kind, final_status, err_msg, returncode, task_log, args.orchestrator_url)
    finally:
        structlog.contextvars.clear_contextvars()

def _execute_task_with_resilience(task: dict, args, log) -> None:
    kind = task.get("kind", "generation")
    task_id = task.get("id")
    _emit_active_tasks(kind)
    try:
        process_task(task, args, log, kind)
    except Exception as e:
        log.exception("process_task_raised", extra={"task_id": task_id})
        if task_id:
            try:
                _report_progress(
                    args.orchestrator_url,
                    task_id,
                    "error",
                    error_message=f"worker_internal_error: {type(e).__name__}: {e}",
                    timeout=5,
                )
            except Exception:
                log.exception("failed_to_report_task_error")
        raise

def _poll_once(orchestrator_url: str) -> requests.Response:
    """Single poll cycle. Extracted for testability and clarity."""
    return _get_session().get(
        f"{orchestrator_url}/api/v1/workers/poll",
        timeout=5,
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orchestrator-url", default=get_settings().orchestrator_url, help="Orchestrator daemon URL")
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging verbosity")
    ap.add_argument("--allow-duplicate", action="store_true",
                    help="Allow starting a second worker (for manual testing)")
    args = ap.parse_args()

    numeric_level = getattr(logging, args.log_level.upper(), logging.INFO)
    configure_telemetry("worker")
    configure_logging("worker", level=numeric_level)
    log = logging.getLogger("worker")

    worker_lock = None
    if not args.allow_duplicate:
        try:
            worker_lock = acquire_lock(
                "know-worker",
                command="worker.py " + " ".join(sys.argv[1:]),
            )
        except LockAlreadyHeld as e:
            log.error("Refusing to start: %s", e)
            log.error("Another worker is running. Use --allow-duplicate to override.")
            sys.exit(1)
    else:
        log.warning("Worker started with --allow-duplicate; two workers may claim tasks concurrently")

    signal.signal(signal.SIGINT, _handle_exit_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_exit_signal)

    log.info("Worker daemon started, polling %s", args.orchestrator_url)

    recovery_done = False
    backoff = 2
    start_time = time.time()
    last_heartbeat = time.time()
    current_task_kind: str | None = None
    current_task_id: str | None = None

    while not shutdown_event.is_set():
        now = time.time()
        _emit_active_tasks(current_task_kind)
        if now - last_heartbeat >= 30:
            last_heartbeat = now
            log.info("worker_heartbeat", extra={
                "active_tasks": 1 if current_task_kind else 0,
                "active_kind": current_task_kind or "none",
                "uptime_seconds": int(now - start_time),
            })

        try:
            # --- 1. Startup Recovery ---
            if not recovery_done:
                if _recover_running_tasks(args.orchestrator_url):
                    recovery_done = True
                    log.info("Startup recovery complete.")
                else:
                    for _ in range(30):
                        if shutdown_event.is_set():
                            break
                        time.sleep(0.1)
                    continue

            # --- 2. Poll for Tasks ---
            try:
                resp = _poll_once(args.orchestrator_url)
                backoff = 2
            except requests.RequestException as e:
                log.warning("Failed to reach orchestrator at %s: %s. Retrying in %ds...", args.orchestrator_url, e, backoff)
                for _ in range(int(backoff * 10)):
                    if shutdown_event.is_set():
                        break
                    time.sleep(0.1)
                backoff = min(backoff * 2, 30)
                continue

            if resp.status_code == 204 or not resp.text.strip():
                log.debug("Polling: no tasks in queue. Sleeping...")
                for _ in range(20):
                    if shutdown_event.is_set():
                        break
                    time.sleep(0.1)
                continue

            if resp.status_code != 200:
                log.warning("Unexpected poll response %d: %s", resp.status_code, resp.text)
                time.sleep(2)
                continue

            data = resp.json()
            task = data.get("task")
            if not task:
                log.debug("Polling: response contained no task object. Sleeping...")
                for _ in range(20):
                    if shutdown_event.is_set():
                        break
                    time.sleep(0.1)
                continue

            current_task_kind = task.get("kind", "generation")
            current_task_id = task.get("id")

            try:
                _execute_task_with_resilience(task, args, log)
            finally:
                current_task_kind = None
                current_task_id = None
                _emit_active_tasks(None)

            if shutdown_event.is_set():
                break

        except Exception as e:
            logging.getLogger("worker").exception("Exception in worker main loop")
            current_task_kind = None
            current_task_id = None
            _emit_active_tasks(None)
            time.sleep(2)

    log.info("Worker daemon stopped gracefully.")
    try:
        force_flush_telemetry()
    except Exception:
        pass
    if worker_lock:
        worker_lock.release()
    if _worker_session is not None:
        try:
            _worker_session.close()
        except Exception:
            pass

if __name__ == "__main__":
    sys.exit(main())

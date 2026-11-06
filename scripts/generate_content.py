import argparse
import logging
import os
import sys
import json
import traceback
import threading
from pathlib import Path
import structlog.contextvars

stop_event = threading.Event()

def heartbeat_loop(task_id: str, action: str, log: logging.Logger):
    while not stop_event.is_set():
        log.info("pipeline_heartbeat", extra={"task_id": task_id, "action": action})
        stop_event.wait(30)

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quizkb import generation
from quizkb.config import (
    OUTPUT_ROOT, LOGS_ROOT, DOMAINS, CAMPAIGNS
)
from quizkb.jobs import sync_config_to_output, translate_domains
from quizkb.recovery import recover_thin_articles
from quizkb.client import OllamaClient
from quizkb.ask import make_ask
from quizkb.logging import Logger
from quizkb.observability import (
    configure_logging, configure_telemetry, parse_traceparent,
    force_flush_telemetry
)
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan


def _configure_logging() -> None:
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding.lower() != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
    configure_logging("subprocess")


def main() -> int:
    import signal as _signal
    from quizkb.io_utils import install_safe_std_streams
    install_safe_std_streams()

    if hasattr(_signal, "SIGTERM"):
        def _sigterm_handler(signum, frame):
            raise KeyboardInterrupt()
        _signal.signal(_signal.SIGTERM, _sigterm_handler)

    configure_telemetry("subprocess")
    _configure_logging()
    log = logging.getLogger("generate_content")

    ap = argparse.ArgumentParser()
    ap.add_argument("--action",
                    choices=[
                        "bootstrap", "generate", "translate",
                        "ensure_meta", "recover", "translate_single_q",
                        "generate_article_questions"],
                    required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--lang", required=True)
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--target-uuid", help="UUID of the specific question to translate")
    ap.add_argument("--target-article-key", help="Article key (domain/slug) for article-specific generation")
    ap.add_argument("--target-count", type=int, help="Number of questions to generate for the article")
    ap.add_argument("--generation-json", default=None, help="JSON string of user generation parameter overrides")
    args = ap.parse_args()

    hb_thread = None
    if args.task_id:
        structlog.contextvars.bind_contextvars(task_id=args.task_id)

    parent_ctx = None
    tp = os.environ.get("OTEL_TRACEPARENT")
    if tp:
        parent_span_ctx = parse_traceparent(tp)
        if parent_span_ctx:
            parent_ctx = trace.set_span_in_context(NonRecordingSpan(parent_span_ctx))

    tracer = trace.get_tracer("subprocess")

    with tracer.start_as_current_span(
        "subprocess.run",
        context=parent_ctx,
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("subprocess.action", args.action)
        span.set_attribute("subprocess.domain", args.domain)
        span.set_attribute("subprocess.lang", args.lang)
        span.set_attribute("subprocess.task_id", args.task_id or "")

        if args.task_id:
            hb_thread = threading.Thread(target=heartbeat_loop, args=(args.task_id, args.action, log), daemon=True)
            hb_thread.start()

        if args.task_id:
            task_dir = REPO_ROOT / "state" / "tasks" / args.task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            progress_file = task_dir / "progress.json"

            def on_progress(phase: str, done: int, total: int, current: str = ""):
                data = {
                    "phase": phase,
                    "done": done,
                    "total": total,
                    "current": current
                }
                progress_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        else:
            # Standalone mode: just print progress to console
            def on_progress(phase: str, done: int, total: int, current: str = ""):
                print(f"[progress] {phase}: {done}/{total} ({current})", flush=True)
                log.info("progress", extra={"phase": phase, "done": done, "total": total, "current": current})

        log.info("Starting %s for %s -> %s", args.action, args.domain, args.lang)

        exit_code = 1
        run_logger = None
        try:
            client = OllamaClient()
            run_logger = Logger(root=LOGS_ROOT, pipeline_rev_cwd=REPO_ROOT)
            ask = make_ask(client, run_logger)

            domain_cfg = next((d for d in DOMAINS if d["slug"] == args.domain), None)
            if not domain_cfg:
                raise ValueError(f"Domain {args.domain} not found in config")

            if args.generation_json:
                try:
                    overrides = json.loads(args.generation_json)
                    domain_cfg = {**domain_cfg}
                    if "articles_per_domain" in overrides:
                        domain_cfg["articles_per_domain"] = overrides["articles_per_domain"]
                    if "questions_types" in overrides:
                        domain_cfg["question_types"] = overrides["questions_types"]
                except Exception as e:
                    log.error("Failed to parse --generation-json: %s", e)

            print("[*] Syncing config to output...", flush=True)
            log.info("sync_config")
            sync_config_to_output(OUTPUT_ROOT, DOMAINS, CAMPAIGNS)

            if args.action == "bootstrap":
                from quizkb.jobs import bootstrap_domain
                bootstrap_domain(
                    ask,
                    output_root=OUTPUT_ROOT,
                    domain_cfg=domain_cfg,
                    on_progress=on_progress,
                    on_reject=run_logger.parse_reject,
                    verbose=True
                )
            elif args.action == "generate":
                generation.generate_questions_for_domain(
                    ask,
                    output_root=OUTPUT_ROOT,
                    domain_slug=domain_cfg["slug"],
                    domain_name=domain_cfg["name"],
                    domain_cfg=domain_cfg,
                    on_reject=run_logger.parse_reject,
                    verbose=True,
                    on_progress=on_progress
                )
            elif args.action == "translate":
                translate_domains(
                    ask,
                    output_root=OUTPUT_ROOT,
                    domains=[domain_cfg],
                    langs=[args.lang],
                    on_reject=run_logger.parse_reject,
                    verbose=True,
                    on_progress=on_progress
                )
            elif args.action == "ensure_meta":
                from quizkb.domain_meta import ensure_meta
                ensure_meta(
                    ask,
                    output_root=OUTPUT_ROOT,
                    domains=[domain_cfg],
                    langs=[args.lang],
                    verbose=True
                )
            elif args.action == "recover":
                qt = domain_cfg.get("question_types") or {}
                override = sum(qt.values()) if qt else None
                recover_thin_articles(
                    ask,
                    output_root=OUTPUT_ROOT,
                    domains=[domain_cfg],
                    questions_per_article_override=override,
                    on_reject=run_logger.parse_reject,
                    verbose=True,
                    on_progress=on_progress
                )
            elif args.action == "translate_single_q":
                from quizkb.translation import translate_single_question
                translate_single_question(
                    ask,
                    output_root=OUTPUT_ROOT,
                    lang=args.lang,
                    domain_slug=domain_cfg["slug"],
                    uuid=args.target_uuid,
                    on_reject=run_logger.parse_reject,
                    verbose=True
                )
            elif args.action == "generate_article_questions":
                from quizkb.jobs import generate_questions_for_article
                generate_questions_for_article(
                    ask,
                    output_root=OUTPUT_ROOT,
                    domain_slug=domain_cfg["slug"],
                    lang=args.lang,
                    article_key=args.target_article_key,
                    count=args.target_count or 1,
                    on_reject=run_logger.parse_reject,
                    verbose=True
                )
            print("=== Task Completed Successfully ===", flush=True)
            log.info("Task completed successfully")
            exit_code = 0
        except Exception:
            log.exception("Task failed")
            exit_code = 1
        finally:
            if exit_code == 0:
                try:
                    print("[*] Syncing content.db...", flush=True)
                    from quizkb import db as kb_db
                    from quizkb.config import PACKS_JSON
                    db_path = REPO_ROOT / "state" / "content.db"

                    if args.action in ("bootstrap", "generate", "recover",
                                        "generate_article_questions", "ensure_meta"):
                        kb_db.sync_all(db_path, OUTPUT_ROOT, PACKS_JSON, reembed=False)
                    elif args.action in ("translate", "translate_single_q"):
                        if getattr(args, "lang", None) and getattr(args, "domain", None):
                            kb_db.sync_domain(db_path, OUTPUT_ROOT, args.lang, args.domain, reembed=False)
                        else:
                            kb_db.sync_all(db_path, OUTPUT_ROOT, PACKS_JSON, reembed=False)
                    log.info("post-task DB sync complete")
                except Exception as e:
                    log.error("post-task sync failed: %s", e)

            if args.task_id:
                stop_event.set()
                if hb_thread:
                    hb_thread.join(timeout=1)
            if run_logger is not None:
                try:
                    run_logger.close()
                except Exception:
                    pass

            if args.task_id:
                try:
                    from quizkb.io_utils import write_task_status
                    task_dir = REPO_ROOT / "state" / "tasks" / args.task_id
                    write_task_status(task_dir, "done" if exit_code == 0 else "error", exit_code)
                except Exception:
                    pass
                
            # Manually end the span before hard-exiting
            try:
                span.end()
            except Exception:
                pass
                
            try:
                force_flush_telemetry()
            except Exception:
                pass
            os._exit(exit_code)

    return 0

if __name__ == "__main__":
    sys.exit(main())
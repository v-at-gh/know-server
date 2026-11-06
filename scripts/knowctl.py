#!/usr/bin/env python3
"""Operational CLI (MVP) for Know."""
import argparse
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sqlite_vec
import sys
import time
import tarfile
import psutil
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CONFIG_PATH = REPO_ROOT / "state" / "cli_config.json"

from quizkb.observability import configure_telemetry, safe_exit

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                changed = False
                for k, v in [
                    ("admin_url", "http://127.0.0.1:8000"),
                    ("orch_url", "http://127.0.0.1:8002"),
                    ("public_url", "http://127.0.0.1:8001"),
                    ("token", ""),
                    ("backups_dir", "state/backups")
                ]:
                    if k not in cfg:
                        cfg[k] = v
                        changed = True
                if changed:
                    save_config(cfg)
                return cfg
        except Exception:
            pass
    defaults = {
        "admin_url": "http://127.0.0.1:8000",
        "orch_url": "http://127.0.0.1:8002",
        "public_url": "http://127.0.0.1:8001",
        "token": "",
        "backups_dir": "state/backups"
    }
    save_config(defaults)
    return defaults

def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def get_headers(cfg: dict) -> dict:
    headers = {}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    return headers

def vlog(args, msg):
    if getattr(args, "verbose", 0) >= 1:
        print(f"[debug] {msg}", file=sys.stderr)

def _is_orphaned(task: dict) -> bool:
    if task.get("status") != "running":
        return False
    pid = task.get("pid")
    if not pid:
        return True
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    try:
        if proc.status() == psutil.STATUS_ZOMBIE:
            return True
        cmd = " ".join(proc.cmdline())
        return "generate_content.py" not in cmd or task["id"] not in cmd
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return True

def confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except Exception:
        return False

def _backup_db(src: Path, dst: Path) -> None:
    """Create backup of src at dst using Python's backup API.
    Cross-platform, no external sqlite3 binary, WAL-safe."""
    with sqlite3.connect(src) as src_conn:
        with sqlite3.connect(dst) as dst_conn:
            src_conn.backup(dst_conn)

# --- 4.1 status ---
def cmd_status(args):
    cfg = load_config()
    timeout = 2.0
    services = {}
    active_tasks = 0
    
    # 1. Admin
    t0 = time.time()
    try:
        r = requests.get(f"{cfg['admin_url']}/login", timeout=timeout)
        dt = (time.time() - t0) * 1000
        services["admin"] = "up" if r.status_code == 200 else "down"
        vlog(args, f"GET {cfg['admin_url']}/login -> status={r.status_code} in {dt:.1f}ms")
    except Exception as e:
        dt = (time.time() - t0) * 1000
        services["admin"] = "down"
        vlog(args, f"GET {cfg['admin_url']}/login -> error {e} in {dt:.1f}ms")

    # 2. Orchestrator
    t0 = time.time()
    try:
        r = requests.get(f"{cfg['orch_url']}/health", timeout=timeout)
        dt = (time.time() - t0) * 1000
        if r.status_code == 200:
            services["orchestrator"] = "up"
            try:
                data = r.json()
                active_tasks = data.get("active_tasks", 0)
            except Exception:
                pass
        else:
            services["orchestrator"] = "down"
        vlog(args, f"GET {cfg['orch_url']}/health -> status={r.status_code} in {dt:.1f}ms")
    except Exception as e:
        dt = (time.time() - t0) * 1000
        services["orchestrator"] = "down"
        vlog(args, f"GET {cfg['orch_url']}/health -> error {e} in {dt:.1f}ms")

    # 3. Public API
    t0 = time.time()
    try:
        r = requests.get(f"{cfg['public_url']}/health", timeout=timeout)
        dt = (time.time() - t0) * 1000
        services["public_api"] = "up" if r.status_code == 200 else "down"
        vlog(args, f"GET {cfg['public_url']}/health -> status={r.status_code} in {dt:.1f}ms")
    except Exception as e:
        dt = (time.time() - t0) * 1000
        services["public_api"] = "down"
        vlog(args, f"GET {cfg['public_url']}/health -> error {e} in {dt:.1f}ms")

    # 4. Ollama
    t0 = time.time()
    ollama_models = []
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=timeout)
        dt = (time.time() - t0) * 1000
        if r.status_code == 200:
            services["ollama"] = "up"
            try:
                models_data = r.json().get("models", [])
                for m in models_data:
                    mname = m.get("name") or m.get("model")
                    if mname:
                        ollama_models.append(mname)
            except Exception:
                pass
        else:
            services["ollama"] = "down"
        vlog(args, f"GET http://localhost:11434/api/tags -> status={r.status_code} in {dt:.1f}ms")
    except Exception as e:
        dt = (time.time() - t0) * 1000
        services["ollama"] = "down"
        vlog(args, f"GET http://localhost:11434/api/tags -> error {e} in {dt:.1f}ms")

    # Tasks from tasks.db
    tasks_db = REPO_ROOT / "state" / "tasks.db"
    task_counts = {"queued": 0, "running": 0, "done": 0, "error": 0, "killed": 0, "interrupted": 0, "orphaned": 0}
    if tasks_db.exists():
        t0 = time.time()
        try:
            with sqlite3.connect(tasks_db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT id, status, pid FROM tasks").fetchall()
                for row in rows:
                    st = row["status"]
                    if st in task_counts:
                        task_counts[st] += 1
                    if st == "running" and _is_orphaned(dict(row)):
                        task_counts["orphaned"] += 1
            dt = (time.time() - t0) * 1000
            vlog(args, f"read tasks.db: {len(rows)} rows, {task_counts['orphaned']} orphaned in {dt:.1f}ms")
        except Exception as e:
            dt = (time.time() - t0) * 1000
            vlog(args, f"read tasks.db error: {e} in {dt:.1f}ms")

    # Databases quick_check
    databases = {}
    db_files = ["content.db", "users.db", "tasks.db", "app.db", "server.db"]
    all_dbs_ok = True
    for db_name in db_files:
        db_path = REPO_ROOT / "state" / db_name
        if not db_path.exists():
            databases[db_name] = {"ok": False, "size_bytes": 0, "error": "not found"}
            all_dbs_ok = False
            vlog(args, f"DB {db_name}: not found")
            continue
        size = db_path.stat().st_size
        ok = False
        t0 = time.time()
        try:
            with sqlite3.connect(db_path) as conn:
                res = conn.execute("PRAGMA quick_check;").fetchone()
                if res and res[0] == "ok":
                    ok = True
            dt = (time.time() - t0) * 1000
            vlog(args, f"PRAGMA quick_check {db_name}: ok={ok} in {dt:.1f}ms")
        except Exception as e:
            dt = (time.time() - t0) * 1000
            ok = False
            vlog(args, f"PRAGMA quick_check {db_name}: error {e} in {dt:.1f}ms")
        if not ok:
            all_dbs_ok = False
        databases[db_name] = {"ok": ok, "size_bytes": size}

    content_db = REPO_ROOT / "state" / "content.db"

    # Drift check between output/ knowledge files and content.db
    if content_db.exists():
        try:
            from quizkb.config import OUTPUT_ROOT
            md_count = sum(1 for p in OUTPUT_ROOT.rglob("knowledge/*/*/*.md") if p.is_file())
            with sqlite3.connect(content_db) as conn:
                tbls = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "article_translations" in tbls:
                    db_articles = conn.execute("SELECT COUNT(*) FROM article_translations").fetchone()[0]
                    if abs(md_count - db_articles) > 0:
                        databases["content.db"]["drift_warning"] = {
                            "output_md_files": md_count,
                            "content_db_translations": db_articles,
                            "hint": "run POST /sync to reconcile"
                        }
        except Exception as e:
            vlog(args, f"drift check error: {e}")

    # Vector
    vec_info = {"initialized": False}
    content_db = REPO_ROOT / "state" / "content.db"
    if content_db.exists():
        try:
            with sqlite3.connect(content_db) as conn:
                try:
                    conn.enable_load_extension(True)
                    sqlite_vec.load(conn)
                    conn.enable_load_extension(False)
                except Exception as e:
                    vlog(args, f"vec extension load failed: {e}")

                tbls = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "schema_meta" in tbls:
                    row_model = conn.execute(
                        "SELECT value FROM schema_meta WHERE key = 'embed_model'"
                    ).fetchone()
                    row_dim = conn.execute(
                        "SELECT value FROM schema_meta WHERE key = 'embed_dimensions'"
                    ).fetchone()
                    if row_model and row_dim:
                        vec_info["initialized"] = True
                        vec_info["embed_model"] = row_model[0]
                        try:
                            vec_info["embed_dimensions"] = int(row_dim[0])
                        except (ValueError, TypeError):
                            vec_info["embed_dimensions"] = row_dim[0]

                    for t_name in ["article_embeddings", "question_embeddings"]:
                        if t_name in tbls:
                            try:
                                cnt = conn.execute(f"SELECT COUNT(*) FROM {t_name}").fetchone()[0]
                                vec_info[f"{t_name}_rowcount"] = cnt
                            except Exception as e:
                                vlog(args, f"count {t_name} error: {e}")
                                vec_info[f"{t_name}_rowcount"] = "error"
                        else:
                            vec_info[f"{t_name}_rowcount"] = "table missing"
                    
                    try:
                        art_tot = conn.execute("SELECT COUNT(*) FROM article_translations").fetchone()[0]
                        art_vec = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone() if "article_embeddings" in tbls else [0]
                        art_vec_cnt = art_vec[0] if art_vec else 0
                        vec_info["articles_with_vec"] = art_vec_cnt
                        vec_info["articles_total"] = art_tot
                    except Exception as e:
                        vlog(args, f"articles_with_vec count error: {e}")
                    try:
                        q_tot = conn.execute("SELECT COUNT(*) FROM question_translations").fetchone()[0]
                        q_vec = conn.execute("SELECT COUNT(*) FROM question_embeddings").fetchone() if "question_embeddings" in tbls else [0]
                        q_vec_cnt = q_vec[0] if q_vec else 0
                        vec_info["questions_with_vec"] = q_vec_cnt
                        vec_info["questions_total"] = q_tot
                    except Exception as e:
                        vlog(args, f"questions_with_vec count error: {e}")
        except Exception as e:
            vlog(args, f"content.db vec status error: {e}")

    # Recent backups
    backups_dir = REPO_ROOT / cfg.get("backups_dir", "state/backups")
    backups_list = []
    if backups_dir.exists():
        files = sorted(backups_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        for f in files:
            st = f.stat()
            backups_list.append({
                "path": str(f.relative_to(REPO_ROOT)),
                "size_bytes": st.st_size,
                "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat()
            })

    services_up = all(s == "up" for s in services.values() if s in ("admin", "orchestrator", "public_api"))
    overall = "ok" if (services_up and all_dbs_ok) else "degraded"

    result = {
        "services": services,
        "orchestrator_active_tasks": active_tasks,
        "ollama_models": ollama_models,
        "tasks": task_counts,
        "databases": databases,
        "vec": vec_info,
        "backups": backups_list,
        "overall": overall
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=== knowctl status ===")
        print(f"Overall: {overall.upper()}")
        print("\nServices:")
        for s, st in services.items():
            print(f"  - {s}: {st}")
        print(f"Orchestrator active tasks: {active_tasks}")
        print(f"Ollama models: {ollama_models if ollama_models else 'none/offline'}")
        
        print("\nTasks:")
        for k, v in task_counts.items():
            print(f"  - {k}: {v}")

        print("\nDatabases:")
        for db, info in databases.items():
            status_str = "OK" if info.get("ok") else "FAIL/MISSING"
            print(f"  - {db}: {status_str} ({info.get('size_bytes', 0)} bytes)")
            if info.get("drift_warning"):
                dw = info["drift_warning"]
                print(f"    WARNING: drifted ({dw['output_md_files']} output files vs {dw['content_db_translations']} DB rows). {dw['hint']}")

        print("\nVector (Semantic Search):")
        if vec_info.get("initialized"):
            print(f"  - model: {vec_info.get('embed_model')}")
            print(f"  - dimensions: {vec_info.get('embed_dimensions')}")
            print(f"  - articles with vec: {vec_info.get('articles_with_vec', '?')}/{vec_info.get('articles_total', '?')}")
            print(f"  - questions with vec: {vec_info.get('questions_with_vec', '?')}/{vec_info.get('questions_total', '?')}")
        else:
            print("  - vec: not initialized")

        print("\nRecent Backups:")
        if backups_list:
            for b in backups_list:
                print(f"  - {b['path']} ({b['size_bytes']} bytes, {b['mtime']})")
        else:
            print("  - none")

    safe_exit(0 if overall == "ok" else 1)

# --- 4.2 tasks list ---
def cmd_tasks_list(args):
    tasks_db = REPO_ROOT / "state" / "tasks.db"
    if not tasks_db.exists():
        if args.json:
            print(json.dumps([]))
        else:
            print("No tasks.db found.")
        return

    try:
        t0 = time.time()
        with sqlite3.connect(tasks_db) as conn:
            conn.row_factory = sqlite3.Row
            params = []
            if args.orphaned:
                query = "SELECT id, status, action, domain, lang, username, started_at, pid FROM tasks WHERE status = 'running' ORDER BY started_at DESC"
            elif args.status:
                query = "SELECT id, status, action, domain, lang, username, started_at, pid FROM tasks WHERE status = ? ORDER BY started_at DESC LIMIT ?"
                params.extend([args.status, args.limit])
            else:
                query = "SELECT id, status, action, domain, lang, username, started_at, pid FROM tasks ORDER BY started_at DESC LIMIT ?"
                params.append(args.limit)
            vlog(args, f"query: {query} with params {params}")
            rows = conn.execute(query, params).fetchall()

            results = []
            for r in rows:
                row_dict = dict(r)
                orph = _is_orphaned(row_dict)
                if args.orphaned and not orph:
                    continue
                row_dict["orphaned"] = orph
                results.append(row_dict)

            if args.orphaned:
                results = results[:args.limit]

            dt = (time.time() - t0) * 1000
            vlog(args, f"orphan check: {len(rows)} tasks, {len(results)} matched in {dt:.1f}ms")

            if args.json:
                print(json.dumps(results, indent=2))
            else:
                print(f"{'ID':<30} | {'STATUS':<10} | {'ACTION':<10} | {'DOMAIN':<10} | {'LANG':<5} | {'USER':<8} | {'STARTED_AT':<20} | {'ORPHAN'}")
                print("-" * 120)
                for t in results:
                    orph_mark = "*" if t["orphaned"] else ""
                    print(f"{t.get('id',''):<30} | {t.get('status',''):<10} | {t.get('action',''):<10} | {t.get('domain',''):<10} | {t.get('lang',''):<5} | {t.get('username',''):<8} | {t.get('started_at',''):<20} | {orph_mark}")
    except Exception as e:
        print(f"Error reading tasks.db: {e}", file=sys.stderr)
        safe_exit(1)

# --- 4.3 tasks interrupt ---
def cmd_tasks_interrupt(args):
    cfg = load_config()
    task_id = args.task_id

    tasks_db = REPO_ROOT / "state" / "tasks.db"
    current_status = "unknown"
    if tasks_db.exists():
        try:
            with sqlite3.connect(tasks_db) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT status, action, domain, lang, started_at, pid FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if row:
                    current_status = row["status"]
                    vlog(args, f"pre-state: {task_id} status={current_status} action={row['action']} pid={row['pid']}")
        except Exception:
            pass

    if args.dry_run:
        print(f"Dry run: interrupt task {task_id}")
        print(f"Current status: {current_status}")
        print(f"Target state: interrupted")
        print(f"Method: try admin API ({cfg['admin_url']}/api/tasks/{task_id}/force_interrupt), fallback to direct SQLite update")
        safe_exit(0)
    
    if not args.yes:
        if not confirm(f"Are you sure you want to interrupt task {task_id}?"):
            print("Aborted.")
            return

    via = "api"
    success = False
    try:
        vlog(args, f"trying admin API at {cfg['admin_url']}/api/tasks/{task_id}/force_interrupt")
        r = requests.post(f"{cfg['admin_url']}/api/tasks/{task_id}/force_interrupt", headers=get_headers(cfg), timeout=2.0)
        if r.status_code == 200:
            success = True
        else:
            raise Exception(f"HTTP {r.status_code}: {r.text}")
    except Exception as e:
        vlog(args, f"admin API failed: {e}")
        # Fallback to sqlite direct update
        via = "sqlite"
        if tasks_db.exists():
            try:
                with sqlite3.connect(tasks_db) as conn:
                    cur = conn.execute(
                        "UPDATE tasks SET status='interrupted', finished_at=?, error_message='Manually interrupted' WHERE id=? AND status IN ('running','queued')",
                        (datetime.datetime.now().isoformat(), task_id)
                    )
                    vlog(args, f"fallback to sqlite: rowcount={cur.rowcount}")
                    if cur.rowcount > 0:
                        success = True
            except Exception as ex:
                print(f"SQLite fallback failed: {ex}", file=sys.stderr)

    new_status = "interrupted" if success else current_status
    vlog(args, f"post-state: {task_id} status={new_status} via={via}")

    res = {"task_id": task_id, "interrupted": success, "via": via}
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if success:
            print(f"Task {task_id} successfully interrupted via {via}.")
        else:
            print(f"Failed to interrupt task {task_id}.", file=sys.stderr)
            safe_exit(1)

# --- 4.4 tasks cleanup-orphaned ---
def cmd_tasks_cleanup_orphaned(args):
    tasks_db = REPO_ROOT / "state" / "tasks.db"
    if not tasks_db.exists():
        print("tasks.db not found.")
        if args.json:
            print(json.dumps({"orphaned": 0, "interrupted": 0, "skipped": 0}))
        return

    orphaned_tasks = []
    try:
        with sqlite3.connect(tasks_db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM tasks WHERE status = 'running'").fetchall()
            vlog(args, f"running tasks: {len(rows)}")
            for r in rows:
                t = dict(r)
                pid = t.get("pid")
                alive = False
                match = False
                try:
                    if pid:
                        proc = psutil.Process(pid)
                        if proc.status() != psutil.STATUS_ZOMBIE:
                            alive = True
                            cmd = " ".join(proc.cmdline())
                            match = "generate_content.py" in cmd and t["id"] in cmd
                except Exception:
                    pass
                orph = _is_orphaned(t)
                vlog(args, f"{t['id']}: pid={pid} alive={alive} cmd_match={match} orphaned={orph}")
                if orph:
                    orphaned_tasks.append(t)
    except Exception as e:
        print(f"Error reading tasks.db: {e}", file=sys.stderr)
        safe_exit(1)

    summary = {"orphaned": len(orphaned_tasks), "interrupted": 0, "skipped": 0}

    if not orphaned_tasks:
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print("No orphaned running tasks found.")
        return

    if args.dry_run:
        if args.json:
            print(json.dumps({"dry_run": True, "orphaned_tasks": orphaned_tasks}, indent=2))
        else:
            print(f"Found {len(orphaned_tasks)} orphaned running task(s) (Dry Run):")
            for t in orphaned_tasks:
                print(f"  - ID: {t['id']}, PID: {t.get('pid')}, Started: {t.get('started_at')}, Action: {t.get('action')}, Domain: {t.get('domain')}")
        return

    if not args.yes:
        print(f"Found {len(orphaned_tasks)} orphaned running task(s):")
        for t in orphaned_tasks:
            print(f"  - ID: {t['id']}, PID: {t.get('pid')}, Started: {t.get('started_at')}")
        if not confirm("Do you want to mark these orphaned tasks as interrupted?"):
            print("Aborted.")
            return

    interrupted = 0
    try:
        vlog(args, f"updating {len(orphaned_tasks)} rows")
        with sqlite3.connect(tasks_db) as conn:
            now_str = datetime.datetime.now().isoformat()
            for t in orphaned_tasks:
                conn.execute(
                    "UPDATE tasks SET status='interrupted', finished_at=?, error_message='Orphaned process terminated' WHERE id=?",
                    (now_str, t["id"])
                )
                interrupted += 1
    except Exception as e:
        print(f"Error updating tasks.db: {e}", file=sys.stderr)
        safe_exit(1)

    summary["interrupted"] = interrupted
    summary["skipped"] = len(orphaned_tasks) - interrupted

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Successfully interrupted {interrupted} orphaned task(s).")

# --- 4.5 logs ---
def read_jsonl_file(path: Path, lines_count: int, min_level: str, args=None):
    level_hierarchy = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
    min_lvl_val = level_hierarchy.get(min_level.lower(), 10)
    
    file_paths = [path]
    rotated_found = []
    for i in range(1, 6):
        p_rot = path.with_name(f"{path.name}.{i}")
        if p_rot.exists():
            file_paths.append(p_rot)
            rotated_found.append(str(p_rot.relative_to(REPO_ROOT) if p_rot.is_relative_to(REPO_ROOT) else p_rot))
            
    if args:
        vlog(args, f"reading {path}")
        if rotated_found:
            vlog(args, f"rotated found: {rotated_found}")

    all_recs = []
    for fp in reversed(file_paths):
        if not fp.exists():
            continue
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        lvl = str(rec.get("level", "info")).lower()
                        if level_hierarchy.get(lvl, 20) < min_lvl_val:
                            continue
                        all_recs.append(rec)
                    except Exception:
                        all_recs.append({"message": line, "level": "info", "ts": ""})
        except Exception:
            pass
    if args:
        vlog(args, f"{len(all_recs)} parsed across files")
    return all_recs[-lines_count:]

def find_records_by_task_id(path: Path, task_id: str, lines_count: int, min_level: str, args=None):
    level_hierarchy = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
    min_lvl_val = level_hierarchy.get(min_level.lower(), 10)
    
    file_paths = [path]
    rotated_found = []
    for i in range(1, 6):
        p_rot = path.with_name(f"{path.name}.{i}")
        if p_rot.exists():
            file_paths.append(p_rot)
            rotated_found.append(str(p_rot.relative_to(REPO_ROOT) if p_rot.is_relative_to(REPO_ROOT) else p_rot))
            
    if args:
        vlog(args, f"reading {path} for task {task_id}")
        if rotated_found:
            vlog(args, f"rotated found: {rotated_found}")

    matching = []
    total_parsed = 0
    after_level = 0
    for fp in reversed(file_paths):
        if not fp.exists():
            continue
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total_parsed += 1
                    try:
                        rec = json.loads(line)
                        lvl = str(rec.get("level", "info")).lower()
                        if level_hierarchy.get(lvl, 20) >= min_lvl_val:
                            after_level += 1
                        if str(rec.get("task_id")) != task_id:
                            continue
                        if level_hierarchy.get(lvl, 20) < min_lvl_val:
                            continue
                        matching.append(rec)
                    except Exception:
                        pass
        except Exception:
            pass
    if args:
        vlog(args, f"{total_parsed} parsed, {after_level} after level, {len(matching)} after task")
    return matching[-lines_count:]

def cmd_logs(args):
    logs_dir = REPO_ROOT / "state" / "logs"
    tasks_dir = REPO_ROOT / "state" / "tasks"

    json_output = []

    if args.task:
        task_id = args.task
        # 1. output.log
        out_log = tasks_dir / task_id / "output.log"
        if out_log.exists():
            vlog(args, f"reading {out_log}")
            content = out_log.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines:]
            if args.json:
                for l in content:
                    json_output.append({"source": "task-output", "message": l, "task_id": task_id})
            else:
                print(f"=== {task_id}/output.log (last {args.lines}) ===")
                for l in content:
                    print(l)

        # 2. worker.jsonl / subprocess.jsonl / admin.jsonl with task_id across all rotated files
        for sname in ["worker", "subprocess", "admin"]:
            fpath = logs_dir / f"{sname}.jsonl"
            task_recs = find_records_by_task_id(fpath, task_id, args.lines, args.level or "debug", args=args)
            if task_recs:
                if args.json:
                    for r in task_recs:
                        r["source"] = sname
                        json_output.append(r)
                else:
                    print(f"\n=== {sname}.jsonl (task={task_id}, last {args.lines}) ===")
                    for r in task_recs:
                        print(f"{r.get('ts','')} [{r.get('level','INFO')}] {r.get('logger','')} {r.get('event','')} {r.get('message','')}")

    elif args.service:
        services_to_read = []
        if args.service == "all":
            services_to_read = ["admin", "orchestrator", "server", "worker", "subprocess"]
        else:
            services_to_read = [args.service]

        for sname in services_to_read:
            fpath = logs_dir / f"{sname}.jsonl"
            if not fpath.exists():
                fpath_alt = logs_dir / f"{sname}.log"
                if fpath_alt.exists():
                    fpath = fpath_alt
                else:
                    if not args.json:
                        print(f"[skipped: state/logs/{sname}.jsonl not found]")
                    continue

            recs = read_jsonl_file(fpath, args.lines, args.level or "debug", args=args)
            if args.json:
                for r in recs:
                    if "source" not in r:
                        r["source"] = sname
                    json_output.append(r)
            else:
                print(f"=== {sname} (last {args.lines}) ===")
                for r in recs:
                    if isinstance(r, dict) and "ts" in r:
                        print(f"{r.get('ts','')} [{r.get('level','INFO')}] {r.get('logger','')} {r.get('event','')} {r.get('message','')}")
                    else:
                        print(str(r))
                print()

    else:
        # Default: last 10 lines of each service log + last 5 lines of each running task output.log
        for sname in ["admin", "orchestrator", "server", "worker"]:
            for ext in [".jsonl", ".log"]:
                fpath = logs_dir / f"{sname}{ext}"
                if fpath.exists():
                    recs = read_jsonl_file(fpath, 10, "debug", args=args)
                    if args.json:
                        for r in recs:
                            r["source"] = sname
                            json_output.append(r)
                    else:
                        print(f"=== {sname}{ext} (last 10) ===")
                        for r in recs:
                            if isinstance(r, dict) and "ts" in r:
                                print(f"{r.get('ts','')} [{r.get('level','INFO')}] {r.get('message','')}")
                            else:
                                print(str(r))
                    break

        # Running tasks output.log
        tasks_db = REPO_ROOT / "state" / "tasks.db"
        if tasks_db.exists():
            try:
                with sqlite3.connect(tasks_db) as conn:
                    conn.row_factory = sqlite3.Row
                    running_tasks = conn.execute("SELECT id FROM tasks WHERE status = 'running'").fetchall()
                    for rt in running_tasks:
                        tid = rt["id"]
                        out_log = tasks_dir / tid / "output.log"
                        if out_log.exists():
                            content = out_log.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
                            if args.json:
                                for l in content:
                                    json_output.append({"source": "task-output", "message": l, "task_id": tid})
                            else:
                                print(f"=== task {tid} output.log (last 5) ===")
                                for l in content:
                                    print(l)
            except Exception:
                pass

    if args.json:
        print(json.dumps(json_output, indent=2))

    if args.follow:
        print("Following logs (Ctrl+C to exit)...")
        try:
            target_files = []
            if args.service and args.service != "all":
                for ext in [".jsonl", ".log"]:
                    p = logs_dir / f"{args.service}{ext}"
                    if p.exists():
                        target_files.append(p)
            elif args.task:
                p = tasks_dir / args.task / "output.log"
                if p.exists():
                    target_files.append(p)
            else:
                target_files = list(logs_dir.glob("*.jsonl")) + list(logs_dir.glob("*.log"))

            file_positions = {f: f.stat().st_size for f in target_files if f.exists()}
            while True:
                time.sleep(0.2)
                for f in list(file_positions.keys()):
                    if not f.exists():
                        continue
                    st = f.stat()
                    curr_size = st.st_size
                    prev_size = file_positions[f]
                    if curr_size < prev_size:
                        file_positions[f] = 0
                        prev_size = 0
                    if curr_size > prev_size:
                        with f.open("r", encoding="utf-8", errors="replace") as stream:
                            stream.seek(prev_size)
                            new_data = stream.read()
                            if new_data:
                                print(f"[{f.name}] {new_data}", end="")
                        file_positions[f] = curr_size
        except KeyboardInterrupt:
            print("\nStopped following.")

# --- 4.6 backup ---
def _plan_backup(args, cfg) -> dict:
    backups_dir = REPO_ROOT / cfg.get("backups_dir", "state/backups")
    do_content = args.content_only or (not args.output_only and not args.state_only)
    do_output = args.output_only or (not args.content_only and not args.state_only)
    do_state = args.state_only

    plan = {
        "backups_dir": str(backups_dir.relative_to(REPO_ROOT) if backups_dir.is_relative_to(REPO_ROOT) else backups_dir),
        "sources": {},
        "valid": True,
        "errors": []
    }

    if do_content:
        content_db = REPO_ROOT / "state" / "content.db"
        if not content_db.exists():
            plan["valid"] = False
            plan["errors"].append("content.db not found")
        else:
            size = content_db.stat().st_size
            try:
                t0 = time.time()
                with sqlite3.connect(content_db) as conn:
                    res = conn.execute("PRAGMA quick_check;").fetchone()
                    qc = res[0] if res else "failed"
                dt = (time.time() - t0) * 1000
                vlog(args, f"quick_check {content_db}: {qc} in {dt:.1f}ms")
            except Exception as e:
                qc = f"error: {e}"
            plan["sources"]["content_db"] = {"path": str(content_db.relative_to(REPO_ROOT)), "size_bytes": size, "quick_check": qc}

    if do_output:
        output_dir = REPO_ROOT / "output"
        if not output_dir.exists():
            plan["sources"]["output"] = {"exists": False, "files": 0, "size_bytes": 0}
        else:
            files = [p for p in output_dir.rglob("*") if p.is_file()]
            total_size = sum(p.stat().st_size for p in files)
            plan["sources"]["output"] = {"exists": True, "files": len(files), "size_bytes": total_size}

    if do_state:
        state_dir = REPO_ROOT / "state"
        if not state_dir.exists():
            plan["sources"]["state"] = {"exists": False, "files": 0, "size_bytes": 0}
        else:
            files = []
            for p in state_dir.rglob("*"):
                if p.is_dir():
                    continue
                rel = p.relative_to(state_dir)
                parts = rel.parts
                if parts and parts[0] == "backups":
                    continue
                if p.name.endswith("-wal") or p.name.endswith("-shm"):
                    continue
                if "tasks" in parts and "output.log" in p.name:
                    continue
                files.append(p)
            total_size = sum(p.stat().st_size for p in files)
            plan["sources"]["state"] = {"exists": True, "files": len(files), "size_bytes": total_size}

    # Check backups_dir parent writable (read-only check)
    parent = backups_dir.parent
    if not parent.exists():
        plan["valid"] = False
        plan["errors"].append(f"parent directory missing: {parent}")
    elif not os.access(parent, os.W_OK):
        plan["valid"] = False
        plan["errors"].append(f"parent directory not writable: {parent}")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    plan["ts"] = ts

    cdb_size = plan["sources"]["content_db"]["size_bytes"] if "content_db" in plan["sources"] else 0
    out_size = plan["sources"]["output"]["size_bytes"] if "output" in plan["sources"] and plan["sources"]["output"]["exists"] else 0
    st_size = plan["sources"]["state"]["size_bytes"] if "state" in plan["sources"] and plan["sources"]["state"]["exists"] else 0

    would_create = []
    total_est = 0
    if "content_db" in plan["sources"]:
        sz_kb = max(1, cdb_size // 1024)
        would_create.append(f"content.db.{ts}.bak (est ~{sz_kb} KB)")
        total_est += cdb_size
    if "output" in plan["sources"] and plan["sources"]["output"]["exists"]:
        est_out = int(out_size * 0.3)
        sz_out_mb = max(1, est_out // (1024 * 1024))
        would_create.append(f"output.{ts}.tar.gz (est ~{sz_out_mb} MB, compressed)")
        total_est += est_out
    if "state" in plan["sources"] and plan["sources"]["state"]["exists"]:
        est_st = int(st_size * 0.4)
        sz_st_mb = max(1, est_st // (1024 * 1024))
        would_create.append(f"state.{ts}.tar.gz (est ~{sz_st_mb} MB, compressed)")
        total_est += est_st
    plan["would_create"] = would_create
    plan["total_est_bytes"] = total_est

    return plan

def _execute_backup(args, cfg, plan) -> dict:
    backups_dir = REPO_ROOT / cfg.get("backups_dir", "state/backups")
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = plan.get("ts") or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = {}

    if "content_db" in plan["sources"]:
        content_db = REPO_ROOT / "state" / "content.db"
        bak_path = backups_dir / f"content.db.{ts}.bak"
        t0 = time.time()
        vlog(args, f"writing {bak_path}")
        _backup_db(content_db, bak_path)
        with sqlite3.connect(bak_path) as conn:
            res = conn.execute("PRAGMA quick_check;").fetchone()
            if not res or res[0] != "ok":
                raise Exception("Backup quick_check failed")
        dt = (time.time() - t0) * 1000
        vlog(args, f"verify {bak_path}: quick_check=ok size={bak_path.stat().st_size} in {dt:.1f}ms")
        results["content_db"] = {"path": str(bak_path.relative_to(REPO_ROOT)), "size_bytes": bak_path.stat().st_size}

    if "output" in plan["sources"] and plan["sources"]["output"]["exists"]:
        output_dir = REPO_ROOT / "output"
        out_tar = backups_dir / f"output.{ts}.tar.gz"
        t0 = time.time()
        vlog(args, f"writing {out_tar}")
        with tarfile.open(out_tar, "w:gz") as tar:
            tar.add(output_dir, arcname="output")
        dt = (time.time() - t0) * 1000
        vlog(args, f"verify {out_tar}: size={out_tar.stat().st_size} in {dt:.1f}ms")
        results["output"] = {"path": str(out_tar.relative_to(REPO_ROOT)), "size_bytes": out_tar.stat().st_size}

    if "state" in plan["sources"] and plan["sources"]["state"]["exists"]:
        state_dir = REPO_ROOT / "state"
        state_tar = backups_dir / f"state.{ts}.tar.gz"
        t0 = time.time()
        vlog(args, f"writing {state_tar}")
        with tarfile.open(state_tar, "w:gz") as tar:
            for p in state_dir.rglob("*"):
                if p.is_dir():
                    continue
                rel = p.relative_to(state_dir)
                parts = rel.parts
                if parts and parts[0] == "backups":
                    continue
                if p.name.endswith("-wal") or p.name.endswith("-shm"):
                    continue
                if "tasks" in parts and "output.log" in p.name:
                    continue
                tar.add(p, arcname=str(Path("state") / rel))
        dt = (time.time() - t0) * 1000
        vlog(args, f"verify {state_tar}: size={state_tar.stat().st_size} in {dt:.1f}ms")
        results["state"] = {"path": str(state_tar.relative_to(REPO_ROOT)), "size_bytes": state_tar.stat().st_size}

    return results

def cmd_backup(args):
    cfg = load_config()
    plan = _plan_backup(args, cfg)
    
    if getattr(args, "dry_run", False):
        if not plan["valid"]:
            print(f"Dry run failed: {plan['errors']}", file=sys.stderr)
            safe_exit(1)
        print("Dry run: backup")
        print(f"Target: {plan['backups_dir']}/")
        print("Sources:")
        if "content_db" in plan["sources"]:
            cdb = plan["sources"]["content_db"]
            print(f"  content.db: {cdb['size_bytes']} bytes (quick_check {cdb['quick_check']})")
        if "output" in plan["sources"] and plan["sources"]["output"]["exists"]:
            out = plan["sources"]["output"]
            print(f"  output/: {out['files']} files, {out['size_bytes'] / (1024*1024):.1f} MB")
        if "state" in plan["sources"] and plan["sources"]["state"]["exists"]:
            st = plan["sources"]["state"]
            print(f"  state/: {st['files']} files, {st['size_bytes'] / (1024*1024):.1f} MB (excludes backups, wal, task output.logs)")
        print("Would create:")
        for item in plan["would_create"]:
            print(f"  {item}")
        tot_mb = plan["total_est_bytes"] / (1024 * 1024)
        if tot_mb < 1:
            tot_kb = max(1, plan["total_est_bytes"] // 1024)
            print(f"Total estimated: ~{tot_kb} KB")
        else:
            print(f"Total estimated: ~{tot_mb:.0f} MB")
        safe_exit(0)

    if not plan["valid"]:
        print(f"Backup failed: {plan['errors']}", file=sys.stderr)
        safe_exit(1)

    t0 = time.time()
    try:
        results = _execute_backup(args, cfg, plan)
        dt = (time.time() - t0) * 1000
        vlog(args, f"done in {dt:.1f}ms")
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print("Backup completed successfully:")
            for k, v in results.items():
                if v:
                    print(f"  - {k}: {v['path']} ({v['size_bytes']} bytes)")
    except Exception as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        safe_exit(1)

# --- 4.7 restore ---
def cmd_restore(args):
    backup_path = Path(args.backup_path)
    if not backup_path.is_absolute():
        backup_path = REPO_ROOT / backup_path

    vlog(args, f"backup path: {backup_path}")
    if not backup_path.exists():
        print(f"Backup file not found: {backup_path}", file=sys.stderr)
        safe_exit(1)

    bname = backup_path.name
    btype = None
    if bname.endswith(".bak") or "content.db" in bname:
        btype = "content_db"
    elif "output" in bname and bname.endswith(".tar.gz"):
        btype = "output"
    elif "state" in bname and bname.endswith(".tar.gz"):
        btype = "state"
    else:
        if backup_path.name.endswith(".tar.gz"):
            try:
                with tarfile.open(backup_path, "r:gz") as tar:
                    names = tar.getnames()
                    if any("output/" in n for n in names):
                        btype = "output"
                    elif any("state/" in n for n in names):
                        btype = "state"
            except Exception:
                pass
        if not btype:
            btype = "content_db"

    vlog(args, f"detected type: {btype}")

    cfg = load_config()
    backups_dir = REPO_ROOT / cfg.get("backups_dir", "state/backups")
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    safety_backup = None
    if not args.dry_run:
        try:
            if btype == "content_db":
                live_db = REPO_ROOT / "state" / "content.db"
                if live_db.exists():
                    safe_path = backups_dir / f"pre-restore.{ts}.content.db.bak"
                    _backup_db(live_db, safe_path)
                    safety_backup = str(safe_path.relative_to(REPO_ROOT))
            elif btype == "output":
                live_out = REPO_ROOT / "output"
                if live_out.exists():
                    safe_path = backups_dir / f"pre-restore.{ts}.output.tar.gz"
                    with tarfile.open(safe_path, "w:gz") as tar:
                        tar.add(live_out, arcname="output")
                    safety_backup = str(safe_path.relative_to(REPO_ROOT))
            elif btype == "state":
                live_state = REPO_ROOT / "state"
                if live_state.exists():
                    safe_path = backups_dir / f"pre-restore.{ts}.state.tar.gz"
                    with tarfile.open(safe_path, "w:gz") as tar:
                        tar.add(live_state, arcname="state")
                    safety_backup = str(safe_path.relative_to(REPO_ROOT))
            vlog(args, f"safety backup: {safety_backup or 'skipped'}")
        except Exception as e:
            print(f"Failed to create safety backup: {e}", file=sys.stderr)
            safe_exit(1)

    restored_to = None
    instructions = ""

    if btype == "content_db":
        restored_to = str(REPO_ROOT / "state" / "content.db.restored")
        vlog(args, f"extracting to {restored_to}")
        if not args.dry_run:
            import shutil
            shutil.copy2(backup_path, restored_to)
        instructions = (
            f"Restored to state/content.db.restored.\n"
            f"To apply:\n"
            f"  1. Stop all services (dev_launcher, worker).\n"
            f"  2. mv state/content.db state/content.db.old\n"
            f"  3. mv state/content.db.restored state/content.db\n"
            f"  4. rm state/content.db-wal state/content.db-shm (if present)\n"
            f"  5. Restart services."
        )
    elif btype == "output":
        restored_to = str(REPO_ROOT / "output.restored")
        vlog(args, f"extracting to {restored_to}")
        if not args.dry_run:
            with tarfile.open(backup_path, "r:gz") as tar:
                if sys.version_info >= (3, 12):
                    tar.extractall(path=REPO_ROOT / "output.restored_tmp", filter='data')
                else:
                    tar.extractall(path=REPO_ROOT / "output.restored_tmp")
            ext_path = REPO_ROOT / "output.restored_tmp" / "output"
            target_restored = REPO_ROOT / "output.restored"
            if target_restored.exists():
                import shutil
                shutil.rmtree(target_restored)
            if ext_path.exists():
                ext_path.rename(target_restored)
            else:
                (REPO_ROOT / "output.restored_tmp").rename(target_restored)
            try:
                import shutil
                shutil.rmtree(REPO_ROOT / "output.restored_tmp")
            except Exception:
                pass
        instructions = (
            f"Restored to output.restored/.\n"
            f"To apply:\n"
            f"  1. Stop services.\n"
            f"  2. mv output output.old\n"
            f"  3. mv output.restored output\n"
            f"  4. Restart services."
        )
    elif btype == "state":
        restored_to = str(REPO_ROOT / "state.restored")
        vlog(args, f"extracting to {restored_to}")
        if not args.dry_run:
            with tarfile.open(backup_path, "r:gz") as tar:
                if sys.version_info >= (3, 12):
                    tar.extractall(path=REPO_ROOT / "state.restored_tmp", filter='data')
                else:
                    tar.extractall(path=REPO_ROOT / "state.restored_tmp")
            ext_path = REPO_ROOT / "state.restored_tmp" / "state"
            target_restored = REPO_ROOT / "state.restored"
            if target_restored.exists():
                import shutil
                shutil.rmtree(target_restored)
            if ext_path.exists():
                ext_path.rename(target_restored)
            else:
                (REPO_ROOT / "state.restored_tmp").rename(target_restored)
            try:
                import shutil
                shutil.rmtree(REPO_ROOT / "state.restored_tmp")
            except Exception:
                pass
        instructions = (
            f"Restored to state.restored/.\n"
            f"To apply:\n"
            f"  1. Stop services.\n"
            f"  2. mv state state.old\n"
            f"  3. mv state.restored state\n"
            f"  4. Restart services."
        )

    if not args.yes and not args.dry_run:
        if not confirm("Do you wish to proceed with the restore preparation?"):
            print("Aborted.")
            return

    res = {
        "backup_path": str(backup_path.relative_to(REPO_ROOT)),
        "type": btype,
        "safety_backup": safety_backup,
        "restored_to": restored_to,
        "instructions": instructions
    }

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"Restore prepared successfully (type: {btype}).")
        if safety_backup:
            print(f"Safety backup created at: {safety_backup}")
        print(f"Restored staging path: {restored_to}")
        print("\nInstructions:")
        print(instructions)

# --- 4.8 integrity ---
def cmd_integrity(args):
    checks = []
    has_fail = False

    db_files = ["content.db", "users.db", "tasks.db", "app.db", "server.db"]
    for db_name in db_files:
        db_path = REPO_ROOT / "state" / db_name
        if not db_path.exists():
            checks.append({"name": f"quick_check:{db_name}", "status": "SKIP", "detail": "file not found"})
            continue
        try:
            t0 = time.time()
            with sqlite3.connect(db_path) as conn:
                res = conn.execute("PRAGMA quick_check;").fetchone()
                if res and res[0] == "ok":
                    checks.append({"name": f"quick_check:{db_name}", "status": "PASS", "detail": "ok"})
                else:
                    checks.append({"name": f"quick_check:{db_name}", "status": "FAIL", "detail": str(res)})
                    has_fail = True
            dt = (time.time() - t0) * 1000
            vlog(args, f"quick_check:{db_name}: status={checks[-1]['status']} in {dt:.1f}ms")
        except Exception as e:
            checks.append({"name": f"quick_check:{db_name}", "status": "FAIL", "detail": str(e)})
            has_fail = True

    content_db = REPO_ROOT / "state" / "content.db"
    if content_db.exists():
        try:
            with sqlite3.connect(content_db) as conn:
                try:
                    conn.enable_load_extension(True)
                    sqlite_vec.load(conn)
                    conn.enable_load_extension(False)
                    checks.append({"name": "vec_extension_loadable", "status": "PASS", "detail": "ok"})
                except Exception as e:
                    checks.append({"name": "vec_extension_loadable", "status": "FAIL", "detail": str(e)})
                    has_fail = True

                t0 = time.time()
                tbls = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                vlog(args, f"running: SELECT name FROM sqlite_master WHERE type='table'")
                dt = (time.time() - t0) * 1000
                vlog(args, f"schema introspection in {dt:.1f}ms")
                core_tables = ["articles", "article_translations", "questions", "question_translations", "articles_fts", "questions_fts"]
                missing_core = [t for t in core_tables if t not in tbls]
                existing_core = [t for t in core_tables if t in tbls]

                # Row counts
                counts = {}
                for t in core_tables:
                    if t in tbls:
                        cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        counts[t] = cnt
                    else:
                        counts[t] = "missing"

                # Content state categorization
                all_missing = len(missing_core) == len(core_tables) or (not existing_core and not tbls)
                all_counts_zero_or_missing = all(counts.get(t) == 0 or counts.get(t) == "missing" for t in core_tables)

                if all_missing or all_counts_zero_or_missing:
                    checks.append({"name": "content_state", "status": "SKIP", "detail": "content.db empty - run sync_all first"})
                elif missing_core:
                    checks.append({"name": "content_state", "status": "FAIL", "detail": f"schema incomplete: missing tables {missing_core}"})
                    has_fail = True
                else:
                    checks.append({"name": "content_state", "status": "PASS", "detail": str(counts)})

                checks.append({"name": "content_row_counts", "status": "PASS", "detail": str(counts)})

                if counts.get("articles_fts") != "missing" and counts.get("article_translations") != "missing":
                    if counts["articles_fts"] == counts["article_translations"]:
                        checks.append({"name": "fts_articles_parity", "status": "PASS", "detail": f"count={counts['articles_fts']}"})
                    else:
                        checks.append({"name": "fts_articles_parity", "status": "FAIL", "detail": f"articles_fts ({counts['articles_fts']}) != article_translations ({counts['article_translations']})"})
                        has_fail = True

                if counts.get("questions_fts") != "missing" and counts.get("question_translations") != "missing":
                    if counts["questions_fts"] == counts["question_translations"]:
                        checks.append({"name": "fts_questions_parity", "status": "PASS", "detail": f"count={counts['questions_fts']}"})
                    else:
                        checks.append({"name": "fts_questions_parity", "status": "FAIL", "detail": f"questions_fts ({counts['questions_fts']}) != question_translations ({counts['question_translations']})"})
                        has_fail = True

                if "schema_meta" in tbls:
                    checks.append({"name": "vec_schema_meta", "status": "PASS", "detail": "schema_meta table present"})
                    for t_name in ["article_embeddings", "question_embeddings"]:
                        if t_name in tbls:
                            cnt = conn.execute(f"SELECT COUNT(*) FROM {t_name}").fetchone()[0]
                            checks.append({"name": f"vec_table_{t_name}", "status": "PASS", "detail": f"rowcount={cnt}"})
                        else:
                            checks.append({"name": f"vec_table_{t_name}", "status": "SKIP", "detail": "table missing"})

                    for t_name in ["article_embeddings", "question_embeddings"]:
                        if t_name in tbls:
                            sample = conn.execute(f"SELECT embedding FROM {t_name} LIMIT 10").fetchall()
                            if sample:
                                valid_len = all(row[0] and len(row[0]) == 512 for row in sample if row[0] is not None)
                                if valid_len:
                                    checks.append({"name": f"vec_embedding_length_{t_name}", "status": "PASS", "detail": "all sampled embeddings are 512 bytes"})
                                else:
                                    checks.append({"name": f"vec_embedding_length_{t_name}", "status": "FAIL", "detail": "sampled embedding length != 512 bytes"})
                                    has_fail = True
                            else:
                                checks.append({"name": f"vec_embedding_length_{t_name}", "status": "SKIP", "detail": "table empty"})
                else:
                    checks.append({"name": "vec_status", "status": "SKIP", "detail": "vec not initialized (schema_meta absent)"})

        except Exception as e:
            checks.append({"name": "content_db_checks", "status": "FAIL", "detail": str(e)})
            has_fail = True
    else:
        checks.append({"name": "content_db_checks", "status": "SKIP", "detail": "content.db not found"})

    result = {"checks": checks}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("=== knowctl integrity ===")
        for c in checks:
            print(f"[{c['status']}] {c['name']}: {c['detail']}")

    safe_exit(1 if has_fail else 0)

def main():
    configure_telemetry("knowctl")
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity")

    parser = argparse.ArgumentParser(description="knowctl — Operational CLI (MVP)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", parents=[parent_parser], help="Check system status")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")

    # tasks
    p_tasks = subparsers.add_parser("tasks", parents=[parent_parser], help="Task management")
    p_tasks_sub = p_tasks.add_subparsers(dest="tasks_command", required=True)

    # tasks list
    p_tlist = p_tasks_sub.add_parser("list", parents=[parent_parser], help="List tasks")
    p_tlist.add_argument("--status", help="Filter by status")
    p_tlist.add_argument("--orphaned", action="store_true", help="Show only orphaned tasks")
    p_tlist.add_argument("--limit", type=int, default=50, help="Limit results")
    p_tlist.add_argument("--json", action="store_true", help="Output as JSON")

    # tasks interrupt
    p_tinter = p_tasks_sub.add_parser("interrupt", parents=[parent_parser], help="Interrupt a task")
    p_tinter.add_argument("task_id", help="Task ID")
    p_tinter.add_argument("--yes", action="store_true", help="Skip confirmation")
    p_tinter.add_argument("--dry-run", action="store_true", help="Show interruption plan without executing")
    p_tinter.add_argument("--json", action="store_true", help="Output as JSON")

    # tasks cleanup-orphaned
    p_tclean = p_tasks_sub.add_parser("cleanup-orphaned", parents=[parent_parser], help="Cleanup orphaned running tasks")
    p_tclean.add_argument("--dry-run", action="store_true", help="Show orphaned tasks without updating")
    p_tclean.add_argument("--yes", action="store_true", help="Skip confirmation")
    p_tclean.add_argument("--json", action="store_true", help="Output as JSON")

    # logs
    p_logs = subparsers.add_parser("logs", parents=[parent_parser], help="View logs")
    p_logs.add_argument("--service", help="Service name (admin, orchestrator, server, worker, subprocess, all)")
    p_logs.add_argument("--task", help="Task ID")
    p_logs.add_argument("--lines", type=int, default=50, help="Number of lines")
    p_logs.add_argument("--follow", action="store_true", help="Follow logs (tail -f)")
    p_logs.add_argument("--level", help="Minimum log level (debug, info, warning, error)")
    p_logs.add_argument("--json", action="store_true", help="Output as JSON")

    # backup
    p_backup = subparsers.add_parser("backup", parents=[parent_parser], help="Create backups")
    p_backup.add_argument("--content-only", action="store_true", help="Backup content.db only")
    p_backup.add_argument("--output-only", action="store_true", help="Backup output/ only")
    p_backup.add_argument("--state-only", action="store_true", help="Backup state/ only")
    p_backup.add_argument("--dry-run", action="store_true", help="Show backup plan without executing")
    p_backup.add_argument("--json", action="store_true", help="Output as JSON")

    # restore
    p_restore = subparsers.add_parser("restore", parents=[parent_parser], help="Restore from backup")
    p_restore.add_argument("backup_path", help="Path to backup file")
    p_restore.add_argument("--yes", action="store_true", help="Skip confirmation")
    p_restore.add_argument("--dry-run", action="store_true", help="Show restore plan without writing")
    p_restore.add_argument("--json", action="store_true", help="Output as JSON")

    # integrity
    p_integ = subparsers.add_parser("integrity", parents=[parent_parser], help="Run database integrity and parity checks")
    p_integ.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "tasks":
        if args.tasks_command == "list":
            cmd_tasks_list(args)
        elif args.tasks_command == "interrupt":
            cmd_tasks_interrupt(args)
        elif args.tasks_command == "cleanup-orphaned":
            cmd_tasks_cleanup_orphaned(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "backup":
        cmd_backup(args)
    elif args.command == "restore":
        cmd_restore(args)
    elif args.command == "integrity":
        cmd_integrity(args)

if __name__ == "__main__":
    main()

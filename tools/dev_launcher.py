import subprocess
import sys
import argparse
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quizkb.settings import get_settings
from quizkb.lock import acquire as acquire_lock, LockAlreadyHeld

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote-worker", action="store_true", help="Do not start local worker")
    args = ap.parse_args()

    try:
        launcher_lock = acquire_lock(
            "know-dev-launcher",
            command="dev_launcher.py " + " ".join(sys.argv[1:]),
        )
    except LockAlreadyHeld as e:
        print(f"\n[FATAL] {e}", file=sys.stderr)
        print("[FATAL] Refusing to start second launcher.", file=sys.stderr)
        print("[FATAL] If the previous launcher crashed, wait 5s (kernel releases lock),", file=sys.stderr)
        sys.exit(1)

    print("Starting Orchestrator Daemon...")
    orchestrator = subprocess.Popen([
        "uv", "run", "--group", "web", "uvicorn", "orchestrator.app:app", 
        "--host", "0.0.0.0", "--port", "8002", "--reload", 
        "--reload-dir", "orchestrator", "--reload-dir", "quizkb"
    ])

    print("Starting FastAPI Admin...")
    admin = subprocess.Popen([
        "uv", "run", "--group", "web", "uvicorn", "web.app:app", 
        "--host", "0.0.0.0", "--port", "8000", "--reload", 
        "--reload-dir", "web", "--reload-dir", "quizkb"
    ])
    
    print("Starting Public API Server...")
    server = subprocess.Popen([
        "uv", "run", "--group", "web", "uvicorn", "server.app:app", 
        "--host", "0.0.0.0", "--port", "8001", "--reload",
        "--reload-dir", "server", "--reload-dir", "quizkb"
    ])

    worker = None
    if not args.remote_worker:
        print("Waiting 2 seconds for orchestrator to start...")
        time.sleep(2)
        print("Starting Worker Daemon...")
        worker = subprocess.Popen([
            sys.executable, "scripts/worker.py", "--orchestrator-url",
            get_settings().orchestrator_url
        ])

    try:
        admin.wait()
    except KeyboardInterrupt:
        print("\nShutting down dev services...")
        if worker:
            worker.terminate()
        admin.terminate()
        server.terminate()
        orchestrator.terminate()
    finally:
        if worker:
            worker.wait()
        server.wait()
        admin.wait()
        orchestrator.wait()
        launcher_lock.release()

if __name__ == "__main__":
    main()

#!/usr/bin/env bash
set -e

echo "=== Starting Worker Isolation Test ==="

mkdir -p state/tasks state/logs

# 1. Start Admin in background
uv run --group web uvicorn web.app:app --host 127.0.0.1 --port 8000 &
ADMIN_PID=$!
echo "Admin started with PID $ADMIN_PID"
sleep 3

# 2. Start Worker in background
python scripts/worker.py --orchestrator-url http://127.0.0.1:8002 &
WORKER_PID=$!
echo "Worker started with PID $WORKER_PID"
sleep 2

# 3. Inject a queued task directly into SQLite DB
echo "Injecting test task into DB..."
python3 -c "
import sqlite3, datetime
conn = sqlite3.connect('state/tasks.db')
conn.execute('INSERT OR REPLACE INTO tasks (id, action, domain, lang, status, started_at, kill_requested) VALUES (?, ?, ?, ?, ?, ?, ?)',
             ('test_task_001', 'bootstrap', 'python', 'ru', 'queued', datetime.datetime.now().isoformat(), 0))
conn.commit()
conn.close()
"

echo "Waiting for worker to claim and run task..."
sleep 5

# 4. Kill Admin
echo "Killing Admin (PID $ADMIN_PID)..."
kill -9 $ADMIN_PID || true

echo "Verifying Worker is still alive..."
if kill -0 $WORKER_PID 2>/dev/null; then
    echo "SUCCESS: Worker is still running independently after Admin death!"
else
    echo "FAILURE: Worker died along with Admin."
    kill -9 $WORKER_PID 2>/dev/null || true
    exit 1
fi

# 5. Restart Admin
echo "Restarting Admin..."
uv run --group web uvicorn web.app:app --host 127.0.0.1 --port 8000 &
ADMIN_PID=$!
sleep 3

# 6. Check task status from DB
echo "Checking task status from DB..."
python3 -c "
import sqlite3
conn = sqlite3.connect('state/tasks.db')
row = conn.execute('SELECT status FROM tasks WHERE id = ?', ('test_task_001',)).fetchone()
print(f'Test task status in DB: {row[0] if row else \"not found\"}')
conn.close()
"

# Cleanup
echo "Cleaning up processes..."
kill -9 $ADMIN_PID $WORKER_PID 2>/dev/null || true
echo "=== Test Completed Successfully ==="

Write-Host "=== Starting Worker Isolation Test (PowerShell) ==="

# 1. Start Admin
$adminProcess = Start-Process -FilePath "uv" -ArgumentList "run", "--group", "web", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", "8000" -PassThru
Start-Sleep -Seconds 4

# 2. Start Worker
$workerProcess = Start-Process -FilePath "python" -ArgumentList "scripts/worker.py", "--orchestrator-url", "http://127.0.0.1:8002" -PassThru
Start-Sleep -Seconds 2

# 3. Inject task via Python
python -c "import sqlite3, datetime; conn = sqlite3.connect('state/tasks.db'); conn.execute('INSERT OR REPLACE INTO tasks (id, action, domain, lang, status, started_at, kill_requested) VALUES (?, ?, ?, ?, ?, ?, ?)', ('test_task_001', 'bootstrap', 'python', 'ru', 'queued', datetime.datetime.now().isoformat(), 0)); conn.commit(); conn.close()"

Start-Sleep -Seconds 5

# 4. Kill Admin
Write-Host "Killing Admin (PID $($adminProcess.Id))..."
Stop-Process -Id $adminProcess.Id -Force

Start-Sleep -Seconds 2

# 5. Check if Worker is still running
if (Get-Process -Id $workerProcess.Id -ErrorAction SilentlyContinue) {
    Write-Host "SUCCESS: Worker is still running independently after Admin death!" -ForegroundColor Green
} else {
    Write-Host "FAILURE: Worker died along with Admin." -ForegroundColor Red
    exit 1
}

# 6. Restart Admin
$adminProcess = Start-Process -FilePath "uv" -ArgumentList "run", "--group", "web", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", "8000" -PassThru
Start-Sleep -Seconds 4

# Cleanup
Stop-Process -Id $adminProcess.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $workerProcess.Id -Force -ErrorAction SilentlyContinue

Write-Host "=== Test Completed Successfully ==="

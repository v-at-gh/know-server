if (-not $env:OTEL_EXPORTER_OTLP_ENDPOINT) {
    Write-Host "[info] OTEL_EXPORTER_OTLP_ENDPOINT unset -- telemetry disabled." -ForegroundColor Yellow
    Write-Host "       Example: `$env:OTEL_EXPORTER_OTLP_ENDPOINT = 'http://127.0.0.1:4317'"
}
uv run --group web python tools/dev_launcher.py

#!/bin/bash
set -e
cd "$(dirname "$0")/.."

if [ -z "${OTEL_EXPORTER_OTLP_ENDPOINT:-}" ]; then
    echo "[info] OTEL_EXPORTER_OTLP_ENDPOINT unset -- telemetry disabled." >&2
    echo "       Set it to a collector endpoint if you want metrics/traces:" >&2
    echo "       export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317" >&2
fi

uv run --group web python tools/dev_launcher.py

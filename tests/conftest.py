import os
import resource as _resource

# Bump file descriptor limit on macOS for large test suites.
# Default is 256, pytest needs ~500+ concurrent fds with sqlite/httpx.
try:
    _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
    if _soft < 4096:
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (min(4096, _hard), _hard))
except (ValueError, OSError):
    pass  # Windows doesn't have this; or permissions restrict

os.environ.setdefault("OTEL_SDK_DISABLED", "true")

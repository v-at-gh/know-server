from pathlib import Path
import logging
from quizkb.observability import configure_logging

def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    configure_logging("admin", log_dir or Path("state/logs"), level)

from pathlib import Path
import re

def test_counter_names_match_otel_convention():
    worker_py = (Path(__file__).parent.parent / "scripts" / "worker.py").read_text()
    assert re.search(r'create_counter\(\s*"worker_tasks_started"\s*,', worker_py)
    assert re.search(r'create_counter\(\s*"worker_tasks_finished"\s*,', worker_py)
    assert not re.search(r'create_counter\(\s*"worker_tasks_started_total"\s*,', worker_py)
    assert not re.search(r'create_counter\(\s*"worker_tasks_finished_total"\s*,', worker_py)
import pytest
import json
from pathlib import Path
from quizkb.logging import Logger
from quizkb.state import recent_errors
from quizkb.translation import _parse_one_question

def test_logger_parse_reject_splits_reason(tmp_path):
    logger = Logger(root=tmp_path)
    run_dir = logger.run_dir
    logger.parse_reject(label="test:label", reason="parse_reject: Q1: 3 hints", prompt="prompt", raw_response="raw")
    logger.close()

    errors_file = run_dir / "errors.jsonl"
    line = errors_file.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert obj["reason"] == "parse_reject"
    assert obj["detail"] == "Q1: 3 hints"

def test_logger_parse_reject_no_split_for_unknown_prefix(tmp_path):
    logger = Logger(root=tmp_path)
    run_dir = logger.run_dir
    logger.parse_reject(label="test:label", reason="unknown_reason: some text", prompt="prompt", raw_response="raw")
    logger.close()

    errors_file = run_dir / "errors.jsonl"
    line = errors_file.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert obj["reason"] == "unknown_reason: some text"
    assert obj["detail"] == ""

def test_logger_parse_reject_empty_reason(tmp_path):
    logger = Logger(root=tmp_path)
    run_dir = logger.run_dir
    logger.parse_reject(label="test:label", reason="no_response", prompt="prompt", raw_response="raw")
    logger.close()

    errors_file = run_dir / "errors.jsonl"
    line = errors_file.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert obj["reason"] == "no_response"
    assert obj["detail"] == ""

def test_recent_errors_backward_compat(tmp_path):
    run_dir = tmp_path / "run_old"
    run_dir.mkdir(parents=True)
    errors_file = run_dir / "errors.jsonl"
    old_obj = {
        "kind": "parse_reject",
        "ts": "2026-01-01T00:00:00Z",
        "label": "test:label",
        "reason": "parse_reject",
        "prompt": "p",
        "raw_response": "r"
    }
    errors_file.write_text(json.dumps(old_obj) + "\n", encoding="utf-8")

    records = recent_errors(tmp_path)
    assert len(records) == 1
    assert records[0].detail == ""

def test_recent_errors_reads_detail(tmp_path):
    run_dir = tmp_path / "run_new"
    run_dir.mkdir(parents=True)
    errors_file = run_dir / "errors.jsonl"
    new_obj = {
        "kind": "parse_reject",
        "ts": "2026-01-01T00:00:00Z",
        "label": "test:label",
        "reason": "parse_reject",
        "detail": "Q1: 3 hints",
        "prompt": "p",
        "raw_response": "r"
    }
    errors_file.write_text(json.dumps(new_obj) + "\n", encoding="utf-8")

    records = recent_errors(tmp_path)
    assert len(records) == 1
    assert records[0].detail == "Q1: 3 hints"


def test_parse_one_question_dedupes_distractors():
    raw = """
--- Q1 ---
Q: What is 2+2?
A: 4
D: Неправильно
W: wrong 1
D: Неправильно
W: duplicate translation should be dropped
D: 5
W: wrong 5
D: 6
W: wrong 6
H: Hint 1
"""
    parsed, errs = _parse_one_question(raw)
    assert parsed is not None
    assert len(errs) == 0
    assert len(parsed["distractors"]) == 3


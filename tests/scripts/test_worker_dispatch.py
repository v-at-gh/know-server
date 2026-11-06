import pytest
import sys
from scripts.worker import _build_subprocess_cmd, _is_our_process

def test_build_subprocess_cmd_reindex_missing():
    task = {"kind": "reindex", "action": "reindex_missing"}
    cmd = _build_subprocess_cmd(task, "task_123")
    assert "reindex_vec.py" in cmd[2]
    assert "--missing" in cmd
    assert "--task-id" in cmd
    assert "task_123" in cmd

def test_build_subprocess_cmd_reindex_domain():
    task = {"kind": "reindex", "action": "reindex_domain", "domain": "linux", "lang": "ru"}
    cmd = _build_subprocess_cmd(task, "task_123")
    assert "reindex_vec.py" in cmd[2]
    assert "--domain" in cmd
    assert "linux" in cmd
    assert "--lang" in cmd
    assert "ru" in cmd

def test_build_subprocess_cmd_reindex_full():
    task = {"kind": "reindex", "action": "reindex_full"}
    cmd = _build_subprocess_cmd(task, "task_123")
    assert "--full" in cmd

def test_build_subprocess_cmd_reindex_prune():
    task = {"kind": "reindex", "action": "reindex_prune"}
    cmd = _build_subprocess_cmd(task, "task_123")
    assert "--prune" in cmd

def test_build_subprocess_cmd_generation():
    task = {"kind": "generation", "action": "bootstrap", "domain": "linux", "lang": "en"}
    cmd = _build_subprocess_cmd(task, "task_123")
    assert "generate_content.py" in cmd[2]
    assert "--action" in cmd
    assert "bootstrap" in cmd
    assert "--domain" in cmd
    assert "linux" in cmd
    assert "--lang" in cmd
    assert "en" in cmd

def test_is_our_process_rejects_none():
    assert _is_our_process(None, "abc") is False
    assert _is_our_process(0, "abc") is False

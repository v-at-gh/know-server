import sqlite3
import json
from pathlib import Path
import pytest
import sqlite_vec
from quizkb import db as kb_db
from scripts.generate_content import main as gen_main

class MockEmbedClient:
    def embed_batch(self, inputs, **kwargs):
        class Rec:
            embeddings = [[0.1] * 128 for _ in inputs]
            error = None
        return Rec()

def test_sync_after_generation_populates_db(tmp_path, monkeypatch):
    db_path = tmp_path / "state" / "content.db"
    output_root = tmp_path / "output"
    packs_json = tmp_path / "state" / "packs.json"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    packs_json.write_text('{"packs":[]}', encoding="utf-8")

    en_dir = output_root / "datasets" / "en"
    en_dir.mkdir(parents=True, exist_ok=True)
    domains_json = [{"slug": "linux", "name": "Linux OS", "kind": "topic"}]
    (en_dir / "domains.json").write_text(json.dumps(domains_json), encoding="utf-8")

    monkeypatch.setattr("quizkb.config.OUTPUT_ROOT", output_root)
    monkeypatch.setattr("scripts.generate_content.OUTPUT_ROOT", output_root)
    monkeypatch.setattr("scripts.generate_content.REPO_ROOT", tmp_path)
    monkeypatch.setattr("scripts.generate_content.OllamaClient", MockEmbedClient)

    fake_domains = [{"slug": "linux", "name": "Linux OS", "kind": "topic"}]
    monkeypatch.setattr("scripts.generate_content.DOMAINS", fake_domains)

    monkeypatch.setattr("quizkb.domain_meta.ensure_meta", lambda *a, **kw: None)

    monkeypatch.setattr("sys.argv", ["generate_content.py", "--action", "ensure_meta", "--domain", "linux", "--lang", "en"])

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr("os._exit", fake_exit)

    with pytest.raises(SystemExit) as exc:
        gen_main()

    assert exc.value.code == 0
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    domains = conn.execute("SELECT slug FROM domains").fetchall()
    conn.close()

    assert ("linux",) in domains

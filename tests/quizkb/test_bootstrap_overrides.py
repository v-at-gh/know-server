import pytest
import json
from pathlib import Path
from quizkb.jobs import bootstrap_domain
from quizkb.recovery import recover_thin_articles

def test_bootstrap_uses_articles_per_domain(tmp_path, monkeypatch):
    captured_prompts = []
    def mock_ask(prompt, predict=None, label=None):
        captured_prompts.append(prompt)
        return "--- A1 ---\nSLUG: art-one\nTITLE: Article One\nSUMMARY: Summary one.\n--- A2 ---\nSLUG: art-two\nTITLE: Article Two\nSUMMARY: Summary two.\n--- A3 ---\nSLUG: art-three\nTITLE: Article Three\nSUMMARY: Summary three."

    domain_cfg = {"slug": "testdom", "name": "Test", "description": "Desc", "articles_per_domain": 3}
    bootstrap_domain(mock_ask, output_root=tmp_path, domain_cfg=domain_cfg, verbose=False)

    assert len(captured_prompts) > 0
    assert "3-article" in captured_prompts[0]
    
    syl_path = tmp_path / "syllabus" / "en" / "testdom.json"
    items = json.loads(syl_path.read_text(encoding="utf-8"))
    assert len(items) == 3

def test_syllabus_prompt_default_still_10(tmp_path, monkeypatch):
    captured_prompts = []
    def mock_ask(prompt, predict=None, label=None):
        captured_prompts.append(prompt)
        return "\n".join([f"--- A{i} ---\nSLUG: art-{i}\nTITLE: Article {i}\nSUMMARY: Summary {i}." for i in range(1, 11)])

    domain_cfg = {"slug": "testdom", "name": "Test", "description": "Desc"}
    bootstrap_domain(mock_ask, output_root=tmp_path, domain_cfg=domain_cfg, verbose=False)

    assert len(captured_prompts) > 0
    assert "10-article" in captured_prompts[0]

def test_recover_respects_override(tmp_path, monkeypatch):
    syl_dir = tmp_path / "syllabus" / "en"
    know_dir = tmp_path / "knowledge" / "en" / "testdom"
    ds_dir = tmp_path / "datasets" / "en"
    syl_dir.mkdir(parents=True, exist_ok=True)
    know_dir.mkdir(parents=True, exist_ok=True)
    ds_dir.mkdir(parents=True, exist_ok=True)

    (syl_dir / "testdom.json").write_text('[{"order": 1, "slug": "art1", "title": "A1", "summary": "S1", "uuid": "u1"}]', encoding="utf-8")
    (know_dir / "art1.md").write_text("# Article One\n" + "x" * 2500, encoding="utf-8")
    (ds_dir / "testdom.json").write_text('[]', encoding="utf-8")

    ask_calls = 0
    def mock_ask(prompt, predict=None, label=None):
        nonlocal ask_calls
        ask_calls += 1
        return f"--- Q{ask_calls} ---\nQ: Question {ask_calls}?\nA: Answer\nD: Distractor 1\nW: Why 1\nD: Distractor 2\nW: Why 2\nD: Distractor 3\nW: Why 3\nD: Distractor 4\nW: Why 4\nH: Hint 1\nH: Hint 2\nH: Hint 3\nANSWER_WHY: Explanation"

    domain_cfg = {"slug": "testdom", "name": "Test", "description": "Desc"}
    res = recover_thin_articles(
        mock_ask,
        output_root=tmp_path,
        domains=[domain_cfg],
        questions_per_article_override=3,
        verbose=False
    )
    assert res["recovered"] == 3
    dataset = json.loads((ds_dir / "testdom.json").read_text(encoding="utf-8"))
    assert len(dataset) == 3

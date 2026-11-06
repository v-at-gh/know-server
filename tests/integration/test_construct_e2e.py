"""E2E: CONSTRUCT question generated, parsed, saved, reindexed."""
import json
from quizkb import generation, parsing


def test_construct_question_full_pipeline(tmp_path):
    """Simulate LLM returning a valid CONSTRUCT block; verify it lands in
    dataset.json with correct structure."""
    out_dir = tmp_path / "output"
    domain = "testdom"
    lang = "en"
    
    # Setup dataset & syllabus directories
    (out_dir / "datasets" / lang).mkdir(parents=True, exist_ok=True)
    (out_dir / "syllabus" / lang).mkdir(parents=True, exist_ok=True)
    (out_dir / "knowledge" / lang / domain).mkdir(parents=True, exist_ok=True)

    (out_dir / "syllabus" / lang / f"{domain}.json").write_text(
        json.dumps([{"order": 1, "slug": "art1", "title": "Article One", "summary": "Summary One"}]),
        encoding="utf-8"
    )
    (out_dir / "knowledge" / lang / domain / "art1.md").write_text(
        "# Article One content\n" + "x" * 2500, encoding="utf-8"
    )

    fake_llm_response = """--- Q1 ---
Q: Assemble find command for logs
T: find
T: /var/log
T: -name
DT: -exec
DT: -delete
DT: -print
H: start with find
H: specify directory
H: filter by name
"""
    fake_ask = lambda prompt, predict=None, label=None, timeout=120: fake_llm_response

    cfg = {
        "question_types": {"select": 0, "construct": 1},
        "articles_per_domain": 1,
    }

    generation.generate_questions_for_domain(
        fake_ask,
        output_root=out_dir,
        domain_slug=domain,
        domain_name="Test Domain",
        domain_cfg=cfg,
    )

    ds_file = out_dir / "datasets" / lang / f"{domain}.json"
    assert ds_file.exists()
    ds = json.loads(ds_file.read_text(encoding="utf-8"))
    assert len(ds) >= 1
    q = ds[0]
    assert q["type"] == "construct"
    assert q["text"] == "Assemble find command for logs"
    assert len(q["tokens"]) == 3
    assert q["solution"] == [0, 1, 2]
    assert len(q["distractorTokens"]) == 3
    assert len(q["hints"]) == 3
    assert "payloadJson" in q


def test_construct_question_rejected_on_validation_error():
    """Verify malformed CONSTRUCT block is rejected (e.g. missing distractor tokens)."""
    malformed_block = """--- Q1 ---
Q: Bad construct question
T: find
H: hint1
H: hint2
H: hint3
"""
    parsed, _ = parsing.parse_questions(malformed_block)
    assert len(parsed) == 0

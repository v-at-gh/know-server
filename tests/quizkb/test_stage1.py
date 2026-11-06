import pytest
from pathlib import Path
from quizkb.parsing import parse_questions, ParseDiagnostic
from quizkb.recovery import recover_thin_articles
from quizkb.translation import translate_articles, translate_single_question
from quizkb.jobs import bootstrap_domain, generate_questions_for_article

def test_parse_questions_returns_diagnostics():
    raw = """
--- Q1 ---
Q: What is 2+2?
A: 4
D: 3
W: wrong
D: 5
W: wrong
H: Hint 1
H: Hint 2
H: Hint 3

--- Q2 ---
Q: What is 3+3?
# missing A
D: 5
W: wrong
D: 7
W: wrong
H: Hint 1
H: Hint 2
H: Hint 3
"""
    parsed, diagnostics = parse_questions(raw)
    assert len(parsed) == 1
    assert len(diagnostics) == 1
    assert diagnostics[0].block_index == 2
    assert "no A" in diagnostics[0].errs

def test_parse_questions_expected_limit():
    raw = """
--- Q1 ---
Q: Q1?
A: 4
D: 3
W: wrong
D: 5
W: wrong
H: H1
H: H2
H: H3

--- Q2 ---
# invalid block trailing
"""
    parsed, diagnostics = parse_questions(raw, expected=1)
    assert len(parsed) == 1
    assert len(diagnostics) == 0

def test_parse_questions_empty_blocks():
    raw = "Just some text without delimiters."
    parsed, diagnostics = parse_questions(raw)
    assert parsed == []
    assert diagnostics == []

def test_recover_thin_articles_none_on_reject(tmp_path):
    syl_dir = tmp_path / "syllabus" / "en"
    syl_dir.mkdir(parents=True)
    (syl_dir / "linux.json").write_text('[{"order": 1, "slug": "intro", "uuid": "12345678-1234-1234-1234-1234567890ab", "title": "Intro", "summary": "Sum"}]', encoding="utf-8")

    know_dir = tmp_path / "knowledge" / "en" / "linux"
    know_dir.mkdir(parents=True)
    (know_dir / "intro.md").write_text("A" * 3000, encoding="utf-8")

    def fake_ask(prompt, *, predict, label="", think=True):
        return "--- Q1 ---\nQ: Bad q?\n# missing A\nD: d1\nW: w1\nD: d2\nW: w2\nH: h1\nH: h2\nH: h3"

    res = recover_thin_articles(
        fake_ask,
        output_root=tmp_path,
        domains=[{"slug": "linux", "name": "Linux"}],
        on_reject=None,
        max_attempts=1,
        verbose=False
    )
    assert res["given_up"] >= 1

def test_recover_thin_articles_on_reject_called(tmp_path):
    syl_dir = tmp_path / "syllabus" / "en"
    syl_dir.mkdir(parents=True)
    (syl_dir / "linux.json").write_text('[{"order": 1, "slug": "intro", "uuid": "12345678-1234-1234-1234-1234567890ab", "title": "Intro", "summary": "Sum"}]', encoding="utf-8")

    know_dir = tmp_path / "knowledge" / "en" / "linux"
    know_dir.mkdir(parents=True)
    (know_dir / "intro.md").write_text("A" * 3000, encoding="utf-8")

    rejections = []
    def fake_on_reject(label, reason, prompt, raw):
        rejections.append((label, reason))

    def fake_ask(prompt, *, predict, label="", think=True):
        return "--- Q1 ---\nQ: Bad q?\n# missing A\nD: d1\nW: w1\nD: d2\nW: w2\nH: h1\nH: h2\nH: h3"

    recover_thin_articles(
        fake_ask,
        output_root=tmp_path,
        domains=[{"slug": "linux", "name": "Linux"}],
        on_reject=fake_on_reject,
        max_attempts=1,
        verbose=False
    )
    assert len(rejections) == 1
    assert rejections[0][1].startswith("parse_reject")
    assert "Q1:" in rejections[0][1]

def test_translate_articles_none_on_reject(tmp_path):
    src_syl = tmp_path / "syllabus" / "en"
    src_syl.mkdir(parents=True)
    (src_syl / "linux.json").write_text('[{"order": 1, "slug": "intro", "uuid": "12345678-1234-1234-1234-1234567890ab", "title": "Intro", "summary": "Sum"}]', encoding="utf-8")

    src_know = tmp_path / "knowledge" / "en" / "linux"
    src_know.mkdir(parents=True)
    (src_know / "intro.md").write_text("A" * 3000, encoding="utf-8")

    def fake_ask(prompt, *, predict, label="", think=True):
        return "short"

    res = translate_articles(
        fake_ask,
        output_root=tmp_path,
        lang="ru",
        domain_slug="linux",
        predict=100,
        on_reject=None,
        verbose=False
    )
    assert res["failed"] >= 1

def test_translate_articles_on_reject_called(tmp_path):
    src_syl = tmp_path / "syllabus" / "en"
    src_syl.mkdir(parents=True)
    (src_syl / "linux.json").write_text('[{"order": 1, "slug": "intro", "uuid": "12345678-1234-1234-1234-1234567890ab", "title": "Intro", "summary": "Sum"}]', encoding="utf-8")

    src_know = tmp_path / "knowledge" / "en" / "linux"
    src_know.mkdir(parents=True)
    (src_know / "intro.md").write_text("A" * 3000, encoding="utf-8")

    def fake_ask(prompt, *, predict, label="", think=True):
        return "too short translation"

    rejections = []
    def fake_on_reject(label, reason, prompt, raw):
        rejections.append((label, reason))

    translate_articles(
        fake_ask,
        output_root=tmp_path,
        lang="ru",
        domain_slug="linux",
        predict=100,
        on_reject=fake_on_reject,
        verbose=False
    )
    assert len(rejections) == 1
    assert rejections[0][1] == "too_short"

def test_translate_single_question_forwards_on_reject(tmp_path):
    ds_path = tmp_path / "datasets" / "en"
    ds_path.mkdir(parents=True)
    (ds_path / "linux.json").write_text('[{"uuid": "12345678-1234-1234-1234-1234567890ab", "text": "Q", "answer": "A", "distractors": [{"text": "D", "why": "W"}], "hints": ["H"], "difficulty": 1}]', encoding="utf-8")

    def fake_ask(prompt, *, predict, label="", think=True):
        return "bad translation response"

    rejections = []
    def fake_on_reject(label, reason, prompt, raw):
        rejections.append((label, reason))

    translate_single_question(
        fake_ask,
        output_root=tmp_path,
        lang="ru",
        domain_slug="linux",
        uuid="12345678-1234-1234-1234-1234567890ab",
        on_reject=fake_on_reject,
        verbose=False
    )
    assert len(rejections) > 0
    assert any("parse_reject" in r[1] for r in rejections)

def test_bootstrap_domain_forwards_on_reject(tmp_path):
    called = []
    def fake_ask(prompt, *, predict, label="", think=True):
        if label.startswith("syl:"):
            return "--- A1 ---\nSLUG: intro\nTITLE: Intro\nSUMMARY: Sum"
        if label.startswith("art:"):
            return "A" * 3000
        return "bad question response"

    def fake_on_reject(label, reason, prompt, raw):
        called.append(reason)

    bootstrap_domain(
        fake_ask,
        output_root=tmp_path,
        domain_cfg={"slug": "linux", "name": "Linux", "description": "Desc"},
        on_reject=fake_on_reject,
        verbose=False
    )
    assert len(called) > 0

def test_generate_questions_for_article_aggregates_per_attempt(tmp_path):
    know_dir = tmp_path / "knowledge" / "en" / "linux"
    know_dir.mkdir(parents=True)
    (know_dir / "intro.md").write_text("A" * 3000, encoding="utf-8")

    syl_dir = tmp_path / "syllabus" / "en"
    syl_dir.mkdir(parents=True)
    (syl_dir / "linux.json").write_text('[{"order": 1, "slug": "intro", "uuid": "12345678-1234-1234-1234-1234567890ab", "title": "Intro", "summary": "Sum"}]', encoding="utf-8")

    def fake_ask(prompt, *, predict, label="", think=True):
        return "--- Q1 ---\nQ: Q?\n# missing A\nD: d1\nW: w1\nD: d2\nW: w2\nH: h1\nH: h2\nH: h3"

    rejections = []
    def fake_on_reject(label, reason, prompt, raw):
        rejections.append((label, reason))

    generate_questions_for_article(
        fake_ask,
        output_root=tmp_path,
        domain_slug="linux",
        lang="en",
        article_key="linux/intro",
        count=1,
        on_reject=fake_on_reject,
        max_attempts=2,
        verbose=False
    )
    assert len(rejections) == 2
    assert rejections[0][1].startswith("parse_reject")


def test_parse_questions_dedupes_distractors():
    raw = """
--- Q1 ---
Q: What is 2+2?
A: 4
D: 3
W: wrong 3
D: 3
W: duplicate distractor text should be dropped
D: 5
W: wrong 5
D: 6
W: wrong 6
H: Hint 1
H: Hint 2
H: Hint 3
"""
    parsed, diagnostics = parse_questions(raw)
    assert len(parsed) == 1
    assert len(diagnostics) == 0
    assert len(parsed[0]["distractors"]) == 3


def test_parse_domain_list():
    from quizkb.parsing import parse_domain_list
    raw = """
## 1. Early Long-Distance Signaling
Slug: `early-long-distance-signaling`
Title: Signals Across Distance: From Smoke Signals to Beacon Fires
Description: Explore the earliest methods...
"""
    items = parse_domain_list(raw)
    assert len(items) == 1
    assert items[0]["order"] == 1
    assert items[0]["slug"] == "early-long-distance-signaling"
    assert items[0]["title"] == "Signals Across Distance: From Smoke Signals to Beacon Fires"
    assert items[0]["description"] == "Explore the earliest methods..."


def test_parse_domain_list_hardened():
    from quizkb.parsing import parse_domain_list
    raw = """
### 1. Ранняя Сигнализация
Description: Первая строка описания.
Вторая строка описания.
Третья строка описания.

## 2. Linux Systems
Title: Linux Core Concepts
slug: linux-core
Description: Overview of Linux kernel and shell.
"""
    items = parse_domain_list(raw)
    assert len(items) == 2
    assert items[0]["order"] == 1
    assert items[0]["title"] == "Ранняя Сигнализация"
    assert "Первая строка" in items[0]["description"]
    assert "Третья строка" in items[0]["description"]
    assert items[0]["slug"] != ""

    assert items[1]["order"] == 2
    assert items[1]["slug"] == "linux-core"
    assert items[1]["title"] == "Linux Core Concepts"
    assert items[1]["description"] == "Overview of Linux kernel and shell."


def test_sync_domain_multilang_isolation(tmp_path):
    import json
    import sqlite3
    from quizkb.db import sync_domain, sync_all

    db_path = tmp_path / "quiz.db"
    output_root = tmp_path / "output"

    # Setup datasets directory
    en_dir = output_root / "datasets" / "en"
    ru_dir = output_root / "datasets" / "ru"
    en_dir.mkdir(parents=True, exist_ok=True)
    ru_dir.mkdir(parents=True, exist_ok=True)

    domains_json = [{"slug": "history", "name": "History"}]
    (en_dir / "domains.json").write_text(json.dumps(domains_json), encoding="utf-8")
    (ru_dir / "domains.json").write_text(json.dumps(domains_json), encoding="utf-8")

    q_en = [{
        "uuid": "q1-uuid",
        "text": "What year?",
        "answer": "1066",
        "distractors": [{"text": "1067", "why": "No"}],
        "hints": ["Hint"],
        "difficulty": 1
    }]
    q_ru = [{
        "uuid": "q1-uuid",
        "text": "Какой год?",
        "answer": "1066",
        "distractors": [{"text": "1067", "why": "Нет"}],
        "hints": ["Подсказка"],
        "difficulty": 1
    }]

    (en_dir / "history.json").write_text(json.dumps(q_en), encoding="utf-8")
    (ru_dir / "history.json").write_text(json.dumps(q_ru), encoding="utf-8")

    # Initial sync_all
    packs_path = tmp_path / "packs.json"
    packs_path.write_text(json.dumps({"packs": []}), encoding="utf-8")
    sync_all(db_path, output_root, packs_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM question_translations")
    assert cur.fetchone()[0] == 2
    conn.close()

    # Now re-sync RU domain only
    sync_domain(db_path, output_root, "ru", "history")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # EN questions must remain intact!
    cur.execute("SELECT text FROM question_translations WHERE lang = 'en'")
    en_rows = cur.fetchall()
    assert len(en_rows) == 1
    assert en_rows[0][0] == "What year?"

    cur.execute("SELECT text FROM question_translations WHERE lang = 'ru'")
    ru_rows = cur.fetchall()
    assert len(ru_rows) == 1
    assert ru_rows[0][0] == "Какой год?"
    conn.close()


def test_parse_questions_construct():
    raw = """
--- Q1 ---
Q: Fill in the blanks.
T: Python
T: is
T: great
DT: Java
DT: C++
H: Hint 1
H: Hint 2
H: Hint 3
"""
    parsed, diagnostics = parse_questions(raw)
    assert len(parsed) == 1
    assert len(diagnostics) == 0
    q = parsed[0]
    assert q["type"] == "construct"
    assert q["text"] == "Fill in the blanks."
    assert len(q["tokens"]) == 3
    assert q["tokens"][0]["text"] == "Python"
    assert len(q["distractorTokens"]) == 2
    assert q["distractorTokens"][0]["text"] == "Java"
    assert q["solution"] == [0, 1, 2]
    assert q["answer"] == "Python is great"
    assert "payloadJson" in q





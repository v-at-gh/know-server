from quizkb import prompts
from quizkb.parsing import parse_questions

def test_questions_prompt_format_matches_parser():
    # The prompt's format spec must use markers the parser accepts.
    # This is the guard against future prompt drift.
    assert "--- Q" in prompts.QUESTIONS_PROMPT
    assert "Q: " in prompts.QUESTIONS_PROMPT
    assert "A: " in prompts.QUESTIONS_PROMPT
    assert "D: " in prompts.QUESTIONS_PROMPT
    assert "W: " in prompts.QUESTIONS_PROMPT
    assert "H: " in prompts.QUESTIONS_PROMPT
    assert "--- QUESTION ---" not in prompts.QUESTIONS_PROMPT

def test_parse_questions_accepts_reference_sample():
    sample = (
        "--- Q1 ---\n"
        "Q: What is X?\n"
        "A: Y\n"
        "D: A1\nW: why1\n"
        "D: A2\nW: why2\n"
        "D: A3\nW: why3\n"
        "D: A4\nW: why4\n"
        "H: hint1\nH: hint2\nH: hint3\n"
    )
    parsed, diags = parse_questions(sample, expected=1)
    assert len(parsed) == 1
    assert parsed[0]["text"] == "What is X?"
    assert diags == []

def test_syllabus_prompt_formats_with_n():
    formatted = prompts.SYLLABUS_PROMPT.format(
        n=3, name="X", description="Y")
    assert "3-article" in formatted
    assert "EXACTLY 3 articles" in formatted

def test_syllabus_prompt_default_when_omitted_uses_caller_default():
    # bootstrap_domain passes n=ARTICLES_PER_DOMAIN (10)
    formatted = prompts.SYLLABUS_PROMPT.format(
        n=10, name="X", description="Y")
    assert "10-article" in formatted

def test_domain_meta_prompt_compatible_with_caller():
    # domain_meta.py passes lang, name, description
    formatted = prompts.DOMAIN_META_PROMPT.format(
        lang="Russian", name="Linux", description="OS")
    assert "NAME:" in formatted
    assert "DESCRIPTION:" in formatted

def test_construct_prompt_uses_t_dt_markers():
    assert "T: " in prompts.CONSTRUCT_QUESTIONS_PROMPT
    assert "DT: " in prompts.CONSTRUCT_QUESTIONS_PROMPT
    assert "TEXT:" not in prompts.CONSTRUCT_QUESTIONS_PROMPT

def test_single_q_prompt_uses_q_a_d_w_h_markers():
    assert "--- Q1 ---" in prompts.SINGLE_Q_PROMPT or \
           "--- Q{index} ---" in prompts.SINGLE_Q_PROMPT
    assert "Q: " in prompts.SINGLE_Q_PROMPT
    assert "A: " in prompts.SINGLE_Q_PROMPT

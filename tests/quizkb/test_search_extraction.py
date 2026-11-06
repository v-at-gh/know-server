"""Boundary unit tests for extracted search runner in quizkb.search."""

def test_run_search_empty_query(tmp_path):
    """Empty query returns empty lists, no semantic."""
    from quizkb.search import run_search
    articles, questions, semantic, elapsed, eff_lang = run_search(
        tmp_path / "content.db", "", "en", "all",
    )
    assert articles == []
    assert questions == []
    assert semantic is False
    assert elapsed == 0
    assert eff_lang == "en"


def test_run_search_drops_lang_not_in_active(tmp_path):
    """If lang not in active_langs, lang drops to None (matches prior behavior)."""
    from quizkb.search import run_search
    articles, questions, _, _, eff_lang = run_search(
        tmp_path / "content.db", "test", "xx", "all",
        active_langs=["en"],
    )
    assert articles == []
    assert eff_lang is None


def test_run_search_semantic_fallback_on_embed_error(tmp_path):
    """If embed fails, semantic_used=False, FTS-only path."""
    from quizkb.search import run_search
    class FailingEmbed:
        def embed_batch(self, texts):
            class R:
                error = "connection"
                embeddings = []
            return R()
    articles, questions, semantic, elapsed, _ = run_search(
        tmp_path / "content.db", "test", None, "all",
        ollama_client=FailingEmbed(),
        semantic_available=True,
    )
    assert semantic is False


def test_run_search_semantic_used_when_available(tmp_path):
    """If embed succeeds, semantic_used=True."""
    from quizkb.search import run_search
    class WorkingEmbed:
        def embed_batch(self, texts):
            class R:
                error = None
                embeddings = [[0.1] * 128]
                prompt_tokens = 0
            return R()
    articles, questions, semantic, _, _ = run_search(
        tmp_path / "content.db", "test", None, "all",
        ollama_client=WorkingEmbed(),
        semantic_available=True,
    )
    assert semantic is True


def test_run_search_empty_lang_string_normalized(tmp_path):
    """lang="" (from HTML form empty select) treated as None, not as filter."""
    from quizkb.search import run_search
    _, _, _, _, eff_lang = run_search(tmp_path / "content.db", "test", "", "all")
    assert eff_lang is None  # not ""


def test_run_search_empty_lang_matches_none_lang(tmp_path):
    """lang="" and lang=None produce identical effective_lang."""
    from quizkb.search import run_search
    _, _, _, _, eff_empty = run_search(tmp_path / "content.db", "test", "", "all")
    _, _, _, _, eff_none = run_search(tmp_path / "content.db", "test", None, "all")
    assert eff_empty == eff_none


def test_run_search_preserves_valid_lang(tmp_path):
    """Regression guard: non-empty lang still filters."""
    from quizkb.search import run_search
    _, _, _, _, eff_lang = run_search(
        tmp_path / "content.db", "test", "en", "all",
        active_langs=["en", "ru"],
    )
    assert eff_lang == "en"


"""Unit tests for quizkb.users module."""

import json
import pytest

from quizkb import users as kb_users


@pytest.fixture(autouse=True)
def isolated_users_db(tmp_path, monkeypatch):
    """Isolate users.db path via kb_users attribute monkeypatch."""
    db_path = tmp_path / "users.db"
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", db_path)
    kb_users.ensure_schema()
    yield


@pytest.fixture
def user_id(isolated_users_db):
    with kb_users._get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, active_domains, active_langs) "
            "VALUES ('test', 'x', '[]', '[]')"
        )
        conn.commit()
        return cur.lastrowid


def test_get_user_settings_defaults(user_id):
    s = kb_users.get_user_settings(user_id)
    assert s["generation_params"]["articles_per_domain"] == 10
    assert s["generation_params"]["questions_types"] == {"select": 5, "construct": 0}
    assert s["favorites"] == []
    assert s["profile"]["active_domains"] == []


def test_update_generation_params_global(user_id):
    kb_users.update_user_settings(
        user_id, {"generation_params": {"articles_per_domain": 15}}
    )
    s = kb_users.get_user_settings(user_id)
    assert s["generation_params"]["articles_per_domain"] == 15
    assert s["generation_params"]["questions_types"] == {"select": 5, "construct": 0}


def test_update_per_domain_override(user_id):
    kb_users.update_user_settings(
        user_id,
        {"generation_params": {"per_domain": {"linux": {"articles_per_domain": 20}}}},
    )
    resolved = kb_users.resolve_effective_generation_params(user_id, domain="linux")
    assert resolved["articles_per_domain"] == 20
    resolved_docker = kb_users.resolve_effective_generation_params(user_id, domain="docker")
    assert resolved_docker["articles_per_domain"] == 10


def test_merge_order_full_chain(user_id):
    """defaults → global → per_domain → request_override"""
    kb_users.update_user_settings(
        user_id,
        {
            "generation_params": {
                "articles_per_domain": 12,
                "per_domain": {"linux": {"articles_per_domain": 18}},
            }
        },
    )
    r = kb_users.resolve_effective_generation_params(user_id)
    assert r["articles_per_domain"] == 12
    r = kb_users.resolve_effective_generation_params(user_id, domain="linux")
    assert r["articles_per_domain"] == 18
    r = kb_users.resolve_effective_generation_params(
        user_id, domain="linux", request_override={"articles_per_domain": 25}
    )
    assert r["articles_per_domain"] == 25


def test_validate_rejects_invalid_articles(user_id):
    with pytest.raises(ValueError, match="articles_per_domain"):
        kb_users.update_user_settings(
            user_id, {"generation_params": {"articles_per_domain": 100}}
        )


def test_validate_rejects_per_lang_wrong_type(user_id):
    with pytest.raises(ValueError, match="translate_domains"):
        kb_users.update_user_settings(
            user_id,
            {"generation_params": {"per_lang": {"ru": {"translate_domains": 42}}}},
        )


def test_favorites_pruned_atomically(user_id):
    kb_users.update_user_settings(
        user_id,
        {"active_domains": ["linux", "docker"], "favorites": ["linux", "docker"]},
    )
    result = kb_users.update_user_settings(
        user_id, {"active_domains": ["linux"]},
    )
    assert set(result["favorites"]) == {"linux"}
    assert set(result["pruned_favorites"]) == {"docker"}


def test_favorites_validation_rejects_when_active_known(user_id):
    kb_users.update_user_settings(
        user_id, {"active_domains": ["linux"]},
    )
    with pytest.raises(ValueError, match="favorites not in active_domains"):
        kb_users.update_user_settings(
            user_id, {"favorites": ["docker"]},
        )


def test_get_effective_question_count(user_id):
    kb_users.update_user_settings(
        user_id, {"generation_params": {"questions_types": {"select": 3, "construct": 2}}},
    )
    assert kb_users.get_effective_question_count(user_id) == 5
    kb_users.update_user_settings(
        user_id, {"generation_params": {"per_domain": {"linux": {"questions_types": {"select": 10, "construct": 0}}}}},
    )
    assert kb_users.get_effective_question_count(user_id, domain="linux") == 10

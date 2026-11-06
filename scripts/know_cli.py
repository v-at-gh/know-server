#!/usr/bin/env python3
"""Unified CLI for Know (Orchestrator + Admin + Public API)."""
import argparse
import inspect
import json
import getpass
import subprocess
import sys
import requests
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "state" / "cli_config.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "admin_url": "http://127.0.0.1:8000",
        "orch_url": "http://127.0.0.1:8002",
        "public_api_url": "http://127.0.0.1:8001",
        "token": ""
    }

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def get_headers(cfg: dict) -> dict:
    headers = {}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    return headers

def _admin_call(method: str, path: str, cfg: dict, **kwargs) -> requests.Response:
    """Make admin call without following redirects. Detects auth redirect
    and reports it, exits gracefully. Returns raw Response for caller."""
    url = f"{cfg['admin_url']}{path}"
    call = getattr(requests, method.lower())
    call_kwargs = {
        "headers": get_headers(cfg),
        "timeout": kwargs.pop("timeout", 10),
    }
    call_kwargs.update(kwargs)
    try:
        sig = inspect.signature(call)
        if "allow_redirects" in sig.parameters or any(
            p.kind == p.VAR_KEYWORD for p in sig.parameters.values()
        ):
            call_kwargs["allow_redirects"] = False
    except (ValueError, TypeError):
        call_kwargs["allow_redirects"] = False

    r = call(url, **call_kwargs)
    if r.status_code in (301, 302, 303, 307, 308):
        location = r.headers.get("location", "") if hasattr(r, "headers") and hasattr(r.headers, "get") else ""
        if isinstance(location, str) and "/login" in location:
            print("Error: not authenticated.", file=sys.stderr)
            print("  Run 'know auth login' to obtain a token.", file=sys.stderr)
            sys.exit(1)
        print(f"Error: unexpected redirect to {location}", file=sys.stderr)
        sys.exit(1)
    return r


def _admin_json(method: str, path: str, cfg: dict, **kwargs) -> dict | list:
    """Like _admin_call but validates JSON response. Returns parsed data or
    exits with friendly error."""
    r = _admin_call(method, path, cfg, **kwargs)
    ct = r.headers.get("content-type", "") if hasattr(r, "headers") and hasattr(r.headers, "get") else ""
    if isinstance(ct, str) and "application/json" not in ct:
        print("Error: unexpected non-JSON response from admin", file=sys.stderr)
        print(f"  Status: {r.status_code}", file=sys.stderr)
        print(f"  Content-Type: {ct or 'none'}", file=sys.stderr)
        print(f"  Body: {r.text[:200]}", file=sys.stderr)
        sys.exit(1)
    try:
        return r.json()
    except Exception as e:
        print(f"Error: invalid JSON in admin response: {e}", file=sys.stderr)
        print(f"  Body: {r.text[:200]}", file=sys.stderr)
        sys.exit(1)


def _bootstrap_first_user(args) -> None:
    """Create first admin user directly in state/users.db.

    Bypasses HTTP (deadlock: HTTP requires auth, but no users exist).
    Refuses if any non-service user exists — safe from accidental overwrite.
    """
    from web import auth as web_auth
    from quizkb.config import DOMAINS, SOURCE_LANG, TARGET_LANGS

    web_auth.init_db()  # ensure schema exists

    with web_auth.get_db() as conn:
        real_users = conn.execute(
            "SELECT id, username FROM users WHERE username NOT LIKE 'sys_%'"
        ).fetchall()

    force = getattr(args, "force", False)

    if real_users and not force:
        usernames = ", ".join(u["username"] for u in real_users)
        print(
            f"Error: users table already contains {len(real_users)} "
            f"non-service user(s): {usernames}. "
            f"Bootstrap is only for the first user.",
            file=sys.stderr,
        )
        print(
            f"  To reset a password: know users reset-password --username <NAME>",
            file=sys.stderr,
        )
        print(
            f"  To wipe and re-bootstrap: add --force flag",
            file=sys.stderr,
        )
        sys.exit(1)

    if real_users and force:
        if not args.password:
            print(
                f"This will DELETE all {len(real_users)} non-service user(s) "
                f"and create a fresh admin. This cannot be undone.",
                file=sys.stderr,
            )
            ans = input("Type 'yes' to continue: ").strip().lower()
            if ans != "yes":
                print("Aborted.")
                sys.exit(1)
        with web_auth.get_db() as conn:
            ids = [u["id"] for u in real_users]
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM api_tokens WHERE user_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", ids)
            conn.commit()
        print(f"Deleted {len(real_users)} non-service user(s).", file=sys.stderr)

    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("Error: passwords do not match", file=sys.stderr)
            sys.exit(2)
    if len(password) < 8:
        print("Error: password must be at least 8 characters", file=sys.stderr)
        sys.exit(2)

    all_domains = [d["slug"] for d in DOMAINS]
    all_langs = [SOURCE_LANG] + list(TARGET_LANGS)

    ok = web_auth.create_user(args.username, password, all_domains, all_langs)
    if not ok:
        print(f"Error: failed to create user {args.username!r}", file=sys.stderr)
        sys.exit(1)

    print(f"Created first user {args.username!r} with {len(all_domains)} domains "
          f"and {len(all_langs)} languages.")
    print(f"Next: know auth login --username {args.username}")


def _reset_user_password(args) -> None:
    """Reset password for existing user directly in state/users.db.

    Bypasses HTTP (recovery path when password is lost). Revokes all API
    tokens for the user -- password change invalidates existing credentials.
    """
    from web import auth as web_auth
    web_auth.init_db()

    if args.username.startswith("sys_"):
        print(
            f"Error: cannot reset password for service account "
            f"{args.username!r}. Service account passwords are managed "
            f"by the pipeline automatically.",
            file=sys.stderr,
        )
        sys.exit(1)

    with web_auth.get_db() as conn:
        row = conn.execute(
            "SELECT id, username FROM users WHERE username = ?",
            (args.username,),
        ).fetchone()

    if not row:
        print(f"Error: user {args.username!r} not found.", file=sys.stderr)
        sys.exit(1)

    user_id = row["id"]

    password = args.password
    if not password:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("Error: passwords do not match", file=sys.stderr)
            sys.exit(2)
    if len(password) < 8:
        print("Error: password must be at least 8 characters", file=sys.stderr)
        sys.exit(2)

    if not web_auth.update_user_password(user_id, password):
        print(f"Error: failed to update password", file=sys.stderr)
        sys.exit(1)

    # Revoke all API tokens for the user
    revoked = 0
    with web_auth.get_db() as conn:
        cur = conn.execute(
            "DELETE FROM api_tokens WHERE user_id = ?", (user_id,)
        )
        revoked = cur.rowcount
        conn.commit()

    print(f"Password for {args.username!r} reset. "
          f"Revoked {revoked} API token(s).")
    print(f"Next: know auth login --username {args.username}")

def _handle_offline(service: str, url: str, e: Exception) -> None:
    print(f"\n[ERROR] {service} is not reachable at {url}.", file=sys.stderr)
    print(f"  Reason: {type(e).__name__}", file=sys.stderr)
    print("  Is it running? Start it with:", file=sys.stderr)
    if service == "Admin":
        print("    uv run --group web uvicorn web.app:app --host 0.0.0.0 --port 8000", file=sys.stderr)
    elif service == "Orchestrator":
        print("    uv run --group web uvicorn orchestrator.app:app --host 127.0.0.1 --port 8002", file=sys.stderr)
    elif service == "Public API":
        print("    uv run --group web uvicorn server.app:app --host 0.0.0.0 --port 8001", file=sys.stderr)
    print(file=sys.stderr)

def _parse_kv_list(items: list[str], *, allowed_keys: set[str], context: str) -> dict:
    """Parse repeated 'KEY.subkey=value' args into nested dict.

    Returns {key: {subkey: value}} where value is string (coerced later).
    Raises ValueError on malformed input.
    """
    result: dict[str, dict] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"{context}: missing '=' in '{item}' (expected KEY.subkey=value)")
        path, _, value = item.partition("=")
        if "." not in path:
            raise ValueError(f"{context}: missing '.' in key '{path}' (expected subkey.field)")
        outer, _, inner = path.partition(".")
        if inner not in allowed_keys:
            raise ValueError(f"{context}: unknown field '{inner}'. Allowed: {sorted(allowed_keys)}")
        result.setdefault(outer, {})[inner] = value
    return result


def _coerce_int(value: str, *, context: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{context}: expected integer, got '{value}'")


def _parse_selection_args(items: list[str], *, default_qpa: int = 5) -> dict:
    """Parse repeatable --selection domain=slug1,slug2[:qpa] arguments.

    Returns {"domain": {"articles": [...], "questions_per_article": qpa}}.
    Raises ValueError on malformed input.
    """
    selection = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid selection format {item!r}: expected domain=slug1,slug2[:qpa]")
        domain, rest = item.split("=", 1)
        domain = domain.strip()
        if not domain:
            raise ValueError(f"invalid selection {item!r}: empty domain")
        
        qpa = default_qpa
        if ":" in rest:
            slugs_part, qpa_part = rest.rsplit(":", 1)
            slugs_part = slugs_part.strip()
            try:
                qpa = int(qpa_part.strip())
            except ValueError:
                raise ValueError(f"invalid questions_per_article in {item!r}: must be integer") from None
            if qpa < 1:
                raise ValueError(f"invalid questions_per_article in {item!r}: must be >= 1")
        else:
            slugs_part = rest.strip()

        articles = [s.strip() for s in slugs_part.split(",") if s.strip()]
        if not articles:
            raise ValueError(f"invalid selection {item!r}: at least one article slug required")

        selection[domain] = {
            "articles": articles,
            "questions_per_article": qpa,
        }
    return selection


def _build_settings_patch_from_args(args) -> dict:
    """Translate CLI args to settings patch dict for PUT /api/v1/users/me/settings."""
    has_individual = any([
        args.articles is not None,
        args.select is not None,
        args.construct is not None,
        args.active_domains,
        args.active_langs,
        args.favorites,
        args.per_domain,
        args.per_lang,
    ])
    if args.json_patch and has_individual:
        raise ValueError("--json cannot be combined with individual flags")

    if args.json_patch:
        try:
            patch = json.loads(args.json_patch)
        except json.JSONDecodeError as e:
            raise ValueError(f"--json parse error: {e}")
        if not isinstance(patch, dict):
            raise ValueError("--json must be an object")
        return patch

    if not has_individual:
        raise ValueError(
            "no settings provided. Use --articles/--select/--construct/--favorites/"
            "--active-domains/--active-langs/--per-domain/--per-lang or --json"
        )

    patch: dict = {}
    gp: dict = {}

    if args.articles is not None:
        gp["articles_per_domain"] = args.articles
    if args.select is not None or args.construct is not None:
        qt: dict = {}
        if args.select is not None:
            qt["select"] = args.select
        if args.construct is not None:
            qt["construct"] = args.construct
        gp["questions_types"] = qt

    if args.per_domain:
        per_domain = _parse_kv_list(
            args.per_domain,
            allowed_keys={"articles", "select", "construct"},
            context="--per-domain",
        )
        per_domain_nested: dict = {}
        for slug, overrides in per_domain.items():
            dom_gp: dict = {}
            if "articles" in overrides:
                dom_gp["articles_per_domain"] = _coerce_int(overrides["articles"], context=f"--per-domain {slug}.articles")
            if "select" in overrides or "construct" in overrides:
                dom_qt: dict = {}
                if "select" in overrides:
                    dom_qt["select"] = _coerce_int(overrides["select"], context=f"--per-domain {slug}.select")
                if "construct" in overrides:
                    dom_qt["construct"] = _coerce_int(overrides["construct"], context=f"--per-domain {slug}.construct")
                dom_gp["questions_types"] = dom_qt
            per_domain_nested[slug] = dom_gp
        gp["per_domain"] = per_domain_nested

    if args.per_lang:
        per_lang = _parse_kv_list(
            args.per_lang,
            allowed_keys={"translate"},
            context="--per-lang",
        )
        per_lang_nested: dict = {}
        for lang, overrides in per_lang.items():
            if "translate" in overrides:
                raw = overrides["translate"]
                if raw == "*":
                    td = "*"
                elif raw == "":
                    td = []
                else:
                    td = [s.strip() for s in raw.split(",") if s.strip()]
                per_lang_nested[lang] = {"translate_domains": td}
        gp["per_lang"] = per_lang_nested

    if gp:
        patch["generation_params"] = gp
    if args.active_domains:
        patch["active_domains"] = [s.strip() for s in args.active_domains.split(",") if s.strip()]
    if args.active_langs:
        patch["active_langs"] = [s.strip() for s in args.active_langs.split(",") if s.strip()]
    if args.favorites:
        patch["favorites"] = [s.strip() for s in args.favorites.split(",") if s.strip()]

    return patch

def main():
    parser = argparse.ArgumentParser(description="Know CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # config set
    p_config = subparsers.add_parser("config", help="Configuration commands")
    p_config_sub = p_config.add_subparsers(dest="config_command", required=True)
    p_set = p_config_sub.add_parser("set", help="Set CLI configuration")
    p_set.add_argument("--admin-url", help="Admin backend URL")
    p_set.add_argument("--orch-url", help="Orchestrator URL")
    p_set.add_argument("--public-url", help="Public API URL")
    p_set.add_argument("--token", help="API token")

    # auth login
    p_auth = subparsers.add_parser("auth", help="Authentication commands")
    p_auth_sub = p_auth.add_subparsers(dest="auth_command", required=True)
    p_login = p_auth_sub.add_parser("login", help="Log in and retrieve API token", allow_abbrev=False)
    p_login.add_argument("--username", required=True, help="Username")
    p_login.add_argument("--password", help="Password (prompted if omitted)")
    p_login.add_argument("--admin-url", help="Admin backend URL")

    # status
    subparsers.add_parser("status", help="Check system status")

    # tasks list
    p_tasks = subparsers.add_parser("tasks", help="Task management")
    p_tasks_sub = p_tasks.add_subparsers(dest="tasks_command", required=True)
    p_list = p_tasks_sub.add_parser("list", help="List tasks")
    p_list.add_argument("--status", help="Filter by status (queued, running, done, error)")
    p_list.add_argument("--domain", help="Filter by domain")
    p_list.add_argument("--limit", type=int, default=20, help="Limit results")

    # task kill
    p_kill = p_tasks_sub.add_parser("kill", help="Kill a task")
    p_kill.add_argument("task_id", help="Task ID to kill")

    # domain management
    p_domain = subparsers.add_parser("domain", help="Domain management")
    p_domain_sub = p_domain.add_subparsers(dest="domain_command", required=True)

    p_domain_list = p_domain_sub.add_parser("list", help="List domains and statuses")
    p_domain_list.add_argument("--pattern", help="Slug pattern")
    p_domain_list.add_argument("--langs", help="Comma-separated languages")
    p_domain_list.add_argument("--json", action="store_true", help="Output as JSON")

    p_translate = p_domain_sub.add_parser("translate", help="Translate domains")
    p_translate.add_argument("--pattern", help="Slug pattern")
    p_translate.add_argument("--langs", required=True, help="Comma-separated target languages")
    g_trans = p_translate.add_mutually_exclusive_group()
    g_trans.add_argument("--yes", action="store_true", help="Execute without prompting")
    g_trans.add_argument("--dry-run", action="store_true", help="Show plan and exit")
    p_translate.add_argument("--json", action="store_true", help="Output as JSON")

    p_bootstrap = p_domain_sub.add_parser("bootstrap", help="Bootstrap domains")
    p_bootstrap.add_argument("--pattern", help="Slug pattern")
    g_boot = p_bootstrap.add_mutually_exclusive_group()
    g_boot.add_argument("--yes", action="store_true", help="Execute without prompting")
    g_boot.add_argument("--dry-run", action="store_true", help="Show plan and exit")
    p_bootstrap.add_argument("--json", action="store_true", help="Output as JSON")

    # users management
    p_users = subparsers.add_parser("users", help="User and settings management")
    p_users_sub = p_users.add_subparsers(dest="users_command", required=True)

    # users settings
    p_settings = p_users_sub.add_parser("settings", help="Manage user settings")
    p_settings_sub2 = p_settings.add_subparsers(dest="settings_command", required=True)

    # users settings get
    p_settings_get = p_settings_sub2.add_parser("get", help="Print current user settings as JSON")

    # users settings set
    p_settings_set = p_settings_sub2.add_parser(
        "set", help="Update settings (partial)",
        epilog=(
            "Examples:\n"
            "  know users settings set --articles 15 --select 5 --construct 0\n"
            "  know users settings set --active-domains linux,docker --favorites linux\n"
            "  know users settings set --per-domain linux.select=10 --per-domain docker.articles=20\n"
            "  know users settings set --per-lang ru.translate='*' --per-lang uk.translate='linux,docker'\n"
            "  know users settings set --json '{\"favorites\":[\"linux\"]}'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_settings_set.add_argument("--articles", type=int, help="Global articles_per_domain (1-30)")
    p_settings_set.add_argument("--select", type=int, help="Global select questions per article")
    p_settings_set.add_argument("--construct", type=int, help="Global construct questions per article")
    p_settings_set.add_argument("--active-domains", help="Comma-separated domain slugs")
    p_settings_set.add_argument("--active-langs", help="Comma-separated language codes")
    p_settings_set.add_argument("--favorites", help="Comma-separated domain slugs (subset of active)")
    p_settings_set.add_argument("--per-domain", action="append", default=[],
                                help="Per-domain override: SLUG.KEY=VALUE (e.g. linux.select=10). "
                                     "KEY ∈ {articles, select, construct}. Repeatable.")
    p_settings_set.add_argument("--per-lang", action="append", default=[],
                                help="Per-lang override: LANG.KEY=VALUE (e.g. ru.translate=*). "
                                     "KEY ∈ {translate}. VALUE = '*' or comma-separated slugs. Repeatable.")
    p_settings_set.add_argument("--json", dest="json_patch",
                                help="Full patch as JSON string (mutually exclusive with other flags)")

    # users settings import
    p_settings_import = p_settings_sub2.add_parser("import", help="Import settings from JSON file")
    p_settings_import.add_argument("path", help="Path to JSON file")

    # users settings export
    p_settings_export = p_settings_sub2.add_parser("export", help="Export current settings to JSON file")
    p_settings_export.add_argument("path", help="Output path")

    # users list
    p_users_list = p_users_sub.add_parser("list", help="List all users (admin)")
    p_users_list.add_argument("--json", action="store_true")

    # users bootstrap
    p_users_bootstrap = p_users_sub.add_parser(
        "bootstrap",
        help="Create first admin user (once, local only)",
    )
    p_users_bootstrap.add_argument("--username", required=True)
    p_users_bootstrap.add_argument("--password", help="Password (prompted if omitted)")
    p_users_bootstrap.add_argument(
        "--force", action="store_true",
        help="Delete all non-service users and create fresh admin (destructive)",
    )

    # users reset-password
    p_users_reset = p_users_sub.add_parser(
        "reset-password",
        help="Reset a user's password (local recovery, revokes API tokens)",
    )
    p_users_reset.add_argument("--username", required=True)
    p_users_reset.add_argument("--password", help="Password (prompted if omitted)")

    # users create
    p_users_create = p_users_sub.add_parser("create", help="Create user (admin)")
    p_users_create.add_argument("--username", required=True)
    p_users_create.add_argument("--password", help="Prompted if omitted")
    p_users_create.add_argument("--domains", help="Comma-separated")
    p_users_create.add_argument("--langs", help="Comma-separated")

    # users delete
    p_users_delete = p_users_sub.add_parser("delete", help="Delete user (admin)")
    p_users_delete.add_argument("username")
    p_users_delete.add_argument("--yes", action="store_true", help="Skip confirmation")

    # users password
    p_users_password = p_users_sub.add_parser("password", help="Change password")
    p_users_password.add_argument("username")
    p_users_password.add_argument("--old", help="Old password (self-change only)")
    p_users_password.add_argument("--new", help="New password (prompted if omitted)")

    # users tokens
    p_users_tokens = p_users_sub.add_parser("tokens", help="Manage own API tokens")
    p_users_tokens_sub = p_users_tokens.add_subparsers(dest="tokens_command", required=True)
    p_users_tokens_list = p_users_tokens_sub.add_parser("list", help="List own tokens")
    p_users_tokens_list.add_argument("--json", action="store_true")
    p_users_tokens_create = p_users_tokens_sub.add_parser("create", help="Create new token")
    p_users_tokens_revoke = p_users_tokens_sub.add_parser("revoke", help="Revoke token")
    p_users_tokens_revoke.add_argument("token")
    p_users_tokens_revoke.add_argument("--yes", action="store_true")

    # packs management
    p_packs = subparsers.add_parser("packs", help="Pack definitions and artifacts")
    p_packs_sub = p_packs.add_subparsers(dest="packs_command", required=True)

    # know packs list
    p_packs_list = p_packs_sub.add_parser("list", help="List pack definitions + built status")
    p_packs_list.add_argument("--json", action="store_true", help="Output as JSON")

    # know packs show
    p_packs_show = p_packs_sub.add_parser("show", help="Show pack definition + built state")
    p_packs_show.add_argument("pack_id", help="Pack id")
    p_packs_show.add_argument("--json", action="store_true")

    # know packs create
    p_packs_create = p_packs_sub.add_parser("create", help="Create new pack definition")
    p_packs_create.add_argument("--id", required=True, help="Pack id ([a-z0-9_-]+)")
    p_packs_create.add_argument("--kind", default="campaign",
                                 choices=["campaign", "domain", "custom"])
    p_packs_create.add_argument("--domains", help="Comma-separated domain slugs")
    p_packs_create.add_argument("--campaigns", help="Comma-separated campaign slugs")
    p_packs_create.add_argument("--languages", help="Comma-separated language codes")
    p_packs_create.add_argument("--selection", action="append", default=[], help="Domain article selection: domain=slug1,slug2:qpa (repeatable)")
    p_packs_create.add_argument("--from-file", help="Read full definition from JSON file")

    # know packs edit
    p_packs_edit = p_packs_sub.add_parser("edit", help="Update pack definition")
    p_packs_edit.add_argument("pack_id")
    p_packs_edit.add_argument("--kind", choices=["campaign", "domain", "custom"])
    p_packs_edit.add_argument("--domains", help="Replace domains (comma-sep; empty = clear)")
    p_packs_edit.add_argument("--campaigns", help="Replace campaigns")
    p_packs_edit.add_argument("--languages", help="Replace languages")
    p_packs_edit.add_argument("--selection", action="append", default=[], help="Replace selection: domain=slug1,slug2:qpa (repeatable)")
    p_packs_edit.add_argument("--no-selection", action="store_true", help="Clear selection entirely")
    p_packs_edit.add_argument("--from-file", help="Read patch from JSON file")

    # know packs delete
    p_packs_delete = p_packs_sub.add_parser("delete", help="Delete definition + built artifact")
    p_packs_delete.add_argument("pack_id")
    p_packs_delete.add_argument("--keep-artifact", action="store_true",
                                help="Remove definition only, keep .qap file")
    p_packs_delete.add_argument("--yes", action="store_true", help="Skip confirmation")

    # know packs build
    p_packs_build = p_packs_sub.add_parser("build", help="Build .qap artifact")
    p_packs_build.add_argument("pack_id", nargs="?", help="Pack id (omit for --all)")
    p_packs_build.add_argument("--all", action="store_true", help="Build all packs")

    # know packs install
    p_packs_install = p_packs_sub.add_parser("install",
                                             help="Install .qap into Android assets (local)")
    p_packs_install.add_argument("pack_id")
    p_packs_install.add_argument("--assets", required=True, help="Target assets directory")

    args = parser.parse_args()
    cfg = load_config()

    if args.command == "config":
        if args.config_command == "set":
            if args.admin_url: cfg["admin_url"] = args.admin_url
            if args.orch_url: cfg["orch_url"] = args.orch_url
            if args.public_url: cfg["public_url"] = args.public_url
            if args.token: cfg["token"] = args.token
            save_config(cfg)
            print("Configuration saved to", CONFIG_PATH)

    elif args.command == "auth":
        if args.auth_command == "login":
            admin_url = args.admin_url or cfg["admin_url"]
            password = args.password
            if not password:
                password = getpass.getpass("Password: ")
            else:
                print("Warning: passing --password on the CLI exposes it in shell history.")
            try:
                r = requests.post(
                    f"{admin_url}/api/v1/auth/token",
                    json={"username": args.username, "password": password},
                    timeout=5
                )
                if r.status_code == 200:
                    data = r.json()
                    token = data.get("token")
                    cfg["token"] = token
                    save_config(cfg)
                    print(f"Login successful! API token saved to {CONFIG_PATH}")
                elif r.status_code == 401:
                    print("Login failed: invalid username or password.", file=sys.stderr)
                    print(
                        f"  Fresh install? Run 'know users bootstrap --username {args.username}' first.",
                        file=sys.stderr,
                    )
                    print(
                        f"  Forgot password? Run 'know users reset-password --username {args.username}'.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                else:
                    print(f"Login failed: HTTP {r.status_code} - {r.text}", file=sys.stderr)
                    sys.exit(1)
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)

    elif args.command == "status":
        print("Checking system status...\n")

        # 1. Orchestrator
        orch_url = cfg["orch_url"]
        try:
            r = requests.get(f"{orch_url}/health", timeout=3)
            if r.status_code == 200:
                data = r.json()
                active = data.get("active_tasks", 0)
                print(f"[Orchestrator] {orch_url}: OK (active_tasks={active})")
            else:
                print(f"[Orchestrator] {orch_url}: HTTP {r.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            print(f"[Orchestrator] {orch_url}: OFFLINE (connection refused)")
            print("  -> Start with: uv run --group web uvicorn orchestrator.app:app --port 8002")

        # 2. Admin
        admin_url = cfg["admin_url"]
        try:
            r = requests.get(f"{admin_url}/login", timeout=3)
            if r.status_code == 200:
                print(f"[Admin]        {admin_url}: OK")
            else:
                print(f"[Admin]        {admin_url}: HTTP {r.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            print(f"[Admin]        {admin_url}: OFFLINE (connection refused)")
            print("  -> Start with: uv run --group web uvicorn web.app:app --port 8000")

        # 3. Public API
        public_url = cfg.get("public_url", "http://127.0.0.1:8001")
        try:
            r = requests.get(f"{public_url}/health", timeout=3)
            if r.status_code == 200:
                print(f"[Public API]   {public_url}: OK")
            else:
                print(f"[Public API]   {public_url}: HTTP {r.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            print(f"[Public API]   {public_url}: OFFLINE (connection refused)")
            print("  -> Start with: uv run --group web uvicorn server.app:app --port 8001")
        print()

    elif args.command == "tasks":
        orch_url = cfg["orch_url"]
        if args.tasks_command == "list":
            params = {"limit": args.limit}
            if args.status: params["status"] = args.status
            if args.domain: params["domain"] = args.domain
            try:
                r = requests.get(f"{orch_url}/api/v1/tasks", params=params, headers=get_headers(cfg), timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    tasks = data.get("tasks", [])
                    print(f"Total: {data.get('total', len(tasks))} tasks")
                    print(f"{'ID':<35} {'STATUS':<12} {'ACTION':<15} {'DOMAIN':<15} {'LANG'}")
                    print("-" * 90)
                    for t in tasks:
                        print(f"{t['id']:<35} {t['status']:<12} {t['action']:<15} {t['domain']:<15} {t['lang']}")
                else:
                    print(f"Error: HTTP {r.status_code} - {r.text}")
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Orchestrator", orch_url, e)

        elif args.tasks_command == "kill":
            try:
                r = requests.post(f"{orch_url}/api/v1/tasks/{args.task_id}/kill", headers=get_headers(cfg), timeout=5)
                if r.status_code == 200:
                    print(f"Task {args.task_id} kill signal sent.")
                else:
                    print(f"Error: {r.status_code} - {r.text}")
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Orchestrator", orch_url, e)

    elif args.command == "users":
        if args.users_command == "bootstrap":
            _bootstrap_first_user(args)
            sys.exit(0)
        if args.users_command == "reset-password":
            _reset_user_password(args)
            sys.exit(0)

        admin_url = cfg["admin_url"]
        headers = get_headers(cfg)

        if args.users_command == "settings":
            if args.settings_command == "get":
                try:
                    data = _admin_json("GET", "/api/v1/users/me/settings", cfg)
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)

            elif args.settings_command == "set":
                try:
                    patch = _build_settings_patch_from_args(args)
                except ValueError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(2)
                try:
                    r = _admin_call("PUT", "/api/v1/users/me/settings", cfg, json=patch)
                    if r.status_code == 200:
                        result = r.json()
                        pruned = result.get("pruned_favorites", [])
                        if pruned:
                            print(f"[WARN] Favorites auto-removed (no longer in active_domains): {', '.join(pruned)}",
                                  file=sys.stderr)
                        print(json.dumps(result, indent=2, ensure_ascii=False))
                    elif r.status_code == 400:
                        print(f"Error: validation failed - {r.json().get('detail', r.text)}", file=sys.stderr)
                        sys.exit(2)
                    elif r.status_code == 401:
                        print("Error: not authenticated. Run 'know auth login'.", file=sys.stderr)
                        sys.exit(1)
                    else:
                        print(f"Error: HTTP {r.status_code} - {r.text}", file=sys.stderr)
                        sys.exit(1)
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)

            elif args.settings_command == "import":
                path = Path(args.path)
                if not path.exists():
                    print(f"Error: file not found: {path}", file=sys.stderr)
                    sys.exit(1)
                try:
                    patch = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    print(f"Error: invalid JSON in {path}: {e}", file=sys.stderr)
                    sys.exit(2)
                if not isinstance(patch, dict):
                    print(f"Error: {path} must contain a JSON object (patch shape)", file=sys.stderr)
                    sys.exit(2)
                try:
                    r = _admin_call("PUT", "/api/v1/users/me/settings", cfg, json=patch)
                    if r.status_code == 200:
                        result = r.json()
                        pruned = result.get("pruned_favorites", [])
                        if pruned:
                            print(f"[WARN] Favorites auto-removed: {', '.join(pruned)}", file=sys.stderr)
                        print(f"Imported from {path}: 200 OK")
                        print(json.dumps(result, indent=2, ensure_ascii=False))
                    else:
                        print(f"Error: HTTP {r.status_code} - {r.text}", file=sys.stderr)
                        sys.exit(1)
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)

            elif args.settings_command == "export":
                path = Path(args.path)
                try:
                    settings = _admin_json("GET", "/api/v1/users/me/settings", cfg)
                    exportable = {
                        "active_domains": settings["profile"]["active_domains"],
                        "active_langs": settings["profile"]["active_langs"],
                        "generation_params": settings["generation_params"],
                        "favorites": settings["favorites"],
                    }
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(exportable, indent=2, ensure_ascii=False), encoding="utf-8")
                    print(f"Exported to {path}")
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)

        elif args.users_command == "list":
            try:
                data = _admin_json("GET", "/api/v1/users", cfg)
                if isinstance(data, dict) and "detail" in data and "users" not in data:
                    print(f"Error: {data['detail']}", file=sys.stderr)
                    sys.exit(1)
                if args.json:
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                else:
                    print(f"{'ID':<4} {'USERNAME':<20} {'DOMAINS':<8} {'LANGS':<6} {'TYPE'}")
                    print("-" * 60)
                    for u in data["users"]:
                        typ = "service" if u.get("service_account") else "user"
                        print(f"{u['id']:<4} {u['username']:<20} "
                              f"{len(u['active_domains']):<8} {len(u['active_langs']):<6} {typ}")
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.users_command == "create":
            password = args.password
            if not password:
                password = getpass.getpass("Password: ")
                confirm = getpass.getpass("Confirm: ")
                if password != confirm:
                    print("Error: passwords do not match", file=sys.stderr)
                    sys.exit(2)
            payload = {
                "username": args.username,
                "password": password,
                "active_domains": [s.strip() for s in (args.domains or "").split(",") if s.strip()],
                "active_langs": [s.strip() for s in (args.langs or "").split(",") if s.strip()],
            }
            try:
                r = _admin_call("POST", "/api/v1/users", cfg, json=payload)
                if r.status_code == 201:
                    print(f"Created user {args.username}")
                else:
                    detail = r.json().get("detail", r.text) if hasattr(r, "headers") and hasattr(r.headers, "get") and r.headers.get("content-type", "").startswith("application/json") else r.text
                    print(f"Error: {detail}", file=sys.stderr)
                    sys.exit(2)
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.users_command == "delete":
            if not args.yes:
                ans = input(f"Delete user {args.username!r}? [y/N]: ").strip().lower()
                if ans != "y":
                    print("Aborted.")
                    sys.exit(1)
            try:
                data = _admin_json("GET", "/api/v1/users", cfg)
                users = data.get("users", []) if isinstance(data, dict) else []
                target = next((u for u in users if u["username"] == args.username), None)
                if not target:
                    print(f"Error: user {args.username!r} not found", file=sys.stderr)
                    sys.exit(1)
                r = _admin_call("DELETE", f"/api/v1/users/{target['id']}", cfg)
                if r.status_code == 200:
                    print(f"Deleted user {args.username}")
                elif r.status_code in (400, 403):
                    print(f"Error: {r.json().get('detail', r.text)}", file=sys.stderr)
                    sys.exit(2)
                else:
                    print(f"Error: HTTP {r.status_code} - {r.text}", file=sys.stderr)
                    sys.exit(1)
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.users_command == "password":
            new_password = args.new
            if not new_password:
                new_password = getpass.getpass("New password: ")
                confirm = getpass.getpass("Confirm: ")
                if new_password != confirm:
                    print("Error: passwords do not match", file=sys.stderr)
                    sys.exit(2)
            if args.old:
                payload = {"old_password": args.old, "new_password": new_password}
                try:
                    r = _admin_call("PATCH", "/api/v1/users/me/password", cfg, json=payload)
                    if r.status_code == 200:
                        print("Password changed.")
                    else:
                        detail = r.json().get("detail", r.text) if hasattr(r, "headers") and hasattr(r.headers, "get") and r.headers.get("content-type", "").startswith("application/json") else r.text
                        print(f"Error: {detail}", file=sys.stderr)
                        sys.exit(2)
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)
            else:
                try:
                    data = _admin_json("GET", "/api/v1/users", cfg)
                    users = data.get("users", []) if isinstance(data, dict) else []
                    target = next((u for u in users if u["username"] == args.username), None)
                    if not target:
                        print(f"Error: user {args.username!r} not found", file=sys.stderr)
                        sys.exit(1)
                    r = _admin_call(
                        "PATCH",
                        f"/api/v1/users/{target['id']}/password",
                        cfg,
                        json={"new_password": new_password},
                    )
                    if r.status_code == 200:
                        print(f"Password for {args.username} changed.")
                    else:
                        detail = r.json().get("detail", r.text) if hasattr(r, "headers") and hasattr(r.headers, "get") and r.headers.get("content-type", "").startswith("application/json") else r.text
                        print(f"Error: {detail}", file=sys.stderr)
                        sys.exit(2)
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)

        elif args.users_command == "tokens":
            if args.tokens_command == "list":
                try:
                    data = _admin_json("GET", "/api/v1/users/me/tokens", cfg)
                    if args.json:
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                    else:
                        tokens = data.get("tokens", [])
                        print(f"Total: {len(tokens)} token(s)\n")
                        for t in tokens:
                            print(f"  {t['token'][:20]}...  (created {t.get('created_at', '?')})")
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)
            elif args.tokens_command == "create":
                try:
                    data = _admin_json("POST", "/api/v1/users/me/tokens", cfg)
                    if isinstance(data, dict) and "token" in data:
                        print(data["token"])
                    else:
                        print(f"Error: {data}", file=sys.stderr)
                        sys.exit(1)
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)
            elif args.tokens_command == "revoke":
                if not args.yes:
                    ans = input(f"Revoke token {args.token[:20]}...? [y/N]: ").strip().lower()
                    if ans != "y":
                        print("Aborted.")
                        sys.exit(1)
                try:
                    r = _admin_call("DELETE", f"/api/v1/users/me/tokens/{args.token}", cfg)
                    if r.status_code == 200:
                        print("Token revoked.")
                    else:
                        print(f"Error: HTTP {r.status_code} - {r.text}", file=sys.stderr)
                        sys.exit(1)
                except (requests.ConnectionError, requests.Timeout) as e:
                    _handle_offline("Admin", admin_url, e)
                    sys.exit(1)

    elif args.command == "packs":
        admin_url = cfg["admin_url"]

        if args.packs_command == "list":
            try:
                data = _admin_json("GET", "/api/v1/packs", cfg)
                if args.json:
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                else:
                    print(f"Total: {data['total']} packs\n")
                    print(f"{'ID':<25} {'KIND':<10} {'DOMAINS':<8} {'LANGS':<6} {'BUILT':<20} {'STALE'}")
                    print("-" * 90)
                    for entry in data["packs"]:
                        d = entry["definition"]
                        b = entry["built"]
                        built_str = f"{b['version']} ({b['size'] // 1024}K)" if b else "(not built)"
                        stale = "⚠" if entry["built_is_stale"] else ""
                        print(f"{d['id']:<25} {d['kind']:<10} "
                              f"{len(d['domains']):<8} {len(d['languages']):<6} "
                              f"{built_str:<20} {stale}")
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.packs_command == "show":
            try:
                r = _admin_call("GET", f"/api/v1/packs/{args.pack_id}", cfg)
                if r.status_code == 404:
                    print(f"Error: pack {args.pack_id!r} not found", file=sys.stderr)
                    sys.exit(1)
                if r.status_code != 200:
                    print(f"Error: HTTP {r.status_code} - {r.text}", file=sys.stderr)
                    sys.exit(1)
                data = r.json()

                readiness_data = None
                if data.get("definition", {}).get("selection"):
                    try:
                        rr = _admin_call("GET", f"/api/v1/packs/{args.pack_id}/readiness", cfg)
                        if rr.status_code == 200:
                            readiness_data = rr.json()
                    except Exception:
                        pass

                if args.json:
                    if readiness_data:
                        data["readiness"] = readiness_data
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                else:
                    d = data["definition"]
                    print(f"Pack: {d['id']}")
                    print(f"  kind:      {d['kind']}")
                    print(f"  domains:   {', '.join(d['domains']) or '(none)'}")
                    print(f"  campaigns: {', '.join(d['campaigns']) or '(none)'}")
                    print(f"  languages: {', '.join(d['languages']) or '(none)'}")
                    if d.get("selection"):
                        print(f"  selection: {len(d['selection'])} domain(s)")
                    if data["built"]:
                        b = data["built"]
                        print(f"  built:     {b['version']} ({b['size']} bytes)")
                        if data["built_is_stale"]:
                            print(f"  ⚠ definition changed since last build")
                    else:
                        print(f"  built:     (not built)")

                    if readiness_data:
                        ready_str = "ready ✓" if readiness_data.get("ready") else f"incomplete — {len(readiness_data.get('blockers', []))} blockers"
                        print(f"\nReadiness: {ready_str}")
                        for ds, dinfo in readiness_data.get("per_domain", {}).items():
                            print(f"  {ds}: articles {dinfo.get('articles_complete', 0)}/{dinfo.get('articles_selected', 0)} complete")
                            for lang, linfo in dinfo.get("per_lang", {}).items():
                                print(f"    {lang}: {linfo.get('articles_present', 0)}/{dinfo.get('articles_selected', 0)} present, {linfo.get('articles_complete', 0)} complete")
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.packs_command == "create":
            if args.from_file:
                if args.selection:
                    print("Error: --from-file and --selection are mutually exclusive", file=sys.stderr)
                    sys.exit(2)
                try:
                    payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    print(f"Error: failed to read {args.from_file}: {e}", file=sys.stderr)
                    sys.exit(2)
            else:
                payload = {
                    "id": args.id,
                    "kind": args.kind,
                    "domains": [s.strip() for s in (args.domains or "").split(",") if s.strip()],
                    "campaigns": [s.strip() for s in (args.campaigns or "").split(",") if s.strip()],
                    "languages": [s.strip() for s in (args.languages or "").split(",") if s.strip()],
                }
                if args.selection:
                    try:
                        payload["selection"] = _parse_selection_args(args.selection, default_qpa=5)
                    except ValueError as e:
                        print(f"Error: {e}", file=sys.stderr)
                        sys.exit(2)
            try:
                r = _admin_call("POST", "/api/v1/packs", cfg, json=payload)
                if r.status_code == 201:
                    print(f"Created pack {payload['id']}")
                else:
                    detail = r.json().get("detail", r.text) if hasattr(r, "headers") and hasattr(r.headers, "get") and r.headers.get("content-type", "").startswith("application/json") else r.text
                    print(f"Error: {detail}", file=sys.stderr)
                    sys.exit(2)
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.packs_command == "edit":
            if args.from_file:
                if args.selection or args.no_selection:
                    print("Error: --from-file and --selection/--no-selection are mutually exclusive", file=sys.stderr)
                    sys.exit(2)
                try:
                    patch = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    print(f"Error: failed to read {args.from_file}: {e}", file=sys.stderr)
                    sys.exit(2)
            else:
                patch = {}
                if args.kind: patch["kind"] = args.kind
                if args.domains is not None:
                    patch["domains"] = [s.strip() for s in args.domains.split(",") if s.strip()]
                if args.campaigns is not None:
                    patch["campaigns"] = [s.strip() for s in args.campaigns.split(",") if s.strip()]
                if args.languages is not None:
                    patch["languages"] = [s.strip() for s in args.languages.split(",") if s.strip()]
                if args.no_selection:
                    patch["selection"] = {}
                elif args.selection:
                    try:
                        patch["selection"] = _parse_selection_args(args.selection, default_qpa=5)
                    except ValueError as e:
                        print(f"Error: {e}", file=sys.stderr)
                        sys.exit(2)
                if not patch:
                    print("Error: no fields to update", file=sys.stderr)
                    sys.exit(2)
            try:
                r = _admin_call("PATCH", f"/api/v1/packs/{args.pack_id}", cfg, json=patch)
                if r.status_code == 200:
                    print(f"Updated pack {args.pack_id}")
                else:
                    detail = r.json().get("detail", r.text) if hasattr(r, "headers") and hasattr(r.headers, "get") and r.headers.get("content-type", "").startswith("application/json") else r.text
                    print(f"Error: {detail}", file=sys.stderr)
                    sys.exit(2)
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.packs_command == "delete":
            if not args.yes:
                ans = input(f"Delete pack '{args.pack_id}'"
                            + ("" if args.keep_artifact else " + built artifact")
                            + "? [y/N]: ").strip().lower()
                if ans != "y":
                    print("Aborted.")
                    sys.exit(1)
            delete_artifact = "false" if args.keep_artifact else "true"
            try:
                r = _admin_call(
                    "DELETE",
                    f"/api/v1/packs/{args.pack_id}?delete_artifact={delete_artifact}",
                    cfg,
                )
                if r.status_code == 200:
                    data = r.json()
                    print(f"Deleted pack {args.pack_id} (definition={data['definition_removed']}, artifact={data['artifact_removed']})")
                    print("Note: installed Android assets are NOT removed automatically.")
                elif r.status_code == 404:
                    print(f"Error: pack {args.pack_id!r} not found", file=sys.stderr)
                    sys.exit(1)
                else:
                    print(f"Error: HTTP {r.status_code} - {r.text}", file=sys.stderr)
                    sys.exit(1)
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)
                sys.exit(1)

        elif args.packs_command == "build":
            script = Path(__file__).parent / "make_packs.py"
            cmd = [sys.executable, str(script)]
            if args.pack_id and not args.all:
                cmd.extend(["--pack-id", args.pack_id])
            elif not args.all:
                print("Error: specify pack_id or --all", file=sys.stderr)
                sys.exit(2)
            try:
                result = subprocess.run(cmd, cwd=str(Path(__file__).parent.parent),
                                        timeout=1800)
                if result.returncode != 0:
                    print(f"Build failed (exit {result.returncode})", file=sys.stderr)
                    sys.exit(1)
                manifest_script = Path(__file__).parent / "update_manifest.py"
                subprocess.run([sys.executable, str(manifest_script)],
                               cwd=str(Path(__file__).parent.parent), timeout=120)
            except subprocess.TimeoutExpired:
                print("Error: build timeout (30 min)", file=sys.stderr)
                sys.exit(1)

        elif args.packs_command == "install":
            qap_path = Path(__file__).parent.parent / "state" / "qaps" / f"{args.pack_id}.qap"
            if not qap_path.exists():
                print(f"Error: {qap_path} not found (build first)", file=sys.stderr)
                sys.exit(1)
            script = Path(__file__).parent / "install_qap.py"
            result = subprocess.run(
                [sys.executable, str(script), str(qap_path), args.assets],
                cwd=str(Path(__file__).parent.parent),
            )
            sys.exit(result.returncode)

    elif args.command == "domain":
        admin_url = cfg["admin_url"]
        orch_url = cfg["orch_url"]

        if args.domain_command == "list":
            params = {}
            if args.pattern: params["pattern"] = args.pattern
            if args.langs: params["langs"] = args.langs
            try:
                data = _admin_json("GET", "/api/v1/domains", cfg, params=params)
                if args.json:
                    print(json.dumps(data, indent=2))
                else:
                    print(f"Total domains: {data['total']}\n")
                    for d in data["domains"]:
                        print(f"- {d['slug']} ({d['name']} [{d['kind']}])")
                        for lang, st in d["status"].items():
                            extra = ""
                            if st["state"] == "partial":
                                extra = f" ({st['articles']}/{st['expected_articles']} articles, {st['questions']}/{st['expected_questions']} questions"
                                if "missing_uuids" in st:
                                    extra += f", missing_uuids={st['missing_uuids']}"
                                extra += ")"
                            print(f"    [{lang}] state={st['state']}{extra}")
            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)

        elif args.domain_command == "translate":
            langs_list = [l.strip() for l in args.langs.split(",") if l.strip()]
            payload = {"pattern": args.pattern, "langs": langs_list}
            try:
                plan = _admin_json("POST", "/api/v1/domains/translate/plan", cfg, json=payload)

                if args.json:
                    if args.dry_run:
                        print(json.dumps(plan, indent=2))
                        return
                    re = _admin_call("POST", "/api/v1/domains/translate/enqueue", cfg, json=payload)
                    if re.status_code == 503:
                        _handle_offline("Orchestrator", orch_url, Exception("Orchestrator offline"))
                        sys.exit(2)
                    print(json.dumps(re.json(), indent=2))
                    return

                print(f"Resolving domains matching pattern: {args.pattern or '*'}")
                print(f"  Matched {plan['domains_matched']} domains. Target languages: {', '.join(langs_list)}\n")
                print("Plan:")
                for item in plan["items"]:
                    act = item["action"].upper()
                    act_padded = f"{act:<6}"
                    reason = item["reason"]
                    if item["state"] == "partial":
                        reason = f"partial ({item.get('articles', 0)}/{item.get('expected_articles', 0)} articles, {item.get('questions', 0)}/{item.get('expected_questions', 0)} questions)"
                    elif item["reason"] == "absent_source":
                        reason = "absent_source (no EN syllabus)"
                    print(f"  {act_padded} {item['slug']:<45} [{item['lang']}] {reason}")

                summary = plan["summary"]
                print(f"\nSummary: {summary.get('queue', 0)} to queue, {summary.get('skip', 0)} skipped, {summary.get('blocked', 0)} blocked. {plan['domains_matched']} domains, {len(langs_list)} languages, {len(plan['items'])} pairs.")

                if args.dry_run:
                    print("\nThis was a dry run. To execute, remove --dry-run.")
                    return

                proceed = args.yes
                if not proceed:
                    ans = input("\nApply? [y/N]: ").strip().lower()
                    if ans == "y":
                        proceed = True

                if proceed:
                    print("\nApplying...")
                    re = _admin_call("POST", "/api/v1/domains/translate/enqueue", cfg, json=payload)
                    if re.status_code == 503:
                        _handle_offline("Orchestrator", orch_url, Exception("Orchestrator offline"))
                        sys.exit(2)
                    if re.status_code == 200:
                        res_data = re.json()
                        for item in res_data["items"]:
                            if item.get("task_id"):
                                print(f"  enqueued task_id={item['task_id']} for {item['slug']} [{item['lang']}]")
                        print("\nDone.")
                    else:
                        print(f"Error executing enqueue: {re.status_code} - {re.text}")
                else:
                    sys.exit(1)

            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)

        elif args.domain_command == "bootstrap":
            payload = {"pattern": args.pattern}
            try:
                plan = _admin_json("POST", "/api/v1/domains/bootstrap/plan", cfg, json=payload)

                if args.json:
                    if args.dry_run:
                        print(json.dumps(plan, indent=2))
                        return
                    re = _admin_call("POST", "/api/v1/domains/bootstrap/enqueue", cfg, json=payload)
                    if re.status_code == 503:
                        _handle_offline("Orchestrator", orch_url, Exception("Orchestrator offline"))
                        sys.exit(2)
                    print(json.dumps(re.json(), indent=2))
                    return

                print(f"Resolving domains matching pattern: {args.pattern or '*'}")
                print(f"  Matched {plan['domains_matched']} domains.\n")
                print("Plan:")
                for item in plan["items"]:
                    act = item["action"].upper()
                    act_padded = f"{act:<6}"
                    reason = item["reason"]
                    if item["reason"] == "no_syllabus":
                        reason = "absent_source (no syllabus)"
                    print(f"  {act_padded} {item['slug']:<45} [en] {reason}")

                summary = plan["summary"]
                print(f"\nSummary: {summary.get('queue', 0)} to queue, {summary.get('skip', 0)} skipped. {plan['domains_matched']} domains.")

                if args.dry_run:
                    print("\nThis was a dry run. To execute, remove --dry-run.")
                    return

                proceed = args.yes
                if not proceed:
                    ans = input("\nApply? [y/N]: ").strip().lower()
                    if ans == "y":
                        proceed = True

                if proceed:
                    print("\nApplying...")
                    re = _admin_call("POST", "/api/v1/domains/bootstrap/enqueue", cfg, json=payload)
                    if re.status_code == 503:
                        _handle_offline("Orchestrator", orch_url, Exception("Orchestrator offline"))
                        sys.exit(2)
                    if re.status_code == 200:
                        res_data = re.json()
                        for item in res_data["items"]:
                            if item.get("task_id"):
                                print(f"  enqueued task_id={item['task_id']} for {item['slug']} [en]")
                        print("\nDone.")
                    else:
                        print(f"Error executing enqueue: {re.status_code} - {re.text}")
                else:
                    sys.exit(1)

            except (requests.ConnectionError, requests.Timeout) as e:
                _handle_offline("Admin", admin_url, e)

if __name__ == "__main__":
    main()

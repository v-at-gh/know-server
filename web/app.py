"""FastAPI app: read-only dashboard for the QuizKB content pipeline."""
from __future__ import annotations

import asyncio
import os
import json
import sqlite3
import uuid
import subprocess
import sys
import re
import logging
import httpx
import secrets
import fnmatch
from pydantic import BaseModel, field_validator

import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict

from starlette.middleware.sessions import SessionMiddleware
from fastapi import (
    Depends, FastAPI, HTTPException, Request,
    Form, Header
)
from fastapi.responses import (
    HTMLResponse, RedirectResponse, FileResponse, JSONResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from web import auth
from quizkb import users as kb_users
from quizkb.search import run_search
from web.db import get_app_db
from web.net import PingServer, ping_app
from web.logging_config import setup_logging
from quizkb.observability import (
    configure_telemetry, extract_traceparent, get_meter
)
from quizkb.settings import get_settings
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from server.db import get_server_db
from scripts.sync_config import import_from_json, export_to_json

from quizkb.config import (
    OUTPUT_ROOT, SOURCE_LANG, TARGET_LANGS, DOMAINS,
    CAMPAIGNS, LOGS_ROOT, QAPS_DIR, QAPS_INDEX, PACKS_JSON,
    LANG_NAMES,
)
from quizkb import packs as kb_packs
from quizkb.state import (
    coverage_rows, recent_errors, error_summary, error_label_languages,
    label_lang,
    domain_detail, domain_summary, article_content,
    question_pairs, questions_for_article, question_across_languages,
    find_question_locations, domain_questions, syllabus_item,
    campaign_detail, campaigns_overview,
)
from quizkb.io_utils import (
    load_json, load_json_cached, load_json_fresh, save_json_atomic
)
from quizkb.issues import (
    collect_issues, load_acks, add_ack, remove_ack,
)
from quizkb import db as kb_db
import sqlite_vec
from quizkb.client import OllamaClient, OLLAMA_EMBED_MODEL, EMBED_DIMENSIONS


setup_logging()
log = logging.getLogger("web.app")


def _match_slug(slug: str, pattern: str | None) -> bool:
    """Slug filter: case-insensitive substring by default, glob if pattern has wildcards.

    - Empty/None pattern -> always match
    - Pattern with *?[ -> fnmatch (power-user explicit glob, e.g. "doc*")
    - Otherwise -> case-insensitive substring (standard filter UX)
    """
    if not pattern:
        return True
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatch(slug, pattern)
    return pattern.lower() in slug.lower()

from opentelemetry import trace
configure_telemetry("admin")

_search_meter = get_meter("admin")
search_requests_total = _search_meter.create_counter(
    "search_requests_total",
    description="Search API requests, by result and fallback path",
)

import time as _time
_PACK_READINESS_CACHE: dict[str, tuple[float, dict]] = {}
_PACK_READINESS_TTL = 30.0

def _get_cached_readiness(pack_id: str) -> dict | None:
    entry = _PACK_READINESS_CACHE.get(pack_id)
    if entry is None:
        return None
    ts, result = entry
    if _time.time() - ts > _PACK_READINESS_TTL:
        _PACK_READINESS_CACHE.pop(pack_id, None)
        return None
    return result

def _set_cached_readiness(pack_id: str, result: dict) -> None:
    _PACK_READINESS_CACHE[pack_id] = (_time.time(), result)

def _invalidate_readiness_cache(pack_id: str | None = None) -> None:
    if pack_id is None:
        _PACK_READINESS_CACHE.clear()
    else:
        _PACK_READINESS_CACHE.pop(pack_id, None)

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
REPO_ROOT = Path(__file__).parent.parent
DB_PATH = REPO_ROOT / "state" / "content.db"

ALL_LANGS = [SOURCE_LANG] + list(TARGET_LANGS)

DOMAIN_ABBREV = {
    "networking": "net",
    "linux": "lin",
    "docker": "doc",
    "kubernetes": "k8s",
    "python": "py",
    "cicd": "ci",
    "iac": "iac",
    "observability": "obs",
    "analyst-requirements": "a-req",
    "analyst-modeling": "a-mod",
    "analyst-stakeholders": "a-stk",
    "mathematics": "math",
    "physics": "phys",
    "biology": "bio",
    "geography": "geo",
    "logic": "log",
}

_settings = get_settings()
ORCHESTRATOR_URL = _settings.orchestrator_url
_orch_token_cache: str | None = None

def get_orch_headers(request: Request | None = None) -> dict:
    global _orch_token_cache
    headers = {}
    if request and "authorization" in request.headers:
        headers["authorization"] = request.headers["authorization"]
    elif _orch_token_cache:
        headers["authorization"] = f"Bearer {_orch_token_cache}"

    tp = extract_traceparent()
    if tp:
        headers["traceparent"] = tp
    return headers

async def orch_post(
        path: str, json_data: dict | None = None,
        request: Request | None = None, timeout: float = 5.0
    ) -> httpx.Response | None:
    client = getattr(request.app.state, "http_client", None) if request else None
    try:
        if client:
            return await client.post(
                f"{ORCHESTRATOR_URL}{path}", json=json_data,
                headers=get_orch_headers(request), timeout=timeout)
        else:
            async with httpx.AsyncClient() as c:
                return await c.post(
                    f"{ORCHESTRATOR_URL}{path}", json=json_data,
                    headers=get_orch_headers(request), timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
        return None

async def orch_get(
        path: str, params: dict | None = None,
        request: Request | None = None, timeout: float = 5.0
    ) -> httpx.Response | None:
    client = getattr(request.app.state, "http_client", None) if request else None
    try:
        if client:
            return await client.get(
                f"{ORCHESTRATOR_URL}{path}", params=params,
                headers=get_orch_headers(request), timeout=timeout)
        else:
            async with httpx.AsyncClient() as c:
                return await c.get(
                    f"{ORCHESTRATOR_URL}{path}", params=params,
                    headers=get_orch_headers(request), timeout=timeout)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orch_token_cache
    provider_type = type(trace.get_tracer_provider()).__name__
    log.info(f"telemetry_provider_check: {provider_type}")
    log.info("Admin backend starting: output_root=%s", OUTPUT_ROOT.resolve())
    auth.init_db()
    with auth.get_db() as conn:
        row = conn.execute(
            "SELECT t.token FROM api_tokens t "
            "JOIN users u ON u.id = t.user_id "
            "WHERE u.username = 'sys_orchestrator' LIMIT 1"
        ).fetchone()
        if row:
            _orch_token_cache = row["token"]
        else:
            sys_pass = secrets.token_hex(16)
            auth.create_user("sys_orchestrator", sys_pass, [], [])
            user = auth.authenticate("sys_orchestrator", sys_pass)
            if user:
                _orch_token_cache = auth.create_api_token(user.id)
            else:
                urow = conn.execute(
                    "SELECT id FROM users WHERE username = 'sys_orchestrator'"
                ).fetchone()
                if urow:
                    trow = conn.execute(
                        "SELECT token FROM api_tokens WHERE user_id = ? LIMIT 1", (urow["id"],)
                    ).fetchone()
                    if trow:
                        _orch_token_cache = trow["token"]
                    else:
                        _orch_token_cache = auth.create_api_token(urow["id"])
    import_from_json()
    if not DB_PATH.exists():
        print("First run: initializing content DB...")
        kb_db.sync_all(DB_PATH, OUTPUT_ROOT, PACKS_JSON)

    app.state.semantic_available = False
    app.state.ollama_client = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        has_meta = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_meta'"
        ).fetchone()
        if not has_meta:
            conn.close()
            log.warning("semantic_search_disabled: schema_meta absent")
        else:
            row_model = conn.execute(
                "SELECT value FROM schema_meta WHERE key='embed_model'"
            ).fetchone()
            row_dim = conn.execute(
                "SELECT value FROM schema_meta WHERE key='embed_dimensions'"
            ).fetchone()
            conn.close()
            if (row_model and row_model[0] == OLLAMA_EMBED_MODEL
                    and row_dim and int(row_dim[0]) == EMBED_DIMENSIONS):
                client = OllamaClient()
                rec = client.embed_batch(["healthcheck"])
                if rec.error is None:
                    app.state.semantic_available = True
                    app.state.ollama_client = client
                    log.info("semantic_search_enabled")
                else:
                    log.warning("semantic_search_disabled: %s", rec.error)
            else:
                log.warning(
                    "semantic_search_disabled: schema_meta mismatch or absent"
                )
    except Exception as e:
        log.warning("semantic_search_disabled: %s", e)

    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_keepalive_connections=20,
            max_connections=40,
            keepalive_expiry=60.0,
        ),
        timeout=httpx.Timeout(5.0),
    )

    server = PingServer()
    await server.start()
    app.state.ping_server = server
    try:
        yield
    finally:
        await server.stop()
        await app.state.http_client.aclose()


class SameOriginMiddleware:
    """Reject state-changing requests where Origin/Referer does not match Host.

    Only applies when a session cookie is present (browser-cookie-auth requests).
    Bearer-token requests bypass (API clients don't send Origin).
    Requests with no session cookie bypass (login, public endpoints).

    Rationale: SameSite=Lax already blocks cross-site POST CSRF in modern
    browsers. This is defense-in-depth for: (a) old browsers without SameSite
    support, (b) future changes that relax SameSite, (c) mitigation if
    reverse proxy modifies Cookie headers.
    """

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in self.SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = {}
        for k, v in scope["headers"]:
            headers[k.decode("latin-1").lower()] = v.decode("latin-1")

        # Bearer = API client, no Origin check
        if headers.get("authorization", "").startswith("Bearer "):
            await self.app(scope, receive, send)
            return

        # No session cookie = no CSRF risk (nothing to forge)
        if "session=" not in headers.get("cookie", ""):
            await self.app(scope, receive, send)
            return

        # Has session cookie + state-changing method → verify origin
        origin = headers.get("origin", "")
        host = headers.get("host", "")

        if not origin:
            await self._forbidden(send, "Origin header required for state-changing requests with session auth")
            return

        origin_host = None
        for scheme in ("http://", "https://"):
            if origin.startswith(scheme):
                origin_host = origin[len(scheme):].rstrip("/")
                break

        if origin_host != host:
            await self._forbidden(send, f"Origin host {origin_host!r} does not match Host {host!r}")
            return

        await self.app(scope, receive, send)

    async def _forbidden(self, send, detail):
        import json as _json
        body = _json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


app = FastAPI(title="QuizKB Admin", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

_settings = get_settings()
_settings.validate_for_deployment()  # fails fast on prod misconfig, creates state_dir

app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret.get_secret_value(),
    same_site="lax",
    https_only=_settings.is_prod,
    max_age=14 * 24 * 60 * 60,
)
app.add_middleware(SameOriginMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def get_current_user(request: Request) -> auth.User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user = auth.get_user_by_id(user_id)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    request.state.user = user
    return user


def get_current_user_or_token(request: Request, authorization: str = Header(None)) -> auth.User:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        with auth.get_db() as conn:
            row = conn.execute("SELECT user_id FROM api_tokens WHERE token = ?", (token,)).fetchone()
            if row:
                user = auth.get_user_by_id(row["user_id"])
                if user:
                    request.state.user = user
                    return user
        raise HTTPException(status_code=401, detail="Invalid API token")
    user_id = request.session.get("user_id")
    if user_id:
        user = auth.get_user_by_id(user_id)
        if user:
            request.state.user = user
            return user
    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide Bearer token or login session.",
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/coverage", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    from opentelemetry import trace
    span = trace.get_current_span()
    log.info(f"login_span_check: {span.get_span_context().is_valid}")
    user = auth.authenticate(username, password)
    if user:
        request.session["user_id"] = user.id
        return RedirectResponse(url="/coverage", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Invalid credentials"})


class AuthTokenRequest(BaseModel):
    username: str
    password: str


@app.post("/api/v1/auth/token")
async def api_auth_token(body: AuthTokenRequest):
    user = auth.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    with auth.get_db() as conn:
        row = conn.execute("SELECT token FROM api_tokens WHERE user_id = ? LIMIT 1", (user.id,)).fetchone()
        if row:
            token = row["token"]
        else:
            token = auth.create_api_token(user.id)
    return {"token": token}


@app.get("/api/v1/users/me/generation-params")
async def get_user_generation_params(
    user: auth.User = Depends(get_current_user_or_token),
):
    """LEGACY. Returns only global articles_per_domain + questions_types."""
    from quizkb import users as kb_users
    settings = kb_users.get_user_settings(user.id)
    gp = settings["generation_params"]
    return {
        "articles_per_domain": gp["articles_per_domain"],
        "questions_types": dict(gp["questions_types"]),
    }


class GenerationParamsPatch(BaseModel):
    articles_per_domain: int | None = None
    questions_types: dict | None = None


@app.put("/api/v1/users/me/generation-params")
async def update_user_generation_params(
    body: GenerationParamsPatch,
    user: auth.User = Depends(get_current_user_or_token),
):
    """LEGACY. Partial update of global generation_params only.
    Delegates to kb_users.update_user_settings, returns same shape as
    legacy GET (no per_domain, no pruned_favorites).
    """
    from quizkb import users as kb_users

    patch_gp: dict = {}
    if body.articles_per_domain is not None:
        patch_gp["articles_per_domain"] = body.articles_per_domain
    if body.questions_types is not None:
        patch_gp["questions_types"] = body.questions_types

    try:
        result = kb_users.update_user_settings(
            user.id, {"generation_params": patch_gp} if patch_gp else {}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    gp = result["generation_params"]
    return {
        "articles_per_domain": gp["articles_per_domain"],
        "questions_types": dict(gp["questions_types"]),
    }


class UserSettingsPatch(BaseModel):
    active_domains: list[str] | None = None
    active_langs: list[str] | None = None
    generation_params: dict | None = None
    favorites: list[str] | None = None


@app.get("/api/v1/users/me/settings")
async def get_user_settings_api(
    user: auth.User = Depends(get_current_user_or_token),
):
    """Return full user settings: profile, generation_params, favorites."""
    from quizkb import users as kb_users
    settings = kb_users.get_user_settings(user.id)
    settings["profile"]["username"] = user.username
    return settings


@app.put("/api/v1/users/me/settings")
async def update_user_settings_api(
    body: UserSettingsPatch,
    user: auth.User = Depends(get_current_user_or_token),
):
    """Partial settings update."""
    from quizkb import users as kb_users

    patch: dict = {}
    if body.active_domains is not None:
        patch["active_domains"] = body.active_domains
    if body.active_langs is not None:
        patch["active_langs"] = body.active_langs
    if body.generation_params is not None:
        patch["generation_params"] = body.generation_params
    if body.favorites is not None:
        patch["favorites"] = body.favorites

    try:
        result = kb_users.update_user_settings(user.id, patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result["profile"]["username"] = user.username
    log.info(
        "settings_updated_via_api",
        extra={
            "user_id": user.id,
            "patch_keys": list(patch.keys()),
            "pruned_favorites": result.get("pruned_favorites", []),
        },
    )
    return result


def _require_admin(user: auth.User) -> None:
    if user.username != "admin":
        raise HTTPException(403, detail="admin privileges required")


def _is_service_account(username: str) -> bool:
    return username.startswith("sys_")


class UserCreateRequest(BaseModel):
    username: str
    password: str
    active_domains: list[str] = []
    active_langs: list[str] = []


class UserPasswordOverrideRequest(BaseModel):
    new_password: str


class SelfPasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class UserResponse(BaseModel):
    id: int
    username: str
    active_domains: list[str]
    active_langs: list[str]
    service_account: bool = False


@app.get("/api/v1/users")
async def api_list_users(
    user: auth.User = Depends(get_current_user_or_token),
):
    _require_admin(user)
    users = auth.list_users()
    return {
        "total": len(users),
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "active_domains": u.active_domains,
                "active_langs": u.active_langs,
                "service_account": _is_service_account(u.username),
            }
            for u in users
        ],
    }


@app.post("/api/v1/users", status_code=201)
async def api_create_user(
    body: UserCreateRequest,
    user: auth.User = Depends(get_current_user_or_token),
):
    _require_admin(user)
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    if not body.username or any(c in body.username for c in " \t\n/"):
        raise HTTPException(400, "invalid username")
    ok = auth.create_user(
        body.username, body.password,
        body.active_domains, body.active_langs,
    )
    if not ok:
        raise HTTPException(409, f"user {body.username!r} already exists")
    created = auth.get_user_by_username(body.username)
    log.info("user_created", extra={"username": body.username, "by": user.username})
    return {
        "id": created.id,
        "username": created.username,
        "active_domains": created.active_domains,
        "active_langs": created.active_langs,
    }


@app.patch("/api/v1/users/me/password")
async def api_self_change_password(
    body: SelfPasswordChangeRequest,
    user: auth.User = Depends(get_current_user_or_token),
):
    if _is_service_account(user.username):
        raise HTTPException(403, "service account password is managed automatically")
    if not auth.authenticate(user.username, body.old_password):
        raise HTTPException(401, "old password incorrect")
    try:
        ok = auth.update_user_password(user.id, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(500, "password update failed")
    log.info("password_changed_by_self", extra={"username": user.username})
    return {"changed": True}


@app.get("/api/v1/users/me/tokens")
async def api_list_my_tokens(
    user: auth.User = Depends(get_current_user_or_token),
):
    return {"tokens": auth.list_user_tokens(user.id)}


@app.post("/api/v1/users/me/tokens", status_code=201)
async def api_create_my_token(
    user: auth.User = Depends(get_current_user_or_token),
):
    token = auth.create_api_token(user.id)
    log.info("token_created", extra={"username": user.username})
    return {"token": token}


@app.delete("/api/v1/users/me/tokens/{token}")
async def api_revoke_my_token(
    token: str,
    user: auth.User = Depends(get_current_user_or_token),
):
    own_tokens = {t["token"] for t in auth.list_user_tokens(user.id)}
    if token not in own_tokens:
        raise HTTPException(404, "token not found for current user")
    removed = auth.revoke_token(token)
    if not removed:
        raise HTTPException(500, "revoke failed")
    log.info("token_revoked", extra={"username": user.username})
    return {"revoked": True}


@app.get("/api/v1/users/{user_id}")
async def api_get_user(
    user_id: int,
    user: auth.User = Depends(get_current_user_or_token),
):
    _require_admin(user)
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, f"user id {user_id} not found")
    return {
        "id": target.id,
        "username": target.username,
        "active_domains": target.active_domains,
        "active_langs": target.active_langs,
        "service_account": _is_service_account(target.username),
    }


@app.delete("/api/v1/users/{user_id}")
async def api_delete_user(
    user_id: int,
    user: auth.User = Depends(get_current_user_or_token),
):
    _require_admin(user)
    if user_id == user.id:
        raise HTTPException(400, "cannot delete self")
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, f"user id {user_id} not found")
    if _is_service_account(target.username):
        raise HTTPException(403, f"cannot delete service account {target.username!r}")
    removed = auth.delete_user(user_id)
    if not removed:
        raise HTTPException(500, "delete failed (user disappeared mid-operation)")
    log.info("user_deleted", extra={"username": target.username, "by": user.username})
    return {"deleted": True, "username": target.username}


@app.patch("/api/v1/users/{user_id}/password")
async def api_admin_override_password(
    user_id: int,
    body: UserPasswordOverrideRequest,
    user: auth.User = Depends(get_current_user_or_token),
):
    _require_admin(user)
    target = auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, f"user id {user_id} not found")
    if _is_service_account(target.username):
        raise HTTPException(403, "cannot change service account password")
    try:
        ok = auth.update_user_password(user_id, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(500, "password update failed")
    log.info("password_changed_by_admin", extra={"username": target.username, "by": user.username})
    return {"changed": True, "username": target.username}


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _parse_settings_form(form) -> dict:
    active_domains = form.getlist("active_domains") or form.getlist("domains")
    active_langs = form.getlist("active_langs") or form.getlist("langs")
    favorites = form.getlist("favorites")

    articles_str = form.get("articles_per_domain", "10")
    select_str = form.get("select_questions", "5")
    construct_str = form.get("construct_questions", "0")

    try:
        articles_per_domain = int(articles_str)
    except ValueError:
        articles_per_domain = 10

    try:
        select_q = int(select_str)
    except ValueError:
        select_q = 5

    try:
        construct_q = int(construct_str)
    except ValueError:
        construct_q = 0

    generation_params = {
        "articles_per_domain": articles_per_domain,
        "questions_types": {
            "select": select_q,
            "construct": construct_q,
        }
    }

    per_domain_slugs = form.getlist("per_domain_slug")
    if per_domain_slugs:
        per_domain = {}
        articles_list = form.getlist("per_domain_articles")
        select_list = form.getlist("per_domain_select")
        construct_list = form.getlist("per_domain_construct")
        for idx, slug in enumerate(per_domain_slugs):
            if not slug.strip():
                continue
            dom_conf = {}
            if idx < len(articles_list) and articles_list[idx].strip():
                try:
                    dom_conf["articles_per_domain"] = int(articles_list[idx])
                except ValueError:
                    pass
            q_types = {}
            if idx < len(select_list) and select_list[idx].strip():
                try:
                    q_types["select"] = int(select_list[idx])
                except ValueError:
                    pass
            if idx < len(construct_list) and construct_list[idx].strip():
                try:
                    q_types["construct"] = int(construct_list[idx])
                except ValueError:
                    pass
            if q_types:
                dom_conf["questions_types"] = q_types
            if dom_conf:
                per_domain[slug.strip()] = dom_conf
        if per_domain:
            generation_params["per_domain"] = per_domain

    per_lang_codes = form.getlist("per_lang_code")
    if per_lang_codes:
        per_lang = {}
        translate_list = form.getlist("per_lang_translate_domains")
        for idx, code in enumerate(per_lang_codes):
            if not code.strip():
                continue
            td = "*"
            if idx < len(translate_list) and translate_list[idx].strip():
                val = translate_list[idx].strip()
                if val != "*":
                    td = [s.strip() for s in val.split(",") if s.strip()]
                else:
                    td = "*"
            per_lang[code.strip()] = {"translate_domains": td}
        if per_lang:
            generation_params["per_lang"] = per_lang

    patch = {
        "active_domains": active_domains,
        "active_langs": active_langs,
        "generation_params": generation_params,
    }
    if form.get("_favorites_section") == "1":
        patch["favorites"] = form.getlist("favorites")
    return patch


@app.get("/settings/profile", response_class=HTMLResponse)
async def profile_settings(request: Request, user: auth.User = Depends(get_current_user_or_token)):
    all_domains = get_current_domains() 
    pruned_param = request.query_params.get("pruned", "")
    pruned_favorites = [x for x in pruned_param.split(",") if x] if pruned_param else []
    settings_data = kb_users.get_user_settings(user.id)

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "user": user,
            "all_domains": all_domains,
            "all_langs": [SOURCE_LANG] + list(TARGET_LANGS),
            "pruned_favorites": pruned_favorites,
            "settings": settings_data,
            "error": None,
        }
    )


@app.post("/settings/profile")
async def profile_settings_save(
    request: Request,
    user: auth.User = Depends(get_current_user_or_token)
):
    form = await request.form()
    patch = _parse_settings_form(form)
    try:
        result = kb_users.update_user_settings(user.id, patch)
    except ValueError as e:
        all_domains = get_current_domains()
        current_settings = kb_users.get_user_settings(user.id)
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={
                "user": user,
                "all_domains": all_domains,
                "all_langs": [SOURCE_LANG] + list(TARGET_LANGS),
                "pruned_favorites": [],
                "settings": {
                    "profile": {
                        "active_domains": patch["active_domains"],
                        "active_langs": patch["active_langs"],
                    },
                    "favorites": patch.get("favorites", current_settings["favorites"]),
                    "generation_params": patch["generation_params"],
                },
                "error": str(e),
            },
            status_code=400,
        )
    pruned = result.get("pruned_favorites", [])
    url = "/settings/profile"
    if pruned:
        url += "?pruned=" + ",".join(pruned)
    return RedirectResponse(url=url, status_code=303)


@app.get("/qaps/manifest.json")
async def qaps_manifest():
    """Pack index produced by qap-pipeline/scripts/update_manifest.py.

    Lives at the repo root, not inside qaps/, so it cannot be served by a
    single StaticFiles mount over qaps/.
    """
    if not QAPS_INDEX.is_file():
        return JSONResponse({"packs": []})
    return FileResponse(QAPS_INDEX, media_type="application/json")


@app.get("/qaps/{filename}")
async def qaps_file(filename: str):
    """Serve a single .qap archive. No traversal, no subdirectories."""
    if not filename.endswith(".qap") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = QAPS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"pack not found: {filename}")
    return FileResponse(path, media_type="application/zip", filename=filename)


def _user_context_processor(request: Request) -> dict:
    """Inject `user` from session into every template render.

    Handlers may still pass `user` explicitly in their context dict (that
    wins because Jinja merges handler context over processor output).
    This processor covers the 7 handlers that don't pass user: packs_builder,
    article_view, article_edit_view, question_view, question_edit_view,
    question_find, pack_view.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return {}
    user = auth.get_user_by_id(user_id)
    if not user:
        request.session.clear()
        return {}
    return {"user": user}


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.context_processors.append(_user_context_processor)

_last_ping_result: dict | None = None
_last_ping_host: str = ""
_LABEL_HEADS = re.compile(r"^(syl|art|q|campaign|dommeta|fix):")


def abbrev(slug: str) -> str:
    return DOMAIN_ABBREV.get(slug, slug[:6])


@dataclass
class LabelParts:
    kind: str
    lang: str | None = None
    domain: str | None = None
    slug: str | None = None
    uuid_prefix: str | None = None
    attempt: str | None = None
    raw: str = ""


def parse_label(label: str) -> LabelParts:
    """Разобрать label на структурные части. Никогда не падает."""
    if label.startswith("1q:"):
        body, _, attempt = label[3:].rpartition("#")
        if "/" in body:
            domain, _, slug = body.partition("/")
            return LabelParts("1q", lang=SOURCE_LANG, domain=domain,
                              slug=slug, attempt=attempt or None, raw=label)
        return LabelParts("1q", domain=body or None, attempt=attempt or None, raw=label)

    m = _LABEL_HEADS.match(label)
    if not m:
        return LabelParts("unknown", raw=label)
    kind = m.group(1)
    rest = label[len(kind) + 1:]
    head, _, tail = rest.partition(":")

    if kind == "q":
        body, _, attempt = tail.rpartition("#")
        return LabelParts("q", lang=head, uuid_prefix=body, attempt=attempt or None, raw=label)
    if kind == "art":
        if "/" in tail:
            domain, _, slug = tail.partition("/")
            return LabelParts("art", lang=head, domain=domain, slug=slug, raw=label)
        return LabelParts("art", lang=head, domain=tail or None, raw=label)
    if kind == "syl":
        return LabelParts("syl", lang=head, domain=tail or None, raw=label)
    if kind == "campaign":
        return LabelParts("campaign", lang=head, slug=tail or None, raw=label)
    if kind == "dommeta":
        return LabelParts("dommeta", lang=head, slug=tail or None, raw=label)
    if kind == "fix":
        body, _, attempt = rest.partition("#")
        return LabelParts("fix", lang=body or None, attempt=attempt or None, raw=label)
    return LabelParts("unknown", raw=label)


def label_href(parts: LabelParts) -> str | None:
    if parts.kind == "art":
        if parts.domain and parts.slug and parts.lang:
            return f"/article/{parts.lang}/{parts.domain}/{parts.slug}"
        if parts.lang and parts.domain:
            return f"/domain/{parts.lang}/{parts.domain}"
        return None

    if parts.kind == "syl":
        if parts.lang and parts.domain:
            return f"/domain/{parts.lang}/{parts.domain}"
        return None

    if parts.kind == "dommeta":
        if parts.lang and parts.slug:
            return f"/domain/{parts.lang}/{parts.slug}"
        return None

    if parts.kind == "1q" and parts.domain and parts.slug:
        return f"/article/{SOURCE_LANG}/{parts.domain}/{parts.slug}"

    if parts.kind == "campaign" and parts.slug:
        return f"/campaign/{parts.slug}"

    if parts.kind == "q" and parts.uuid_prefix:
        return f"/question/find/{parts.uuid_prefix}"

    return None


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse(url="/coverage")


def _prepare_coverage_context(
    user: auth.User,
    domains_param: str | None,
    langs_param: str | None,
    cache: dict | None = None,
) -> dict:
    if cache is None:
        cache = {}
    all_domains = get_current_domains()
    user_domains = [d for d in all_domains if d["slug"] in user.active_domains]
    user_langs = user.active_langs

    if domains_param is not None:
        if domains_param in ("", "_none_"):
            selected_domain_slugs = set()
        else:
            selected_domain_slugs = set(s.strip() for s in domains_param.split(",") if s.strip())
    else:
        selected_domain_slugs = set(d["slug"] for d in user_domains)

    if langs_param is not None:
        if langs_param in ("", "_none_"):
            selected_langs = set()
        else:
            selected_langs = set(s.strip() for s in langs_param.split(",") if s.strip())
    else:
        selected_langs = set(user_langs)

    filtered_domains = [d for d in user_domains if d["slug"] in selected_domain_slugs]
    filtered_langs = [l for l in user_langs if l in selected_langs]

    domain_groups = []
    with get_app_db() as conn:
        campaigns = [dict(r) for r in conn.execute('SELECT slug, name FROM campaigns ORDER BY "order"').fetchall()]
        assigned_slugs = set()
        user_domain_slugs = [d["slug"] for d in user_domains]
        if user_domain_slugs:
            placeholders = ",".join("?" for _ in user_domain_slugs)
            for c in campaigns:
                sql = f"""
                    SELECT d.slug, d.name FROM domains d 
                    JOIN campaign_domains cd ON d.slug = cd.domain_slug 
                    WHERE cd.campaign_slug = ? AND d.slug IN ({placeholders})
                    ORDER BY cd.position
                """
                c_domains = [dict(r) for r in conn.execute(sql, (c["slug"], *user_domain_slugs)).fetchall()]
                if c_domains:
                    domain_groups.append({"name": c["name"], "domains": c_domains})
                    assigned_slugs.update(d["slug"] for d in c_domains)

        unassigned = [d for d in user_domains if d["slug"] not in assigned_slugs]
        if unassigned:
            domain_groups.append({"name": "Other", "domains": unassigned})

    rows = coverage_rows(OUTPUT_ROOT, filtered_langs, filtered_domains, cache=cache)
    total_cells = len(filtered_langs) * len(filtered_domains)
    ready_cells = sum(
        1 for row in rows for cell in row["cells"] if cell.status == "ok"
    )

    return {
        "user": user,
        "rows": rows,
        "domains": filtered_domains,
        "all_domains": user_domains,
        "all_langs": user_langs,
        "selected_domains": selected_domain_slugs,
        "selected_langs": selected_langs,
        "domain_groups": domain_groups,
        "abbrev": abbrev,
        "total_cells": total_cells,
        "ready_cells": ready_cells,
        "output_root": str(OUTPUT_ROOT.resolve()),
    }


@app.get("/coverage", response_class=HTMLResponse)
async def coverage(
    request: Request,
    domains: str | None = None,
    langs: str | None = None,
    user: auth.User = Depends(get_current_user)
):
    cache = {}
    context = _prepare_coverage_context(user, domains, langs, cache=cache)
    return templates.TemplateResponse(
        request=request,
        name="coverage.html",
        context=context,
    )


@app.get("/api/coverage/table", response_class=HTMLResponse)
async def coverage_table_partial(
    request: Request,
    domains: str | None = None,
    langs: str | None = None,
    user: auth.User = Depends(get_current_user_or_token)
):
    cache = {}
    context = _prepare_coverage_context(user, domains, langs, cache=cache)
    return templates.TemplateResponse(
        request=request,
        name="coverage_table_partial.html",
        context=context,
    )


def get_current_domains():
    with get_app_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM domains ORDER BY slug")]


@app.get("/domains", response_class=HTMLResponse)
async def domains_index(request: Request, user: auth.User = Depends(get_current_user)):
    all_domains = get_current_domains()
    user_domains = [d for d in all_domains if d["slug"] in user.active_domains]

    domains_data = []
    for d in user_domains:
        lang_stats = {}
        for lang in user.active_langs:
            s = domain_summary(OUTPUT_ROOT, lang, d["slug"])
            if s.articles > 0 or s.questions > 0:
                lang_stats[lang] = s

        domains_data.append({
            "slug": d["slug"],
            "name": d["name"],
            "kind": d.get("kind", "unknown"),
            "description": d.get("description", ""),
            "languages": lang_stats,
        })

    return templates.TemplateResponse(
        request=request,
        name="domains.html",
        context={
            "user": user,
            "domains": domains_data,
        }
    )


def _apply_error_filters(
    records,
    *,
    reason: str | None = None,
    prefix: str | None = None,
    run: str | None = None,
    lang: str | None = None,
):
    out = records
    if reason:
        out = [r for r in out if r.reason == reason]
    if prefix:
        out = [r for r in out if r.label.startswith(prefix)]
    if run:
        out = [r for r in out if r.run_id == run]
    if lang:
        out = [r for r in out if label_lang(r.label) == lang]
    return out


def _label_now(parts, output_root: Path, uuids_for) -> str:
    """Return one of: ok | broken | absent | unknown."""
    kind = parts.kind

    if kind == "art":
        if not parts.lang or not parts.domain or not parts.slug:
            return "unknown"
        md = output_root / "knowledge" / parts.lang / parts.domain / f"{parts.slug}.md"
        if not md.exists():
            return "absent"
        return "ok" if md.stat().st_size >= 2000 else "broken"

    if kind == "1q":
        if not parts.domain or not parts.slug:
            return "unknown"
        article_key = f"{parts.domain}/{parts.slug}"
        path = output_root / "datasets" / "en" / f"{parts.domain}.json"
        count = 0
        for q in load_json(path, []) or []:
            if q.get("relatedArticleSlug") == article_key:
                count += 1
        return "ok" if count >= 5 else "broken"

    if kind == "syl":
        if not parts.lang or not parts.domain:
            return "unknown"
        p = output_root / "syllabus" / parts.lang / f"{parts.domain}.json"
        return "ok" if p.exists() else "absent"

    if kind == "dommeta":
        if not parts.lang or not parts.slug:
            return "unknown"
        meta = load_json(output_root / "datasets" / parts.lang / "domains.json", []) or []
        return "ok" if any(d.get("slug") == parts.slug for d in meta) else "absent"

    if kind == "campaign":
        if not parts.lang or not parts.slug:
            return "unknown"
        c = load_json(output_root / "datasets" / parts.lang / "campaigns.json", []) or []
        entry = next((x for x in c if x.get("slug") == parts.slug), None)
        if entry is None:
            return "absent"
        return "ok" if not entry.get("needs_translation") else "broken"

    if kind == "q":
        if not parts.lang or not parts.uuid_prefix:
            return "unknown"
        uuids = uuids_for(parts.lang)
        return "ok" if any(u.startswith(parts.uuid_prefix) for u in uuids) else "absent"

    return "unknown"


@app.get("/errors", response_class=HTMLResponse)
async def errors_view(
    request: Request,
    runs: int = 20,
    reason: str | None = None,
    prefix: str | None = None,
    run: str | None = None,
    lang: str | None = None,
    now: str | None = None,
    user: auth.User = Depends(get_current_user),
):
    all_records = recent_errors(LOGS_ROOT, limit_runs=runs)

    all_records = [
        r for r in all_records 
        if label_lang(r.label) in user.active_langs or label_lang(r.label) is None
    ]
    full_count = len(all_records)

    records = _apply_error_filters(
        all_records, reason=reason, prefix=prefix, run=run, lang=lang,
    )

    reason_facet = _apply_error_filters(
        all_records, prefix=prefix, run=run, lang=lang,
    )
    lang_facet = _apply_error_filters(
        all_records, reason=reason, prefix=prefix, run=run,
    )
    summary = error_summary(reason_facet)
    by_lang = error_label_languages(lang_facet)

    # Lazy UUID index, shared across records within this request.
    _uuid_cache: dict[str, set[str]] = {}

    def _uuids_for(lang_code: str) -> set[str]:
        if lang_code not in _uuid_cache:
            uuids: set[str] = set()
            ddir = OUTPUT_ROOT / "datasets" / lang_code
            if ddir.is_dir():
                for path in ddir.glob("*.json"):
                    if path.name in ("domains.json", "campaigns.json"):
                        continue
                    for q in load_json(path, []) or []:
                        u = q.get("uuid", "")
                        if u:
                            uuids.add(u)
            _uuid_cache[lang_code] = uuids
        return _uuid_cache[lang_code]

    records_view = []
    for r in records:
        parts = parse_label(r.label)
        records_view.append({
            "ts": r.ts,
            "run_id": r.run_id,
            "run_short": r.run_id[:12],
            "cell": r.cell,
            "label": r.label,
            "reason": r.reason,
            "detail": r.detail,
            "prompt": r.prompt,
            "raw_response": r.raw_response,
            "has_prompt": r.has_prompt,
            "has_response": r.has_response,
            "prompt_len": r.prompt_len,
            "link": label_href(parts),
            "now": _label_now(parts, OUTPUT_ROOT, _uuids_for),
        })

    if now:
        records_view = [r for r in records_view if r["now"] == now]

    filtered_count = len(records_view)

    return templates.TemplateResponse(
        request=request,
        name="errors.html",
        context={
            "user": user,
            "records": records_view,
            "summary": summary,
            "by_lang": by_lang,
            "full_count": full_count,
            "filtered_count": filtered_count,
            "runs": runs,
            "reason_filter": reason,
            "prefix_filter": prefix,
            "run_filter": run,
            "lang_filter": lang,
            "now_filter": now,
            "logs_root": str(LOGS_ROOT),
        },
    )


_ERROR_MESSAGES = {
    "empty_name": "Name is required.",
    "name_too_long": "Name must be 100 characters or fewer.",
    "desc_too_long": "Description must be 500 characters or fewer.",
    "unknown_domain": "Domain does not exist in syllabus.",
    "orchestrator_offline": "Orchestrator is offline or unreachable. Failed to enqueue translation tasks.",
}

_NAME_MAX = 100
_DESC_MAX = 500


@app.get("/domain/{lang}/{slug}", response_class=HTMLResponse)
async def domain_view(
    request: Request,
    lang: str,
    slug: str,
    edit: str | None = None,
    saved: str | None = None,
    error: str | None = None,
    user: auth.User = Depends(get_current_user),
):
    cache = {}
    detail = domain_detail(OUTPUT_ROOT, lang, slug, cache=cache)
    if detail is None:
        from web.db import get_app_db
        with get_app_db() as conn:
            row = conn.execute("SELECT name FROM domains WHERE slug = ?", (slug,)).fetchone()
            
        if row:
            return templates.TemplateResponse(
                request=request,
                name="domain_empty.html",
                context={
                    "user": user,
                    "slug": slug,
                    "lang": lang,
                    "domain_name": row["name"]
                }
            )
        else:
            raise HTTPException(status_code=404, detail=f"Domain not found: {slug}")

    all_errors = recent_errors(LOGS_ROOT, limit_runs=50)
    domain_errors = [
        e for e in all_errors
        if f":{slug}/" in e.label or e.label.endswith(f":{slug}")
    ]

    return templates.TemplateResponse(
        request=request,
        name="domain.html",
        context={
            "user": user,
            "detail": detail,
            "errors": domain_errors[:100],
            "all_langs": ALL_LANGS,
            "current_lang": lang,
            "editing": edit == "1",
            "saved": saved == "1",
            "error_message": _ERROR_MESSAGES.get(error) if error else None,
        },
    )


@app.post("/domain/{lang}/{slug}/meta")
async def domain_meta_update(
    lang: str,
    slug: str,
    name: str = Form(...),
    description: str = Form(""),
    user: auth.User = Depends(get_current_user),
):
    name = name.strip()
    description = description.strip()

    back = f"/domain/{lang}/{slug}"

    if not name:
        return RedirectResponse(f"{back}?edit=1&error=empty_name", status_code=303)
    if len(name) > _NAME_MAX:
        return RedirectResponse(f"{back}?edit=1&error=name_too_long", status_code=303)
    if len(description) > _DESC_MAX:
        return RedirectResponse(f"{back}?edit=1&error=desc_too_long", status_code=303)

    # Defensive: domain must exist in syllabus.
    syllabus = load_json(
        OUTPUT_ROOT / "syllabus" / SOURCE_LANG / f"{slug}.json", []
    ) or []
    if not syllabus:
        return RedirectResponse(f"{back}?error=unknown_domain", status_code=303)

    path = OUTPUT_ROOT / "datasets" / lang / "domains.json"
    entries = load_json_fresh(path, []) or []

    found = False
    for entry in entries:
        if entry.get("slug") == slug:
            entry["name"] = name
            entry["description"] = description
            found = True
            break

    if not found:
        entries.append({"slug": slug, "name": name, "description": description})
        entries.sort(key=lambda d: d.get("slug", ""))

    save_json_atomic(path, entries)

    return RedirectResponse(f"{back}?saved=1", status_code=303)


@app.get("/domain/{lang}/{slug}/translate/plan", response_class=HTMLResponse)
async def domain_translate_plan(
    request: Request,
    lang: str,
    slug: str,
    langs: str = "",
    user: auth.User = Depends(get_current_user_or_token),
):
    target_langs = [l.strip() for l in langs.split(",") if l.strip()] or [lang]
    items = _compute_translate_plan(pattern=slug, langs=target_langs, user_id=user.id, scope="active")
    items = [i for i in items if i["slug"] == slug]
    queue_count = sum(1 for i in items if i["action"] == "queue")
    return templates.TemplateResponse(
        request=request,
        name="domain_translate_plan.html",
        context={
            "user": user,
            "domain": slug,
            "lang": lang,
            "target_langs": target_langs,
            "items": items,
            "queue_count": queue_count,
            "skip_count": sum(1 for i in items if i["action"] == "skip"),
            "enqueued": request.query_params.get("enqueued") == "1",
            "error_message": _ERROR_MESSAGES.get(request.query_params.get("error")) if request.query_params.get("error") else None,
        },
    )


@app.post("/domain/{lang}/{slug}/translate/enqueue")
async def domain_translate_enqueue(
    request: Request,
    lang: str,
    slug: str,
    langs: str = Form(...),
    user: auth.User = Depends(get_current_user_or_token),
):
    target_langs = [l.strip() for l in langs.split(",") if l.strip()]
    items = _compute_translate_plan(pattern=slug, langs=target_langs, user_id=user.id, scope="active")
    items = [i for i in items if i["slug"] == slug]
    for item in items:
        if item["action"] != "queue":
            continue
        resp = await orch_post("/api/v1/tasks", {
            "action": "translate",
            "domain": slug,
            "lang": item["lang"],
            "username": user.username,
            "generation_params": None,
        }, request)
        if not resp or resp.status_code != 201:
            return RedirectResponse(
                url=f"/domain/{lang}/{slug}/translate/plan?langs={langs}&error=orchestrator_offline",
                status_code=303,
            )
    return RedirectResponse(
        url=f"/domain/{lang}/{slug}/translate/plan?langs={langs}&enqueued=1",
        status_code=303,
    )


def _get_issues_context(
    user: auth.User,
    severity: str | None = None,
    check: str | None = None,
    lang: str | None = None,
    domain: str | None = None,
    show: str = "active",
    page: int = 1,
    per_page: int = 50,
) -> dict:
    all_issues = collect_issues(OUTPUT_ROOT, user.active_langs)
    acks = load_acks()

    active = [i for i in all_issues if i.key not in acks]
    acked = [i for i in all_issues if i.key in acks]

    def apply(items):
        out = items
        if severity:
            out = [i for i in out if i.severity == severity]
        if check:
            out = [i for i in out if i.check == check]
        if lang:
            out = [i for i in out if i.target.get("lang") == lang]
        if domain:
            out = [i for i in out if i.target.get("domain") == domain]
        return out

    if show == "acked":
        visible_all = apply(acked)
    elif show == "all":
        visible_all = apply(active + acked)
    else:
        visible_all = apply(active)

    total_items = len(visible_all)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, total_items)
    visible = visible_all[start_idx:end_idx]

    sev_facet: dict[str, int] = {}
    check_facet: dict[str, int] = {}
    lang_facet: dict[str, int] = {}
    domain_facet: dict[str, int] = {}
    for i in active:
        sev_facet[i.severity] = sev_facet.get(i.severity, 0) + 1
        check_facet[i.check] = check_facet.get(i.check, 0) + 1
        l = i.target.get("lang")
        if l:
            lang_facet[l] = lang_facet.get(l, 0) + 1
        d = i.target.get("domain")
        if d:
            domain_facet[d] = domain_facet.get(d, 0) + 1

    live_keys = {i.key for i in all_issues}
    orphaned = [k for k in acks.keys() if k not in live_keys]

    return {
        "user": user,
        "visible": visible,
        "total_items": total_items,
        "start_item": start_idx + 1 if total_items > 0 else 0,
        "end_item": end_idx,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "active_count": len(active),
        "acked_count": len(acked),
        "sev_facet": sorted(sev_facet.items(), key=lambda kv: (-kv[1], kv[0])),
        "check_facet": sorted(check_facet.items(), key=lambda kv: (-kv[1], kv[0])),
        "lang_facet": sorted(lang_facet.items()),
        "domain_facet": sorted(domain_facet.items()),
        "orphaned": orphaned,
        "acks": acks,
        "severity_filter": severity,
        "check_filter": check,
        "lang_filter": lang,
        "domain_filter": domain,
        "show": show,
    }


@app.get("/issues", response_class=HTMLResponse)
async def issues_view(
    request: Request,
    severity: str | None = None,
    check: str | None = None,
    lang: str | None = None,
    domain: str | None = None,
    show: str = "active",
    page: int = 1,
    user: auth.User = Depends(get_current_user),
):
    context = _get_issues_context(
        user, severity=severity, check=check, lang=lang, domain=domain, show=show, page=page
    )
    return templates.TemplateResponse(
        request=request,
        name="issues.html",
        context=context,
    )


@app.get("/api/issues/table", response_class=HTMLResponse)
async def issues_table_partial(
    request: Request,
    severity: str | None = None,
    check: str | None = None,
    lang: str | None = None,
    domain: str | None = None,
    show: str = "active",
    page: int = 1,
    user: auth.User = Depends(get_current_user),
):
    context = _get_issues_context(
        user, severity=severity, check=check, lang=lang, domain=domain, show=show, page=page
    )
    return templates.TemplateResponse(
        request=request,
        name="issues_table_partial.html",
        context=context,
    )


@app.post("/issues/ack")
async def issues_ack(request: Request, key: str = Form(...), note: str = Form(""), user: auth.User = Depends(get_current_user)):
    add_ack(key, note)
    return RedirectResponse(url="/issues", status_code=303)


@app.post("/issues/unack")
async def issues_unack(request: Request, key: str = Form(...), user: auth.User = Depends(get_current_user)):
    remove_ack(key)
    return RedirectResponse(url="/issues?show=acked", status_code=303)


@app.get("/article/{lang}/{domain}/{slug}", response_class=HTMLResponse)
async def article_view(
    request: Request,
    lang: str,
    domain: str,
    slug: str,
    compare: str | None = None,
    user: auth.User = Depends(get_current_user),
):
    if compare is None:
        compare = SOURCE_LANG if lang != SOURCE_LANG else None
    elif compare == "":
        compare = None
    if compare == lang:
        compare = None

    current = article_content(OUTPUT_ROOT, lang, domain, slug)
    other = (
        article_content(OUTPUT_ROOT, compare, domain, slug)
        if compare else None
    )

    if other is not None:
        pairs = question_pairs(OUTPUT_ROOT, compare, lang, domain, slug)
        questions = None
        mismatch_count = sum(
            1 for p in pairs
            if p.distractor_delta != 0 or p.hint_delta != 0
            or not p.source.present or not p.target.present
        )
    else:
        pairs = None
        questions = questions_for_article(OUTPUT_ROOT, lang, domain, slug)
        mismatch_count = 0

    domains_meta = load_json(OUTPUT_ROOT / "datasets" / lang / "domains.json", []) or []
    domain_display = next(
        (d["name"] for d in domains_meta if d.get("slug") == domain),
        domain,
    )

    return templates.TemplateResponse(
        request=request,
        name="article.html",
        context={
            "current": current,
            "other": other,
            "domain_display": domain_display,
            "pairs": pairs,
            "questions": questions,
            "mismatch_count": mismatch_count,
            "all_langs": ALL_LANGS,
            "lang": lang,
            "compare_lang": compare,
        },
    )


@app.get("/article/{lang}/{domain}/{slug}/edit", response_class=HTMLResponse)
async def article_edit_view(
    request: Request,
    lang: str,
    domain: str,
    slug: str,
    user: auth.User = Depends(get_current_user)
):
    current = article_content(OUTPUT_ROOT, lang, domain, slug)
    domains_meta = load_json(OUTPUT_ROOT / "datasets" / lang / "domains.json", []) or []
    domain_display = next(
        (d["name"] for d in domains_meta if d.get("slug") == domain),
        domain,
    )
    return templates.TemplateResponse(
        request=request,
        name="article_edit.html",
        context={
            "current": current,
            "lang": lang,
            "domain": domain,
            "slug": slug,
            "domain_display": domain_display,
            "user": user,
        },
    )


@app.post("/article/{lang}/{domain}/{slug}/edit")
async def article_edit_save(
    lang: str, domain: str, slug: str,
    content: str = Form(...),
    user: auth.User = Depends(get_current_user)
):
    md_path = OUTPUT_ROOT / "knowledge" / lang / domain / f"{slug}.md"
    tmp = md_path.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(md_path)

    kb_db.sync_all(DB_PATH, OUTPUT_ROOT, PACKS_JSON, reembed=False)

    if getattr(app.state, "semantic_available", False) and app.state.ollama_client:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT a.uuid FROM articles a
            JOIN domains d ON d.id = a.domain_id
            WHERE d.slug = ? AND a.slug = ?
        """, (domain, slug))
        row = cur.fetchone()
        conn.close()
        if row:
            kb_db.reindex_article(DB_PATH, row[0], lang, app.state.ollama_client)

    return RedirectResponse(url=f"/article/{lang}/{domain}/{slug}", status_code=303)


@app.get("/question/{uuid}", response_class=HTMLResponse)
async def question_view(request: Request, uuid: str, user: auth.User = Depends(get_current_user)):
    detail = question_across_languages(OUTPUT_ROOT, uuid)
    if not any(l.present for l in detail.languages):
        raise HTTPException(status_code=404, detail=f"question not found: {uuid}")

    en = next(
        (l for l in detail.languages if l.lang == SOURCE_LANG and l.present),
        None,
    )
    mismatches: dict[str, list[str]] = {}
    if en is not None:
        for l in detail.languages:
            if l.lang == SOURCE_LANG or not l.present:
                continue
            ms: list[str] = []
            if len(l.distractors) != len(en.distractors):
                ms.append(f"D {len(l.distractors)}≠{len(en.distractors)}")
            if len(l.hints) != len(en.hints):
                ms.append(f"H {len(l.hints)}≠{len(en.hints)}")
            if l.answer.strip() == en.answer.strip():
                ms.append("answer not translated")
            mismatches[l.lang] = ms

    return templates.TemplateResponse(
        request=request,
        name="question.html",
        context={
            "detail": detail,
            "mismatches": mismatches,
        },
    )


@app.get("/question/{uuid}/edit", response_class=HTMLResponse)
async def question_edit_view(
    request: Request,
    uuid: str,
    lang: str = SOURCE_LANG,
    domain: str | None = None,
    user: auth.User = Depends(get_current_user)
):
    if not domain:
        locations = find_question_locations(OUTPUT_ROOT, uuid)
        loc = next((l for l in locations if l[0] == lang), None) or (locations[0] if locations else None)
        if not loc:
            loc = locations[0] if locations else None
        if not loc:
            raise HTTPException(404, "Question not found")
        _, domain, _ = loc

    en_path = OUTPUT_ROOT / "datasets" / SOURCE_LANG / f"{domain}.json"
    en_qs = load_json(en_path, [])
    en_q = next((x for x in en_qs if x.get("uuid") == uuid), None)

    target_q = None
    is_draft = False
    if lang != SOURCE_LANG:
        target_path = OUTPUT_ROOT / "datasets" / lang / f"{domain}.json"
        target_qs = load_json(target_path, [])
        target_q = next((x for x in target_qs if x.get("uuid") == uuid), None)
        if target_q is None:
            target_q = en_q
            is_draft = True
    else:
        en_q = en_q or next((x for x in en_qs if x.get("uuid") == uuid), None)

    if not en_q:
        locations = find_question_locations(OUTPUT_ROOT, uuid)
        if locations:
            _, domain, en_q = locations[0]
            en_path = OUTPUT_ROOT / "datasets" / SOURCE_LANG / f"{domain}.json"
            en_qs = load_json(en_path, [])
            en_q = next((x for x in en_qs if x.get("uuid") == uuid), en_q)

    if not en_q:
        raise HTTPException(404, "Question not found in source language")

    return templates.TemplateResponse(
        request=request,
        name="question_edit.html",
        context={
            "en_q": en_q,
            "q": target_q,
            "lang": lang,
            "domain": domain,
            "is_draft": is_draft,
            "user": user
        }
    )


@app.post("/question/{uuid}/translate")
async def question_translate_task(
    request: Request,
    uuid: str,
    lang: str = Form(...),
    domain: str = Form(...),
    user: auth.User = Depends(get_current_user)
):
    await orch_post(
        "/api/v1/tasks",
        {
            "action": "translate_single_q",
            "domain": domain,
            "lang": lang,
            "username": user.username,
            "target_uuid": uuid
        },
        request
    )
    return RedirectResponse(url=f"/question/{uuid}", status_code=303)


@app.post("/question/{uuid}/edit")
async def question_edit_save(
    request: Request,
    uuid: str,
    lang: str = SOURCE_LANG,
    domain: str | None = None,
    text: str = Form(...),
    type: str = Form("select"),
    answer: str = Form(""),
    answerWhy: str = Form(""),
    distractor_text: list[str] = Form([]),
    distractor_why: list[str] = Form([]),
    hints_text: str = Form(""),
    tokens_text: str = Form(""),
    distractor_tokens_text: str = Form(""),
    solution: str = Form(""),
    difficulty: int = Form(1),
    relatedArticleSlug: str = Form(""),
    user: auth.User = Depends(get_current_user)
):
    if not domain:
        locations = find_question_locations(OUTPUT_ROOT, uuid)
        loc = next((l for l in locations if l[0] == lang), None) or (locations[0] if locations else None)
        if not loc:
            loc = locations[0] if locations else None
        if not loc:
            raise HTTPException(404, "Question not found")
        lang, domain, _ = loc

    json_path = OUTPUT_ROOT / "datasets" / lang / f"{domain}.json"
    questions = load_json_fresh(json_path, [])

    idx = next((i for i, x in enumerate(questions) if x.get("uuid") == uuid), -1)
    if idx < 0:
        q = {"uuid": uuid}
        questions.append(q)
        idx = len(questions) - 1

    q = questions[idx]

    q["text"] = text.strip()
    q["difficulty"] = difficulty
    q["relatedArticleSlug"] = relatedArticleSlug.strip() or None
    if answerWhy.strip():
        q["answerWhy"] = answerWhy.strip()
    else:
        q.pop("answerWhy", None)

    hints = [h.strip() for h in hints_text.split("\n") if h.strip()]
    q["hints"] = hints

    if type == "select":
        q["type"] = "select"
        q["answer"] = answer.strip()
        q["distractors"] = [
            {"text": t.strip(), "why": w.strip()}
            for t, w in zip(distractor_text, distractor_why)
            if t.strip()
        ]
        for key in ("tokens", "solution", "distractorTokens"):
            q.pop(key, None)
    else:  # construct
        q["type"] = "construct"
        tokens = [{"text": t.strip()} for t in tokens_text.split("\n") if t.strip()]
        distractor_tokens = [{"text": t.strip()} for t in distractor_tokens_text.split("\n") if t.strip()]
        sol = [int(x.strip()) for x in solution.split(",") if x.strip()]
        
        q["tokens"] = tokens
        q["solution"] = sol
        q["distractorTokens"] = distractor_tokens
        q["answer"] = " ".join(tokens[i]["text"] for i in sol if 0 <= i < len(tokens))
        q["distractors"] = []

    from quizkb.parsing import DISTRACTOR_WHY_FORBIDDEN
    errors = []
    if type == "select":
        if not q.get("answer"):
            errors.append("Answer cannot be empty")
        if any(d["text"] == q["answer"] for d in q["distractors"]):
            errors.append("Answer appears in distractors")
        if len(q["distractors"]) < 2:
            errors.append("Need at least 2 distractors")
        for d in q["distractors"]:
            if d.get("why") and DISTRACTOR_WHY_FORBIDDEN.search(d["why"]):
                errors.append(f"Distractor why references article: {d['why'][:40]}")
    else:
        if not q.get("tokens"):
            errors.append("Construct needs tokens")
        if not q.get("solution"):
            errors.append("Construct needs solution indices")

    if errors:
        en_path = OUTPUT_ROOT / "datasets" / SOURCE_LANG / f"{domain}.json"
        en_qs = load_json(en_path, [])
        en_q = next((x for x in en_qs if x.get("uuid") == uuid), q)
        return templates.TemplateResponse(
            request=request,
            name="question_edit.html",
            context={"en_q": en_q, "q": q, "lang": lang, "domain": domain, "errors": errors, "user": user},
            status_code=400
        )

    questions[idx] = q
    save_json_atomic(json_path, questions)

    kb_db.sync_domain(DB_PATH, OUTPUT_ROOT, lang, domain, reembed=False)
    if getattr(app.state, "semantic_available", False) and app.state.ollama_client:
        kb_db.reindex_question(DB_PATH, uuid, lang, app.state.ollama_client)

    return RedirectResponse(url=f"/question/{uuid}", status_code=303)


@app.post("/question/{uuid}/archive")
async def question_archive(
    request: Request,
    uuid: str,
    lang: str = Form(...),
    domain: str = Form(...),
    user: auth.User = Depends(get_current_user)
):
    json_path = OUTPUT_ROOT / "datasets" / lang / f"{domain}.json"
    questions = load_json(json_path, [])
    for q in questions:
        if q.get("uuid") == uuid:
            q["archived"] = True
            break
    save_json_atomic(json_path, questions)

    kb_db.sync_domain(DB_PATH, OUTPUT_ROOT, lang, domain, reembed=False)
    if getattr(app.state, "semantic_available", False) and app.state.ollama_client:
        kb_db.reindex_question(DB_PATH, uuid, lang, app.state.ollama_client)

    referer = request.headers.get("referer")
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url=f"/question/{uuid}", status_code=303)


@app.post("/question/{uuid}/unarchive")
async def question_unarchive(
    request: Request,
    uuid: str,
    lang: str = Form(...),
    domain: str = Form(...),
    user: auth.User = Depends(get_current_user)
):
    json_path = OUTPUT_ROOT / "datasets" / lang / f"{domain}.json"
    questions = load_json_fresh(json_path, [])
    for q in questions:
        if q.get("uuid") == uuid:
            q["archived"] = False
            break
    save_json_atomic(json_path, questions)

    kb_db.sync_domain(DB_PATH, OUTPUT_ROOT, lang, domain, reembed=False)
    if getattr(app.state, "semantic_available", False) and app.state.ollama_client:
        kb_db.reindex_question(DB_PATH, uuid, lang, app.state.ollama_client)

    referer = request.headers.get("referer")
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url=f"/question/{uuid}", status_code=303)


@app.get("/question/find/{prefix}")
async def question_find(request: Request, prefix: str, user: auth.User = Depends(get_current_user)):
    # uuid -> {"domain": str, "languages": set[str]}
    found: dict[str, dict] = {}
    for lang in ALL_LANGS:
        ddir = OUTPUT_ROOT / "datasets" / lang
        if not ddir.is_dir():
            continue
        for path in ddir.glob("*.json"):
            if path.name in ("domains.json", "campaigns.json"):
                continue
            for q in load_json(path, []) or []:
                uuid = q.get("uuid", "")
                if uuid.startswith(prefix):
                    entry = found.setdefault(
                        uuid, {"domain": path.stem, "languages": set()}
                    )
                    entry["languages"].add(lang)

    if len(found) == 1:
        return RedirectResponse(f"/question/{next(iter(found))}", status_code=303)

    matches = [
        {
            "uuid": uuid,
            "domain": info["domain"],
            "languages": sorted(info["languages"]),
        }
        for uuid, info in found.items()
    ]
    matches.sort(key=lambda m: m["uuid"])

    return templates.TemplateResponse(
        request=request, name="question_find.html",
        context={"prefix": prefix, "matches": matches},
    )


@app.get("/campaigns", response_class=HTMLResponse)
async def campaigns_index(request: Request, user: auth.User = Depends(get_current_user)):
    cache = {}
    overview = campaigns_overview(OUTPUT_ROOT, user.active_langs, cache=cache)
    return templates.TemplateResponse(
        request=request,
        name="campaigns.html",
        context={
            "user": user,
            "campaigns": overview,
            "all_langs": user.active_langs,
        },
    )


@app.get("/campaign/{slug}", response_class=HTMLResponse)
async def campaign_view(request: Request, slug: str, user: auth.User = Depends(get_current_user)):
    cache = {}
    detail = campaign_detail(OUTPUT_ROOT, slug, user.active_langs, cache=cache)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown campaign: {slug}")

    by_key = {(c.lang, c.domain): c for c in detail.cells}
    matrix = [
        [by_key[(lang, ds)] for lang in detail.langs]
        for ds in detail.domain_slugs
    ]

    return templates.TemplateResponse(
        request=request,
        name="campaign.html",
        context={
            "user": user,
            "detail": detail,
            "matrix": matrix,
            "all_langs": user.active_langs,
            "ready_langs": detail.ready_langs(),
            "partial_langs": detail.partial_langs(),
        },
    )


# --- domain mgmt ---

@app.get("/config/domains", response_class=HTMLResponse)
async def config_domains_index(request: Request, user: auth.User = Depends(get_current_user)):
    with get_app_db() as conn:
        domains = [dict(r) for r in conn.execute("SELECT * FROM domains ORDER BY slug")]
    return templates.TemplateResponse(
        request=request, name="config_domains.html",
        context={"user": user, "domains": domains}
    )

@app.post("/config/domains/create")
async def config_domains_create(
    slug: str = Form(...), name: str = Form(...), 
    description: str = Form(""), kind: str = Form("unknown"),
    user: auth.User = Depends(get_current_user)
):
    slug = slug.strip().lower()
    with get_app_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES (?, ?, ?, ?)",
            (slug, name.strip(), description.strip(), kind.strip())
        )
        conn.commit()

    if slug not in user.active_domains:
        new_domains = user.active_domains + [slug]
        auth.update_user_settings(user.id, new_domains, user.active_langs)

    export_to_json() # Синхронизируем в JSON
    return RedirectResponse(url="/config/domains", status_code=303)

@app.post("/config/domains/import_text")
async def config_domains_import_text(
    request: Request,
    text: str = Form(...),
    user: auth.User = Depends(get_current_user)
):
    from quizkb.parsing import parse_domain_list
    from quizkb.io_utils import ensure_dir, save_json, new_uuid

    parsed_domains = parse_domain_list(text)
    tm = request.app.state.task_manager
    enqueued_tasks = []

    # 1. Save to app.db and output/ (inside transaction)
    with get_app_db() as conn:
        for d in parsed_domains:
            slug = d["slug"]
            name = d["title"]
            description = d["description"]
            kind = "unknown"

            conn.execute(
                "INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES (?, ?, ?, ?)",
                (slug, name, description, kind)
            )

            if slug not in user.active_domains:
                user.active_domains.append(slug)
                auth.update_user_settings(user.id, user.active_domains, user.active_langs)

            syl_path = OUTPUT_ROOT / "syllabus" / SOURCE_LANG / f"{slug}.json"
            ensure_dir(syl_path.parent)
            syl_item = {
                "order": d["order"],
                "slug": slug,
                "title": name,
                "summary": description,
                "uuid": new_uuid()
            }
            save_json(syl_path, [syl_item])
            enqueued_tasks.append(slug)
        conn.commit()

    export_to_json()

    # 2. Enqueue tasks OUTSIDE the transaction, with explicit logging
    tp = extract_traceparent()
    gp = _get_user_generation_payload(user)
    for slug in enqueued_tasks:
        await orch_post(
            "/api/v1/tasks",
            {
                "action": "bootstrap",
                "domain": slug,
                "lang": SOURCE_LANG,
                "username": user.username,
                "generation_params": gp
            },
            request
        )
        log.info("Enqueued bootstrap for %s -> %s", slug, SOURCE_LANG)

    return RedirectResponse(url="/config/domains", status_code=303)

# --- campaign mgmt ---

@app.get("/config/campaigns", response_class=HTMLResponse)
async def config_campaigns_index(request: Request, user: auth.User = Depends(get_current_user)):
    with get_app_db() as conn:
        domains = [dict(r) for r in conn.execute("SELECT slug, name FROM domains ORDER BY slug")]

        campaigns = []
        for row in conn.execute("SELECT * FROM campaigns ORDER BY \"order\", slug"):
            c = dict(row)
            c["prerequisiteDomainSlugs"] = json.loads(c.pop("prerequisite_domain_slugs"))
            c["domainSlugs"] = [r["domain_slug"] for r in conn.execute(
                "SELECT domain_slug FROM campaign_domains WHERE campaign_slug = ? ORDER BY position", (c["slug"],)
            )]
            campaigns.append(c)

    return templates.TemplateResponse(
        request=request, name="config_campaigns.html",
        context={"user": user, "campaigns": campaigns, "domains": domains}
    )

@app.post("/config/campaigns/create")
async def config_campaigns_create(
    slug: str = Form(...), name: str = Form(...), 
    description: str = Form(""), order: int = Form(0),
    domain_slugs: list[str] = Form([]),
    user: auth.User = Depends(get_current_user)
):
    slug = slug.strip().lower()
    with get_app_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO campaigns (slug, name, description, \"order\", prerequisite_domain_slugs) VALUES (?, ?, ?, ?, '[]')",
            (slug, name.strip(), description.strip(), order)
        )
        conn.execute("DELETE FROM campaign_domains WHERE campaign_slug = ?", (slug,))
        for pos, ds in enumerate(domain_slugs):
            conn.execute(
                "INSERT INTO campaign_domains (campaign_slug, domain_slug, position) VALUES (?, ?, ?)",
                (slug, ds, pos)
            )
        conn.commit()
    export_to_json()
    return RedirectResponse(url="/config/campaigns", status_code=303)


def _slug(x) -> str:
    """Extract slug string from heterogeneous domain/campaign representations.

    Handles:
      - plain str (spec side)
      - dataclass with .slug attr (PackEntry side, e.g. DomainInPack)
      - dict with 'slug' key (post-JSON serialization)
    """
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return str(x.get("slug", x))
    return str(getattr(x, "slug", x))


def _slugs(v) -> list[str]:
    """Normalize heterogeneous slug list to sorted list[str]."""
    if not v:
        return []
    return sorted(_slug(item) for item in v)


def _spec_differs_from_built(spec_pack: dict, built_entry) -> bool:
    """True if definition fields diverged from embedded manifest.

    Compares domains/languages/campaigns after normalizing both sides to
    sorted slug lists. Ignores version/size/builtAt (those legitimately
    differ). Best-effort; false negatives OK.
    """
    for key in ("domains", "languages", "campaigns"):
        if isinstance(built_entry, dict):
            built_val = built_entry.get(key, [])
        else:
            built_val = getattr(built_entry, key, [])
        if _slugs(spec_pack.get(key, [])) != _slugs(built_val):
            return True
    return False


def _built_to_dict(built_entry: kb_packs.PackEntry | dict | None) -> dict | None:
    """Serialize PackEntry (or already-dict) to JSON-friendly dict.

    domains/campaigns normalized to slug string lists for consistency with
    spec definition shape. Per-domain details preserved in `domains_detail`
    for consumers that need article/question counts.
    """
    if built_entry is None:
        return None
    if isinstance(built_entry, dict):
        return built_entry
    domains_detail = []
    for d in (built_entry.domains or []):
        if isinstance(d, dict):
            domains_detail.append(d)
        else:
            domains_detail.append({
                "slug": str(getattr(d, "slug", d)),
                "articles": getattr(d, "articles", None),
                "questions": getattr(d, "questions", None),
            })
    return {
        "file": built_entry.file,
        "size": built_entry.size,
        "sha256": built_entry.sha256,
        "id": built_entry.id,
        "version": built_entry.version,
        "kind": built_entry.kind,
        "createdAt": built_entry.createdAt,
        "languages": list(built_entry.languages or []),
        "campaigns": _slugs(built_entry.campaigns or []),
        "domains": _slugs(built_entry.domains or []),
        "domains_detail": domains_detail,
        "totals": built_entry.totals,
        "error": built_entry.error,
    }


@app.get("/packs", response_class=HTMLResponse)
async def packs_index(request: Request, user: auth.User = Depends(get_current_user)):
    spec = kb_packs.load_spec()
    built_by_id = {e.id: e for e in kb_packs.load_index()}

    packs_display = []
    for pack_def in spec["packs"]:
        built = built_by_id.get(pack_def["id"])
        sel = pack_def.get("selection", {})
        packs_display.append({
            "definition": pack_def,
            "built": built,
            "built_is_stale": (
                built is not None
                and _spec_differs_from_built(pack_def, built)
            ),
            "selection_domain_count": len(sel),
            "selection_article_count": sum(len(v.get("articles", [])) for v in sel.values()),
        })
    packs_display.sort(key=lambda p: p["definition"]["id"])
    return templates.TemplateResponse(
        request=request,
        name="packs.html",
        context={
            "user": user,
            "packs_display": packs_display,
            "built_only": [e for k, e in built_by_id.items()
                           if k not in {p["id"] for p in spec["packs"]}],
        },
    )


@app.get("/packs/builder", response_class=HTMLResponse)
async def packs_builder(request: Request, edit: str | None = None, user: auth.User = Depends(get_current_user)):
    spec = kb_packs.load_spec()
    pack_to_edit = None
    if edit:
        pack_to_edit = next((p for p in spec["packs"] if p["id"] == edit), None)

    if not pack_to_edit:
        pack_to_edit = {
            "id": "",
            "kind": "campaign",
            "campaigns": [],
            "domains": [],
            "languages": [],
        }

    all_langs = []
    dataset_dir = OUTPUT_ROOT / "datasets"
    if dataset_dir.is_dir():
        all_langs = sorted([d.name for d in dataset_dir.iterdir() if d.is_dir()])

    stats = {}
    for d in DOMAINS:
        stats[d["slug"]] = {}
        for l in all_langs:
            s = domain_summary(OUTPUT_ROOT, l, d["slug"])
            stats[d["slug"]][l] = {"q": s.questions, "a": s.articles}

    domains_by_kind = defaultdict(list)
    for d in DOMAINS:
        domains_by_kind[d.get("kind", "unknown")].append(d)

    kind_order = [
        "programming-languages", "natural-languages", 
        "tool", "practice", "method", "science", "reasoning"
    ]
    sorted_kinds = sorted(
        domains_by_kind.keys(),
        key=lambda k: (kind_order.index(k) if k in kind_order else 99, k)
    )

    return templates.TemplateResponse(
        request=request,
        name="builder.html",
        context={
            "pack": pack_to_edit,
            "campaigns": CAMPAIGNS,
            "domains_by_kind": domains_by_kind,
            "sorted_kinds": sorted_kinds,
            "all_langs": all_langs,
            "lang_names": LANG_NAMES,
            "stats": stats,
            "is_edit": bool(edit),
        }
    )


@app.post("/packs/builder")
async def packs_builder_save(
    request: Request,
    id: str = Form(...),
    kind: str = Form(...),
    campaigns: list[str] = Form([]),
    domains: list[str] = Form([]),
    languages: list[str] = Form([]),
    user: auth.User = Depends(get_current_user),
):
    id = id.strip()
    if not id:
        return RedirectResponse(url="/packs/builder?error=empty_id", status_code=303)

    payload = {
        "id": id,
        "kind": kind,
        "campaigns": campaigns,
        "domains": domains,
        "languages": languages,
    }

    existing = kb_packs.find_spec_pack(id)
    try:
        if existing is None:
            kb_packs.create_pack(payload)
        else:
            kb_packs.update_pack(id, payload)
    except ValueError as e:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/packs/builder?error={quote(str(e))}"
            + (f"&edit={quote(id)}" if existing else ""),
            status_code=303,
        )

    log.info("pack_saved", extra={"pack_id": id, "is_new": existing is None, "user_id": user.id})
    _invalidate_readiness_cache(id)
    return RedirectResponse(url="/packs", status_code=303)


@app.post("/packs/{pack_id}/delete")
async def pack_delete(
    request: Request,
    pack_id: str,
    user: auth.User = Depends(get_current_user),
):
    definition_removed = kb_packs.delete_pack(pack_id)
    artifact_removed = kb_packs.delete_pack_artifact(pack_id)
    _invalidate_readiness_cache(pack_id)

    if not definition_removed and not artifact_removed:
        return JSONResponse(
            {"status": "error", "message": f"pack {pack_id} not found"},
            status_code=404,
        )

    manifest_script = REPO_ROOT / "scripts" / "update_manifest.py"
    if manifest_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(manifest_script)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            log.warning("manifest_refresh_failed_after_delete", extra={"error": str(e)})

    log.info(
        "pack_deleted",
        extra={"pack_id": pack_id, "definition": definition_removed, "artifact": artifact_removed, "user_id": user.id},
    )
    return RedirectResponse(url="/packs", status_code=303)


@app.get("/pack/{pack_id}", response_class=HTMLResponse)
async def pack_view(request: Request, pack_id: str):
    entry = kb_packs.find_pack(pack_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown pack: {pack_id}")
    if not entry.path.exists():
        raise HTTPException(status_code=404, detail=f"pack file missing on disk: {entry.file}")
    detail = kb_packs.load_detail(entry)
    if detail is None:
        raise HTTPException(status_code=500, detail=f"failed to read {entry.file}")

    spec_pack = kb_packs.find_spec_pack(pack_id)
    readiness = None
    if spec_pack and spec_pack.get("selection"):
        readiness = _get_cached_readiness(pack_id)
        if readiness is None:
            from quizkb.packs import compute_pack_readiness
            readiness = compute_pack_readiness(spec_pack, OUTPUT_ROOT)
            _set_cached_readiness(pack_id, readiness)

    return templates.TemplateResponse(
        request=request,
        name="pack.html",
        context={
            "entry": entry,
            "detail": detail,
            "all_langs": ALL_LANGS,
            "readiness": readiness,
        },
    )


@app.post("/packs/build")
async def packs_build(request: Request, pack_id: str = Form(...), user: auth.User = Depends(get_current_user)):
    script = REPO_ROOT / "scripts" / "make_packs.py"
    manifest_script = REPO_ROOT / "scripts" / "update_manifest.py"

    if not script.exists():
        return JSONResponse({"status": "error", "message": f"Missing {script}"})
    if not manifest_script.exists():
        return JSONResponse({"status": "error", "message": f"Missing {manifest_script}"})

    try:
        # 1. Build the pack
        result = subprocess.run(
            [sys.executable, str(script), "--pack-id", pack_id],
            cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=1800
        )
        output = result.stdout + "\n" + result.stderr

        if result.returncode != 0:
            return JSONResponse({"status": "error", "message": output})

        # 2. Update manifest
        res2 = subprocess.run(
            [sys.executable, str(manifest_script)],
            cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=120
        )
        if res2.returncode != 0:
            output += "\n\nManifest update failed:\n" + res2.stdout + "\n" + res2.stderr
            return JSONResponse({"status": "error", "message": output})

        output += "\n\n" + res2.stdout
        return JSONResponse({"status": "ok", "message": output})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@app.get("/network", response_class=HTMLResponse)
async def network_view(request: Request, host: str | None = None, user: auth.User = Depends(get_current_user)):
    server: PingServer = request.app.state.ping_server
    pings = list(server.recent_pings())
    pings.reverse()
    return templates.TemplateResponse(
        request=request,
        name="network.html",
        context={
            "user": user,
            "host": host or _last_ping_host,
            "ping_result": _last_ping_result,
            "pings": pings,
            "server_running": server.is_running,
            "server_port": server.port,
            "pack_count": len(kb_packs.load_index()),
        },
    )


@app.post("/network/ping")
async def network_ping_post(request: Request, host: str = Form(...), user: auth.User = Depends(get_current_user)):
    global _last_ping_result, _last_ping_host
    host = host.strip()
    if not host or " " in host or "/" in host:
        _last_ping_result = {"status": "error", "reason": "invalid host"}
        return RedirectResponse(url=f"/network?host={host}", status_code=303)
    _last_ping_host = host
    result = await ping_app(host)
    _last_ping_result = result
    return RedirectResponse(url=f"/network?host={host}", status_code=303)


@app.post("/sync")
async def manual_sync(request: Request, user: auth.User = Depends(get_current_user)):
    kb_db.sync_all(DB_PATH, OUTPUT_ROOT, PACKS_JSON)
    return RedirectResponse(url="/coverage", status_code=303)


@app.get("/search", response_class=HTMLResponse)
async def search_view(
    request: Request, 
    q: str | None = None, 
    lang: str | None = None, 
    type: str = "all",
    user: auth.User = Depends(get_current_user),
):
    articles = []
    questions = []
    semantic_used = False
    display_lang = lang

    if q:
        result_label = "error"
        try:
            articles, questions, semantic_used, _, display_lang = run_search(
                DB_PATH, q, lang, type,
                active_langs=user.active_langs,
                ollama_client=getattr(request.app.state, "ollama_client", None),
                semantic_available=getattr(request.app.state, "semantic_available", False),
            )
            result_label = "ok"
        finally:
            search_requests_total.add(
                1,
                {
                    "result": result_label,
                    "fallback": "none" if semantic_used else "fts",
                },
            )

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "user": user,
            "q": q or "",
            "lang": display_lang,
            "type": type,
            "articles": articles,
            "questions": questions,
            "semantic_used": semantic_used,
            "all_langs": user.active_langs,
        }
    )


# --- Domains & Campaigns API (Read & Plan/Enqueue) ---


def _get_user_generation_payload(user) -> dict | None:
    """DEPRECATED. Always returns None.

    Historically returned the user's merged global generation params, which
    were passed to orchestrator as `body.generation_params`. That caused
    per_domain/per_lang overrides to be masked (web payload landed at
    request_override tier in orchestrator's 5-tier merge).

    Orchestrator now resolves effective params from the verified user_id
    directly (Cycle C.1). Keep this helper as a no-op shim for the 4 call
    sites in web/app.py; remove entirely in a future cleanup.
    """
    return None


def evaluate_domain_status(
    slug: str,
    lang: str,
    questions_types: dict | None = None,
    cache: dict | None = None,
) -> dict:
    from quizkb.state import evaluate_domain_status as state_eval_v2
    return state_eval_v2(
        OUTPUT_ROOT, slug, lang,
        questions_types=questions_types,
        cache=cache,
    )


@app.get("/api/v1/domains")
async def api_get_domains(
    request: Request,
    pattern: str | None = None,
    langs: str | None = None,
    only_active: bool = False,
    user: auth.User = Depends(get_current_user_or_token)
):
    target_langs = [l.strip() for l in langs.split(",")] if langs else ["en"]
    cache = {}
    with get_app_db() as conn:
        rows = conn.execute("SELECT slug, name, kind FROM domains ORDER BY slug").fetchall()

    matched_slugs = [r["slug"] for r in rows if _match_slug(r["slug"], pattern)]
    matched_set = set(matched_slugs)
    qt_by_domain = kb_users.get_effective_questions_types_batch(user.id, matched_slugs) if user else {}

    domains_out = []
    active_set = set(user.active_domains) if user and user.active_domains else set()
    for r in rows:
        slug = r["slug"]
        if only_active and active_set and slug not in active_set:
            continue
        if slug not in matched_set:
            continue

        qt = qt_by_domain.get(slug)
        status_dict = {}
        for l in target_langs:
            status_dict[l] = evaluate_domain_status(slug, l, questions_types=qt, cache=cache)

        domains_out.append({
            "slug": slug,
            "name": r["name"],
            "kind": r["kind"],
            "status": status_dict
        })

    return {"total": len(domains_out), "domains": domains_out}


@app.get("/api/v1/domains/{slug}")
async def api_get_domain_detail(
    slug: str,
    langs: str | None = None,
    user: auth.User = Depends(get_current_user_or_token),
):
    lang_list = [l.strip() for l in langs.split(",")] if langs else ["en"]
    qt = kb_users.get_effective_questions_types_batch(user.id, [slug]).get(slug) if user else None
    cache = {}

    name = slug
    description = ""
    kind = "pack"
    found = False

    with get_app_db() as conn:
        row = conn.execute("SELECT slug, name, description, kind FROM domains WHERE slug = ?", (slug,)).fetchone()
        if row:
            found = True
            name = row["name"]
            description = row["description"]
            kind = row["kind"]

        c_rows = conn.execute("""
            SELECT cd.campaign_slug FROM campaign_domains cd
            JOIN campaigns c ON c.slug = cd.campaign_slug
            WHERE cd.domain_slug = ?
            ORDER BY c."order", c.slug
        """, (slug,)).fetchall()
        campaigns_list = [cr["campaign_slug"] for cr in c_rows]

    if not found:
        det = domain_detail(OUTPUT_ROOT, "en", slug, cache=cache)
        if det is None:
            raise HTTPException(status_code=404, detail=f"Domain not found: {slug}")
        name = det.display_name
        description = det.display_description
        kind = "unknown"

    status_dict = {}
    for l in lang_list:
        status_dict[l] = evaluate_domain_status(slug, l, questions_types=qt, cache=cache)

    return {
        "slug": slug,
        "name": name,
        "description": description,
        "kind": kind,
        "campaigns": campaigns_list,
        "status": status_dict,
    }


@app.get("/api/v1/domains/{slug}/articles")
async def api_get_domain_articles(
    slug: str,
    lang: str = "en",
    status: str | None = None,
    user: auth.User = Depends(get_current_user_or_token),
):
    cache = {}
    detail = domain_detail(OUTPUT_ROOT, lang, slug, cache=cache)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Domain not found: {slug}")

    articles_out = []
    for a in detail.articles:
        state = "missing" if a.missing else ("short" if a.short else "ready")
        if status and state != status:
            continue
        articles_out.append({
            "slug": a.slug,
            "title": a.title,
            "bytes": a.md_bytes,
            "questions_count": a.questions,
            "order": a.order,
            "state": state,
        })

    return {
        "domain": slug,
        "lang": lang,
        "total": len(articles_out),
        "articles": articles_out,
    }


@app.get("/api/v1/domains/{slug}/questions")
async def api_get_domain_questions(
    slug: str,
    lang: str = "en",
    article_slug: str | None = None,
    status: str | None = None,
    user: auth.User = Depends(get_current_user_or_token),
):
    cache = {}
    detail = domain_detail(OUTPUT_ROOT, SOURCE_LANG, slug, cache=cache)
    if detail is None:
        with get_app_db() as conn:
            row = conn.execute("SELECT slug FROM domains WHERE slug = ?", (slug,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Domain not found: {slug}")

    q_entries = domain_questions(OUTPUT_ROOT, lang, slug, cache=cache)

    questions_out = []
    for q in q_entries:
        if article_slug:
            if q.related_article_slug != article_slug and not q.related_article_slug.endswith(f"/{article_slug}"):
                continue
        if status and q.state != status:
            continue
        questions_out.append({
            "uuid": q.uuid,
            "text": q.text,
            "type": q.type,
            "difficulty": q.difficulty,
            "archived": q.archived,
            "state": q.state,
            "related_article_slug": q.related_article_slug,
        })

    return {
        "domain": slug,
        "lang": lang,
        "total": len(questions_out),
        "questions": questions_out,
    }


@app.get("/api/v1/articles/{lang}/{domain}/{slug}")
async def api_get_article_detail(
    lang: str,
    domain: str,
    slug: str,
    user: auth.User = Depends(get_current_user_or_token),
):
    cache = {}
    content = article_content(OUTPUT_ROOT, lang, domain, slug, cache=cache)
    if not content.exists and not syllabus_item(OUTPUT_ROOT, lang, domain, slug, cache=cache):
        raise HTTPException(status_code=404, detail=f"Article not found: {domain}/{slug}")

    qs = questions_for_article(OUTPUT_ROOT, lang, domain, slug, cache=cache)
    questions_out = [
        {
            "uuid": q.uuid,
            "text": q.text,
            "answer": q.answer,
            "distractors": q.distractors,
            "hints": q.hints,
            "difficulty": q.difficulty,
            "archived": q.archived,
        }
        for q in qs
    ]

    return {
        "lang": lang,
        "domain": domain,
        "slug": slug,
        "title": content.title,
        "summary": content.summary,
        "md_bytes": content.md_bytes,
        "text": content.text,
        "exists": content.exists,
        "short": content.short,
        "questions": questions_out,
    }


@app.get("/api/v1/questions/{uuid}")
async def api_get_question_detail(
    uuid: str,
    user: auth.User = Depends(get_current_user_or_token),
):
    cache = {}
    detail = question_across_languages(OUTPUT_ROOT, uuid, cache=cache)
    if not any(l.present for l in detail.languages):
        raise HTTPException(status_code=404, detail=f"Question not found: {uuid}")

    langs_out = [
        {
            "lang": l.lang,
            "present": l.present,
            "text": l.text,
            "answer": l.answer,
            "distractors": l.distractors,
            "hints": l.hints,
            "difficulty": l.difficulty,
        }
        for l in detail.languages
    ]

    return {
        "uuid": detail.uuid,
        "domain_slug": detail.domain_slug,
        "article_slug": detail.article_slug,
        "archived": detail.archived,
        "languages": langs_out,
    }


@app.get("/api/v1/search")
async def api_search(
    q: str = "",
    lang: str | None = None,
    type: str = "all",
    user: auth.User = Depends(get_current_user_or_token),
):
    q_clean = q.strip()
    if not q_clean:
        return {
            "query": q,
            "lang": lang,
            "semantic_available": False,
            "elapsed_ms": 0,
            "articles": [],
            "questions": [],
        }

    result_label = "error"
    semantic_used = False
    try:
        articles, questions, semantic_used, elapsed, response_lang = run_search(
            DB_PATH, q_clean, lang, type,
            active_langs=user.active_langs,
            ollama_client=getattr(app.state, "ollama_client", None),
            semantic_available=getattr(app.state, "semantic_available", False),
        )
        result_label = "ok"
    finally:
        search_requests_total.add(
            1,
            {
                "result": result_label,
                "fallback": "none" if semantic_used else "fts",
            },
        )

    return {
        "query": q,
        "lang": response_lang,
        "semantic_available": semantic_used,
        "elapsed_ms": elapsed,
        "articles": articles,
        "questions": questions,
    }


def evaluate_campaign_status(slug: str, lang: str, cache: dict | None = None) -> dict:
    dataset_en_path = OUTPUT_ROOT / "datasets" / "en" / "campaigns.json"
    if not dataset_en_path.exists():
        return {"state": "absent_source"}

    dataset_path = OUTPUT_ROOT / "datasets" / lang / "campaigns.json"
    campaigns = load_json_cached(dataset_path, [], cache=cache) or []
    c_entry = next((c for c in campaigns if c["slug"] == slug), None)

    if lang == "en":
        return {"state": "bootstrapped"}

    if not c_entry:
        return {"state": "missing"}

    if c_entry.get("needs_translation", True):
        return {"state": "partial"}
    else:
        return {"state": "bootstrapped"}


@app.get("/api/v1/campaigns")
async def api_get_campaigns(
    request: Request,
    pattern: str | None = None,
    langs: str | None = None,
    user: auth.User = Depends(get_current_user_or_token)
):
    target_langs = [l.strip() for l in langs.split(",")] if langs else ["en"]
    cache = {}
    with get_app_db() as conn:
        rows = conn.execute("SELECT slug, name, description FROM campaigns ORDER BY \"order\", slug").fetchall()

    campaigns_out = []
    for r in rows:
        slug = r["slug"]
        if not _match_slug(slug, pattern):
            continue

        status_dict = {}
        for l in target_langs:
            status_dict[l] = evaluate_campaign_status(slug, l, cache=cache)

        campaigns_out.append({
            "slug": slug,
            "name": r["name"],
            "description": r["description"],
            "status": status_dict
        })

    return {"total": len(campaigns_out), "campaigns": campaigns_out}


@app.get("/api/v1/campaigns/{slug}")
async def api_get_campaign_detail(
    slug: str,
    langs: str | None = None,
    user: auth.User = Depends(get_current_user_or_token),
):
    lang_list = [l.strip() for l in langs.split(",")] if langs else ["en"]
    cache = {}
    detail = campaign_detail(OUTPUT_ROOT, slug, lang_list, cache=cache)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Campaign not found: {slug}")

    cells_out = [
        {
            "lang": c.lang,
            "domain": c.domain,
            "status": c.status,
            "articles": c.articles,
            "expected_articles": c.expected_articles,
            "questions": c.questions,
            "short_articles": c.short_articles,
        }
        for c in detail.cells
    ]

    return {
        "slug": detail.slug,
        "name": detail.name,
        "description": detail.description,
        "order": detail.order,
        "domain_slugs": detail.domain_slugs,
        "prerequisite_domain_slugs": detail.prerequisite_domain_slugs,
        "prerequisite_campaign_slugs": detail.prerequisite_campaign_slugs,
        "langs": detail.langs,
        "cells": cells_out,
    }


class PackPatch(BaseModel):
    kind: str | None = None
    domains: list[str] | None = None
    languages: list[str] | None = None
    campaigns: list[str] | None = None


@app.get("/api/v1/packs", dependencies=[Depends(get_current_user_or_token)])
async def api_list_packs():
    spec = kb_packs.load_spec()
    built_index = kb_packs.load_index()
    built_by_id = {e.id: e for e in built_index} if isinstance(built_index, list) else {}
    return {
        "schema_version": spec["schema_version"],
        "total": len(spec["packs"]),
        "packs": [
            {
                "definition": p,
                "built": _built_to_dict(built_by_id.get(p["id"])),
                "built_is_stale": (
                    built_by_id.get(p["id"]) is not None
                    and _spec_differs_from_built(p, built_by_id[p["id"]])
                ),
            }
            for p in spec["packs"]
        ],
    }


@app.get("/api/v1/packs/{pack_id}", dependencies=[Depends(get_current_user_or_token)])
async def api_get_pack(pack_id: str):
    entry = kb_packs.find_spec_pack(pack_id)
    if not entry:
        raise HTTPException(404, f"pack {pack_id!r} not found")
    built_entry = kb_packs.find_pack(pack_id)
    built = _built_to_dict(built_entry)
    return {
        "definition": entry,
        "built": built,
        "built_is_stale": built_entry is not None and _spec_differs_from_built(entry, built_entry),
    }


@app.post("/api/v1/packs", status_code=201, dependencies=[Depends(get_current_user_or_token)])
async def api_create_pack(payload: dict):
    try:
        created = kb_packs.create_pack(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _invalidate_readiness_cache(created.get("id"))
    return {"status": "created", "pack": created}


@app.patch("/api/v1/packs/{pack_id}", dependencies=[Depends(get_current_user_or_token)])
async def api_update_pack(pack_id: str, patch: PackPatch):
    patch_dict = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not patch_dict:
        raise HTTPException(400, "empty patch")
    try:
        updated = kb_packs.update_pack(pack_id, patch_dict)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _invalidate_readiness_cache(pack_id)
    return {"status": "updated", "pack": updated}


@app.delete("/api/v1/packs/{pack_id}", dependencies=[Depends(get_current_user_or_token)])
async def api_delete_pack(pack_id: str, delete_artifact: bool = True):
    def_removed = kb_packs.delete_pack(pack_id)
    art_removed = kb_packs.delete_pack_artifact(pack_id) if delete_artifact else False
    if not def_removed and not art_removed:
        raise HTTPException(404, f"pack {pack_id!r} not found")
    _invalidate_readiness_cache(pack_id)
    return {"definition_removed": def_removed, "artifact_removed": art_removed}


@app.get("/api/v1/packs/{pack_id}/readiness",
         dependencies=[Depends(get_current_user_or_token)])
async def api_pack_readiness(pack_id: str):
    """Compute selection-aware readiness for a pack."""
    cached = _get_cached_readiness(pack_id)
    if cached is not None:
        return cached
    entry = kb_packs.find_spec_pack(pack_id)
    if not entry:
        raise HTTPException(404, f"pack {pack_id!r} not found")
    from quizkb.packs import compute_pack_readiness
    result = compute_pack_readiness(entry, OUTPUT_ROOT)
    _set_cached_readiness(pack_id, result)
    return result


class TranslatePlanRequest(BaseModel):
    pattern: str | None = None
    langs: list[str] = []
    scope: str = "all"  # "all" | "active"

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        if v not in ("all", "active"):
            raise ValueError("scope must be 'all' or 'active'")
        return v


def _compute_translate_plan(
    pattern: str | None,
    langs: list[str],
    cache: dict | None = None,
    user_id: int | None = None,
    scope: str = "all",
) -> list[dict]:
    if cache is None:
        cache = {}
    with get_app_db() as conn:
        rows = conn.execute("SELECT slug FROM domains ORDER BY slug").fetchall()

    matched_slugs = [
        r["slug"] for r in rows
        if not pattern or fnmatch.fnmatch(r["slug"], pattern)
    ]

    if scope == "active" and user_id is not None:
        from quizkb import users as kb_users
        settings = kb_users.get_user_settings(user_id)
        active = set(settings["profile"]["active_domains"])
        matched_slugs = [s for s in matched_slugs if s in active]

    matched_slugs.sort()

    qt_by_domain = {}
    per_lang_config = {}
    if user_id is not None:
        from quizkb import users as kb_users
        qt_by_domain = kb_users.get_effective_questions_types_batch(user_id, matched_slugs)
        settings = kb_users.get_user_settings(user_id)
        per_lang_config = settings["generation_params"].get("per_lang", {})

    items = []
    for slug in matched_slugs:
        for lang in langs:
            lang = lang.strip().lower()
            if lang == "en":
                items.append({
                    "slug": slug,
                    "lang": lang,
                    "action": "skip",
                    "reason": "source language",
                    "state": "bootstrapped"
                })
                continue

            # Per-lang filter (Q2 (i): unset = "*")
            allowed_domains = per_lang_config.get(lang, {}).get("translate_domains")
            if allowed_domains is not None:
                if allowed_domains == "*":
                    pass
                elif slug not in allowed_domains:
                    items.append({
                        "slug": slug,
                        "lang": lang,
                        "action": "skip",
                        "reason": "filtered_by_per_lang",
                        "state": "unknown",
                    })
                    continue

            status = evaluate_domain_status(
                slug, lang,
                questions_types=qt_by_domain.get(slug),
                cache=cache,
            )
            state = status["state"]

            if state == "absent_source":
                action = "skip"
                reason = "absent_source"
            elif state == "bootstrapped":
                action = "skip"
                reason = "already translated"
            else:
                action = "queue"
                reason = state

            items.append({
                "slug": slug,
                "lang": lang,
                "action": action,
                "reason": reason,
                "state": state
            })

    items.sort(key=lambda x: (x["slug"], x["lang"]))
    return items


@app.post("/api/v1/domains/translate/plan")
async def api_translate_plan(
    request: Request,
    body: TranslatePlanRequest,
    user: auth.User = Depends(get_current_user_or_token)
):
    cache = {}
    items = _compute_translate_plan(
        body.pattern, body.langs,
        cache=cache,
        user_id=user.id if user else None,
        scope=body.scope,
    )

    queue_count = sum(1 for i in items if i["action"] == "queue")
    skip_count = sum(1 for i in items if i["action"] == "skip")
    blocked_count = sum(1 for i in items if i["reason"] == "absent_source")

    matched_slugs = {i["slug"] for i in items}

    return {
        "domains_matched": len(matched_slugs),
        "items": items,
        "summary": {"queue": queue_count, "skip": skip_count, "blocked": blocked_count}
    }


@app.post("/api/v1/domains/translate/enqueue")
async def api_translate_enqueue(
    request: Request,
    body: TranslatePlanRequest,
    user: auth.User = Depends(get_current_user_or_token)
):
    cache = {}
    items = _compute_translate_plan(
        body.pattern, body.langs,
        cache=cache,
        user_id=user.id if user else None,
        scope=body.scope,
    )
    gp = _get_user_generation_payload(user)

    enqueued_items = []
    for item in items:
        if item["action"] == "queue":
            resp = await orch_post(
                "/api/v1/tasks",
                {
                    "action": "translate",
                    "domain": item["slug"],
                    "lang": item["lang"],
                    "username": user.username,
                    "generation_params": gp
                },
                request
            )
            if not resp or resp.status_code != 201:
                return JSONResponse({"error": "orchestrator_offline", "message": "Orchestrator offline"}, status_code=503)
            data = resp.json()
            item["task_id"] = data.get("task_id")
        enqueued_items.append(item)

    queue_count = sum(1 for i in enqueued_items if i["action"] == "queue")
    skip_count = sum(1 for i in enqueued_items if i["action"] == "skip")
    blocked_count = sum(1 for i in enqueued_items if i["reason"] == "absent_source")
    matched_slugs = {i["slug"] for i in enqueued_items}

    return {
        "domains_matched": len(matched_slugs),
        "items": enqueued_items,
        "summary": {"queue": queue_count, "skip": skip_count, "blocked": blocked_count}
    }


class BootstrapPlanRequest(BaseModel):
    pattern: str | None = None
    scope: str = "all"  # "all" | "active"

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        if v not in ("all", "active"):
            raise ValueError("scope must be 'all' or 'active'")
        return v


def _compute_bootstrap_plan(
    pattern: str | None,
    cache: dict | None = None,
    user_id: int | None = None,
    scope: str = "all",
) -> list[dict]:
    if cache is None:
        cache = {}
    with get_app_db() as conn:
        rows = conn.execute("SELECT slug FROM domains ORDER BY slug").fetchall()

    matched_slugs = [r["slug"] for r in rows if _match_slug(r["slug"], pattern)]

    if scope == "active" and user_id is not None:
        from quizkb import users as kb_users
        settings = kb_users.get_user_settings(user_id)
        active = set(settings["profile"]["active_domains"])
        matched_slugs = [s for s in matched_slugs if s in active]

    matched_slugs.sort()

    qt_by_domain = {}
    if user_id is not None:
        from quizkb import users as kb_users
        qt_by_domain = kb_users.get_effective_questions_types_batch(user_id, matched_slugs)

    items = []
    for slug in matched_slugs:
        status = evaluate_domain_status(
            slug, "en",
            questions_types=qt_by_domain.get(slug),
            cache=cache,
        )
        state = status["state"]

        if state == "absent_source":
            action = "skip"
            reason = "no_syllabus"
        elif state == "bootstrapped":
            action = "skip"
            reason = "already bootstrapped"
        else:
            action = "queue"
            reason = "missing"

        items.append({
            "slug": slug,
            "lang": "en",
            "action": action,
            "reason": reason,
            "state": state
        })

    items.sort(key=lambda x: x["slug"])
    return items


@app.post("/api/v1/domains/bootstrap/plan")
async def api_bootstrap_plan(
    request: Request,
    body: BootstrapPlanRequest,
    user: auth.User = Depends(get_current_user_or_token)
):
    cache = {}
    items = _compute_bootstrap_plan(
        body.pattern,
        cache=cache,
        user_id=user.id if user else None,
        scope=body.scope,
    )
    queue_count = sum(1 for i in items if i["action"] == "queue")
    skip_count = sum(1 for i in items if i["action"] == "skip")

    return {
        "domains_matched": len(items),
        "items": items,
        "summary": {"queue": queue_count, "skip": skip_count}
    }


@app.post("/api/v1/domains/bootstrap/enqueue")
async def api_bootstrap_enqueue(
    request: Request,
    body: BootstrapPlanRequest,
    user: auth.User = Depends(get_current_user_or_token)
):
    cache = {}
    items = _compute_bootstrap_plan(
        body.pattern,
        cache=cache,
        user_id=user.id if user else None,
        scope=body.scope,
    )
    gp = _get_user_generation_payload(user)

    enqueued_items = []
    for item in items:
        if item["action"] == "queue":
            resp = await orch_post(
                "/api/v1/tasks",
                {
                    "action": "bootstrap",
                    "domain": item["slug"],
                    "lang": "en",
                    "username": user.username,
                    "generation_params": gp
                },
                request
            )
            if not resp or resp.status_code != 201:
                return JSONResponse({"error": "orchestrator_offline", "message": "Orchestrator offline"}, status_code=503)
            data = resp.json()
            item["task_id"] = data.get("task_id")
        enqueued_items.append(item)

    queue_count = sum(1 for i in enqueued_items if i["action"] == "queue")
    skip_count = sum(1 for i in enqueued_items if i["action"] == "skip")

    return {
        "domains_matched": len(enqueued_items),
        "items": enqueued_items,
        "summary": {"queue": queue_count, "skip": skip_count}
    }


# --- Feedback Inbox ---

@app.get("/feedback", response_class=HTMLResponse)
async def feedback_index(request: Request, user: auth.User = Depends(get_current_user)):
    with get_server_db() as conn:
        rows = conn.execute("SELECT * FROM feedback ORDER BY server_received_at DESC LIMIT 100").fetchall()
    feedback_list = []
    for r in rows:
        d = dict(r)
        if d.get("server_received_at"):
            ts = d["server_received_at"]
            if ts > 1e11:
                ts = ts / 1000.0
            d["date_formatted"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        else:
            d["date_formatted"] = "-"
        if d.get("resolved_at"):
            ts = d["resolved_at"]
            if ts > 1e11:
                ts = ts / 1000.0
            d["resolved_at_formatted"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        else:
            d["resolved_at_formatted"] = None
        feedback_list.append(d)
    return templates.TemplateResponse(
        request=request, name="feedback.html",
        context={"user": user, "feedback": feedback_list}
    )


@app.post("/feedback/{fb_id}/resolve")
async def feedback_resolve(fb_id: int, user: auth.User = Depends(get_current_user)):
    with get_server_db() as conn:
        conn.execute("UPDATE feedback SET resolved_at = ? WHERE id = ?", (int(time.time() * 1000), fb_id))
        conn.commit()
    return RedirectResponse(url="/feedback", status_code=303)


# --- Tasks API ---

@app.post("/api/tasks/start")
async def start_task(
    request: Request,
    domain: str = Form(...), 
    lang: str = Form(...), 
    action: str = Form(...),
    target_uuid: str | None = Form(None),
    target_article_key: str | None = Form(None),
    target_count: int | None = Form(None),
    user: auth.User = Depends(get_current_user_or_token)
):
    gp = _get_user_generation_payload(user)
    resp = await orch_post(
        "/api/v1/tasks",
        {
            "action": action,
            "domain": domain,
            "lang": lang,
            "username": user.username,
            "target_uuid": target_uuid,
            "target_article_key": target_article_key,
            "target_count": target_count,
            "generation_params": gp
        },
        request
    )
    if not resp or resp.status_code != 201:
        return JSONResponse({"status": "error", "message": "Orchestrator offline"}, status_code=503)
    data = resp.json()
    task_id = data.get("task_id")
    log.info("Enqueued %s %s -> %s (id=%s)", action, domain, lang, task_id)
    accept = request.headers.get("accept", "")
    if "application/json" in accept or "json" in accept:
        return JSONResponse({"task_id": task_id})
    referer = request.headers.get("referer")
    return RedirectResponse(url=referer or f"/tasks/{task_id}", status_code=303)


@app.post("/api/tasks/{task_id}/kill")
async def kill_task(
    request: Request, task_id: str,
    user: auth.User = Depends(get_current_user_or_token)
):
    resp = await orch_post(f"/api/v1/tasks/{task_id}/kill", request=request)
    if not resp or resp.status_code != 200:
        return JSONResponse(
            {"status": "error", "message": "Orchestrator offline or task not running"},
            status_code=503)
    return JSONResponse({"status": "ok"})


@app.post("/api/tasks/{task_id}/force_interrupt")
async def force_interrupt_task(request: Request, task_id: str, user: auth.User = Depends(get_current_user_or_token)):
    """Forcefully marks a task as interrupted.

    Provides an administrative override to manually clear tasks stuck in the 'running'
    state if the remote worker daemon or host machine dies unexpectedly and cannot report completion.
    """
    resp = await orch_post(f"/api/v1/tasks/{task_id}/force_interrupt", request=request)
    if not resp or resp.status_code != 200:
        return JSONResponse({"status": "error", "message": "Orchestrator offline"}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.post("/api/tasks/{task_id}/restart")
async def restart_task(request: Request, task_id: str, user: auth.User = Depends(get_current_user_or_token)):
    resp = await orch_post(f"/api/v1/tasks/{task_id}/restart", request=request, timeout=10.0)
    if not resp or resp.status_code != 200:
        return JSONResponse({"status": "error", "message": "Orchestrator offline"}, status_code=503)
    data = resp.json()
    return JSONResponse({"status": "ok", "new_task_id": data["new_task_id"]})


@app.get("/api/tasks/active")
async def get_active_tasks(request: Request, user: auth.User = Depends(get_current_user_or_token)):
    resp = await orch_get("/api/v1/tasks/active", request=request)
    if not resp or resp.status_code != 200:
        return JSONResponse({"count": 0})
    return JSONResponse(resp.json())


@app.get("/tasks", response_class=HTMLResponse)
async def tasks_list_view(
    request: Request, 
    sort: str = "started_at", 
    dir: str = "desc", 
    page: int = 1,
    user: auth.User = Depends(get_current_user)
):
    page = max(1, page)
    limit = 50

    resp = await orch_get(
        "/api/v1/tasks",
        params={"sort": sort, "direction": dir, "page": page, "limit": limit},
        request=request
    )
    error_msg = None
    if not resp or resp.status_code != 200:
        tasks = []
        total_tasks = 0
        total_pages = 0
        error_msg = "Orchestrator is offline. Task queue is unavailable."
    else:
        data = resp.json()
        tasks = data.get("tasks", [])
        total_tasks = data.get("total", 0)
        if user.username != "admin":
            tasks = [
                t for t in tasks
                if t.get("username") in (user.username, "system", None)
            ]
        total_pages = (total_tasks + limit - 1) // limit

    return templates.TemplateResponse(
        request=request,
        name="tasks.html",
        context={
            "user": user, 
            "tasks": tasks,
            "current_sort": sort,
            "current_dir": dir.lower(),
            "page": page,
            "total_pages": total_pages,
            "total_tasks": total_tasks,
            "error": error_msg,
        }
    )


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail_view(request: Request, task_id: str, user: auth.User = Depends(get_current_user)):
    resp = await orch_get(f"/api/v1/tasks/{task_id}", request=request)
    error_msg = None
    if not resp or resp.status_code != 200:
        task = {"id": task_id, "status": "offline", "action": "unknown",
                "domain": "unknown", "lang": "en"}
        log_text = "Orchestrator is offline."
        error_msg = "Orchestrator is offline."
    else:
        task = resp.json()
        log_resp = await orch_get(f"/api/v1/tasks/{task_id}/log", request=request)
        log_text = log_resp.json().get("log", "") if log_resp and log_resp.status_code == 200 else ""

    return templates.TemplateResponse(
        request=request,
        name="task_detail.html",
        context={"user": user, "task": task, "log": log_text, "error": error_msg}
    )


@app.get("/api/tasks/{task_id}")
async def get_task_status(request: Request, task_id: str, user: auth.User = Depends(get_current_user_or_token)):
    resp = await orch_get(f"/api/v1/tasks/{task_id}", request=request)
    if not resp or resp.status_code != 200:
        return JSONResponse(
            {"status": "offline", "error": "Orchestrator offline",
             "meta": {"status": "offline"}},
             status_code=503)
    task = resp.json()
    log_resp = await orch_get(f"/api/v1/tasks/{task_id}/log", request=request)
    log_text = log_resp.json().get("log", "") if log_resp and log_resp.status_code == 200 else ""
    lines = log_text.splitlines()
    tail_log = "\n".join(lines[-100:])

    return JSONResponse({
        "status": task["status"],
        "log": tail_log,
        "meta": task
    })
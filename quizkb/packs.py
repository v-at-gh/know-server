"""Read-only views over the .qap pack repository.

The pack repository lives in a separate git repo (qap-pipeline) and is
described by a rich index (manifest.json) that update_manifest.py rebuilds
after every packaging run. This module reads that index for list/detail
views and, for the detail page, opens the archive directly to enumerate
its contents and count questions per (lang, domain).

None of the functions here write anything. All are safe to call from a
web request.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import QAPS_DIR, QAPS_INDEX
from .io_utils import load_json


@dataclass
class PackEntry:
    """One entry from the top-level index (manifest.json in qap-pipeline)."""
    file: str
    size: int
    sha256: str
    id: str
    version: str
    kind: str
    createdAt: str
    languages: list[str]
    campaigns: list[str]
    domains: list[str]
    totals: dict
    error: str | None = None

    @property
    def path(self) -> Path:
        return QAPS_DIR / self.file

    @property
    def size_mb(self) -> float:
        return self.size / (1 << 20)


@dataclass
class DomainInPack:
    slug: str
    languages: list[str]
    name: dict[str, str]
    description: dict[str, str]
    articles: dict[str, int] = field(default_factory=dict)
    questions: dict[str, int] = field(default_factory=dict)
    article_bytes: dict[str, int] = field(default_factory=dict)


@dataclass
class CampaignInPack:
    slug: str
    order: int
    name: dict[str, str]
    description: dict[str, str]
    domain_slugs: list[str]
    prerequisite_domain_slugs: list[str]


@dataclass
class PackDetail:
    entry: PackEntry
    domains: list[DomainInPack]
    campaigns: list[CampaignInPack]
    files: list[tuple[str, int]]
    articles_total: int
    questions_total: int


def load_index() -> list[PackEntry]:
    """Read the top-level index. Returns [] if the file is absent."""
    if not QAPS_INDEX.exists():
        return []
    try:
        data = json.loads(QAPS_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return []

    entries: list[PackEntry] = []
    for raw in data.get("packs", []) or []:
        entries.append(PackEntry(
            file=raw.get("file", ""),
            size=int(raw.get("size", 0) or 0),
            sha256=raw.get("sha256", ""),
            id=raw.get("id", ""),
            version=raw.get("version", ""),
            kind=raw.get("kind", "campaign"),
            createdAt=raw.get("createdAt", ""),
            languages=list(raw.get("languages", []) or []),
            campaigns=list(raw.get("campaigns", []) or []),
            domains=list(raw.get("domains", []) or []),
            totals=dict(raw.get("totals", {}) or {}),
            error=raw.get("error"),
        ))
    return entries


def find_pack(pack_id: str) -> PackEntry | None:
    """Look up by id. If two packs share an id (rebuild), returns the newest."""
    matches = [e for e in load_index() if e.id == pack_id]
    if not matches:
        return None
    matches.sort(key=lambda e: e.createdAt, reverse=True)
    return matches[0]


def _read_inner_manifest(z: zipfile.ZipFile) -> dict:
    with z.open("manifest.json") as f:
        return json.loads(f.read().decode("utf-8"))


def _count_questions(z: zipfile.ZipFile, lang: str, domain: str) -> int:
    path = f"datasets/{lang}/{domain}.json"
    try:
        with z.open(path) as f:
            data = json.loads(f.read().decode("utf-8"))
        return len(data) if isinstance(data, list) else 0
    except KeyError:
        return 0
    except Exception:
        return 0


def _article_stats(z: zipfile.ZipFile, lang: str, domain: str) -> tuple[int, int]:
    """Return (article_count, total_bytes) for one (lang, domain)."""
    prefix = f"knowledge/{lang}/{domain}/"
    count = 0
    total = 0
    for info in z.infolist():
        if info.filename.startswith(prefix) and info.filename.endswith(".md"):
            count += 1
            total += info.file_size
    return count, total


def load_detail(entry: PackEntry) -> PackDetail | None:
    """Open the archive, build the detail view. Returns None on IO error."""
    path = entry.path
    if not path.exists():
        return None

    try:
        with zipfile.ZipFile(path) as z:
            inner = _read_inner_manifest(z)

            domains: list[DomainInPack] = []
            for d in inner.get("domains", []) or []:
                slug = d.get("slug", "")
                domain_langs = list(d.get("languages", []) or [])
                articles: dict[str, int] = {}
                questions: dict[str, int] = {}
                article_bytes: dict[str, int] = {}
                for lang in domain_langs:
                    a_count, a_bytes = _article_stats(z, lang, slug)
                    articles[lang] = a_count
                    article_bytes[lang] = a_bytes
                    questions[lang] = _count_questions(z, lang, slug)
                domains.append(DomainInPack(
                    slug=slug,
                    languages=domain_langs,
                    name=dict(d.get("name", {}) or {}),
                    description=dict(d.get("description", {}) or {}),
                    articles=articles,
                    questions=questions,
                    article_bytes=article_bytes,
                ))

            campaigns: list[CampaignInPack] = []
            for c in inner.get("campaigns", []) or []:
                campaigns.append(CampaignInPack(
                    slug=c.get("slug", ""),
                    order=int(c.get("order", 0) or 0),
                    name=dict(c.get("name", {}) or {}),
                    description=dict(c.get("description", {}) or {}),
                    domain_slugs=list(c.get("domainSlugs", []) or []),
                    prerequisite_domain_slugs=list(c.get("prerequisiteDomainSlugs", []) or []),
                ))

            files: list[tuple[str, int]] = []
            for info in z.infolist():
                if info.filename == "manifest.json":
                    continue
                if info.filename.endswith("/"):
                    continue
                files.append((info.filename, info.file_size))
            files.sort()

            articles_total = sum(max(d.articles.values(), default=0) for d in domains)
            questions_total = sum(max(d.questions.values(), default=0) for d in domains)

            return PackDetail(
                entry=entry,
                domains=domains,
                campaigns=campaigns,
                files=files,
                articles_total=articles_total,
                questions_total=questions_total,
            )
    except Exception:
        return None


# --- Spec CRUD (pack definitions in state/packs.json) ---

SPEC_SCHEMA_VERSION = 1


def load_spec() -> dict:
    """Read pack definitions. Returns {"schema_version": N, "packs": [...]}.

    Missing file -> empty spec with current version. Corrupt JSON -> raise
    ValueError (do NOT silently return empty; corruption must be visible).
    Missing schema_version -> assume v0, migrate on next save.
    """
    from quizkb.config import PACKS_JSON
    if not PACKS_JSON.exists():
        return {"schema_version": SPEC_SCHEMA_VERSION, "packs": []}
    try:
        data = json.loads(PACKS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"packs.json corrupt: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("packs.json must be a JSON object")
    packs = data.get("packs", [])
    if not isinstance(packs, list):
        raise ValueError("packs.json 'packs' must be a list")
    return {
        "schema_version": data.get("schema_version", 0),
        "packs": packs,
    }


def _normalize_pack(pack: dict) -> dict:
    """Ensure all standard fields present as empty lists when absent.

    Preserves unknown keys (forward compat for C.3-B selection fields).
    """
    out = {
        "id": pack["id"],
        "kind": pack.get("kind", "campaign"),
        "domains": list(pack.get("domains", [])),
        "languages": list(pack.get("languages", [])),
        "campaigns": list(pack.get("campaigns", [])),
    }
    sel = pack.get("selection")
    if sel:
        normalized_sel = {}
        for ds, cfg in sel.items():
            if isinstance(cfg, dict):
                normalized_sel[ds] = {
                    "articles": list(cfg.get("articles", [])),
                    "questions_per_article": int(cfg.get("questions_per_article", 0)),
                }
            else:
                normalized_sel[ds] = cfg
        out["selection"] = normalized_sel
    for k, v in pack.items():
        if k not in out and k != "selection":
            out[k] = v
    return out


def validate_pack(pack: dict, *, existing_ids: set[str] | None = None, is_update: bool = False) -> None:
    """Raise ValueError on invalid pack definition.

    Validations:
    - id present, non-empty, matches [a-z0-9_-]+
    - kind in {"campaign", "domain", "custom"}
    - domains is list of non-empty strings
    - languages is list of non-empty strings, each in TARGET_LANGS + SOURCE_LANG
    - campaigns is list of non-empty strings
    - on create: id not in existing_ids
    - on update: existing_ids must be provided for existence check by caller
    - domains and campaigns both empty -> warning, not error (in-progress pack OK)
    """
    import re
    from quizkb.config import SOURCE_LANG, TARGET_LANGS

    pid = pack.get("id", "")
    if not pid:
        raise ValueError("pack id required")
    if not re.match(r"^[a-z0-9_-]+$", pid):
        raise ValueError(f"invalid pack id {pid!r}: must match [a-z0-9_-]+")

    kind = pack.get("kind", "")
    if kind not in ("campaign", "domain", "custom"):
        raise ValueError(f"invalid kind {kind!r}: must be campaign|domain|custom")

    allowed_langs = {SOURCE_LANG, *TARGET_LANGS}
    for lang in pack.get("languages", []):
        if not isinstance(lang, str) or not lang:
            raise ValueError(f"invalid language {lang!r}")
        if lang not in allowed_langs:
            raise ValueError(f"language {lang!r} not in configured langs")

    for field in ("domains", "campaigns"):
        val = pack.get(field, [])
        if not isinstance(val, list):
            raise ValueError(f"{field} must be a list")
        for item in val:
            if not isinstance(item, str) or not item:
                raise ValueError(f"invalid {field} entry {item!r}")

    # Selection validation (if present)
    sel = pack.get("selection")
    if sel is not None:
        if not isinstance(sel, dict):
            raise ValueError("selection must be a dict")
        domain_set = set(pack.get("domains", []))
        for ds, cfg in sel.items():
            if ds not in domain_set:
                raise ValueError(
                    f"selection key {ds!r} not in pack domains; "
                    f"add it to 'domains' or remove from 'selection'"
                )
            if not isinstance(cfg, dict):
                continue  # non-dict (legacy/arbitrary) allowed for backward compat
            articles = cfg.get("articles", [])
            if not isinstance(articles, list):
                raise ValueError(f"selection[{ds!r}].articles must be a list")
            for slug in articles:
                if not isinstance(slug, str) or not slug:
                    raise ValueError(f"selection[{ds!r}].articles contains invalid slug {slug!r}")
            qpa = cfg.get("questions_per_article", 0)
            if not isinstance(qpa, int) or qpa < 1:
                raise ValueError(f"selection[{ds!r}].questions_per_article must be int >= 1")

    if not is_update and existing_ids is not None and pid in existing_ids:
        raise ValueError(f"pack id {pid!r} already exists")


def save_spec(spec: dict) -> None:
    """Atomic write of spec. Uses save_json_atomic for crash safety."""
    from quizkb.config import PACKS_JSON
    from quizkb.io_utils import save_json_atomic
    normalized = {
        "schema_version": SPEC_SCHEMA_VERSION,
        "packs": [_normalize_pack(p) for p in spec.get("packs", [])],
    }
    save_json_atomic(PACKS_JSON, normalized)


def create_pack(pack: dict) -> dict:
    """Create new pack. Raises ValueError on validation. Returns normalized pack."""
    if not isinstance(pack, dict):
        raise ValueError("pack must be a dict")
    if not pack.get("id"):
        raise ValueError("pack id required")
    spec = load_spec()
    existing_ids = {p["id"] for p in spec["packs"]}
    normalized = _normalize_pack(pack)
    validate_pack(normalized, existing_ids=existing_ids, is_update=False)
    spec["packs"].append(normalized)
    save_spec(spec)
    return normalized


def update_pack(pack_id: str, patch: dict) -> dict:
    """Merge patch into existing pack by id. Raises ValueError if not found or invalid.

    Patch semantics: for lists (domains/languages/campaigns), replace if present
    (empty list = clear). For scalar fields (kind), replace if present. Unknown
    keys in patch are merged (forward compat).
    """
    spec = load_spec()
    idx = next((i for i, p in enumerate(spec["packs"]) if p["id"] == pack_id), -1)
    if idx < 0:
        raise ValueError(f"pack id {pack_id!r} not found")
    merged = {**spec["packs"][idx]}
    for k, v in patch.items():
        if k == "id":
            continue  # id immutable
        merged[k] = v
    normalized = _normalize_pack(merged)
    validate_pack(normalized, is_update=True)
    spec["packs"][idx] = normalized
    save_spec(spec)
    return normalized


def delete_pack(pack_id: str) -> bool:
    """Remove pack definition. Returns True if found+removed, False if absent.
    Does NOT delete .qap artifact — caller decides (see delete_pack_and_artifact)."""
    spec = load_spec()
    before = len(spec["packs"])
    spec["packs"] = [p for p in spec["packs"] if p["id"] != pack_id]
    if len(spec["packs"]) == before:
        return False
    save_spec(spec)
    return True


def delete_pack_artifact(pack_id: str) -> bool:
    """Remove state/qaps/{id}.qap. Returns True if removed, False if absent.
    Does NOT touch spec."""
    from quizkb.config import QAPS_DIR
    artifact = QAPS_DIR / f"{pack_id}.qap"
    if artifact.exists():
        artifact.unlink()
        return True
    return False


def find_spec_pack(pack_id: str) -> dict | None:
    """Return normalized spec entry or None."""
    spec = load_spec()
    entry = next((p for p in spec["packs"] if p["id"] == pack_id), None)
    return _normalize_pack(entry) if entry else None


def compute_pack_readiness(
    pack_def: dict,
    output_root: Path,
) -> dict:
    """Compute selection-aware readiness for a pack definition."""
    from quizkb.release import check_pack

    pack_id = pack_def["id"]
    domains = pack_def.get("domains", [])
    langs = pack_def.get("languages", [])
    selection = pack_def.get("selection", {})
    has_selection = bool(selection)

    blockers: list[str] = []
    per_domain: dict = {}

    for ds in domains:
        if ds in selection:
            sel = selection[ds]
            per_domain[ds] = {
                "articles_selected": len(sel.get("articles", [])),
                "per_lang": {},
            }

    for lang in langs:
        for ds in domains:
            sel = selection.get(ds)
            status = check_pack(output_root, lang, ds, selection=sel)
            if not status.ok:
                blockers.extend(f"{lang}/{ds}: {b}" for b in status.blockers)
            if sel:
                detail = _per_article_detail(output_root, lang, ds, sel)
                per_domain[ds]["per_lang"][lang] = detail

    for ds, entry in per_domain.items():
        langs_data = list(entry["per_lang"].values())
        if langs_data:
            entry["articles_present"] = min(
                d["articles_present"] for d in langs_data
            )
            entry["articles_complete"] = min(
                d["articles_complete"] for d in langs_data
            )
            entry["all_complete"] = all(
                d["all_complete"] for d in langs_data
            )

    return {
        "pack_id": pack_id,
        "ready": not blockers,
        "has_selection": has_selection,
        "blockers": blockers,
        "per_domain": per_domain,
    }


def _per_article_detail(
    output_root: Path, lang: str, domain_slug: str, sel: dict,
) -> dict:
    """Per-article detail for one (lang, domain) selection."""
    target_articles = sel.get("articles", [])
    target_qpa = sel.get("questions_per_article", 0)

    qs = load_json(
        output_root / "datasets" / lang / f"{domain_slug}.json", []
    ) or []
    counts_by_slug: dict[str, int] = {}
    for q in qs:
        rel = q.get("relatedArticleSlug", "")
        if "/" in rel:
            local = rel.split("/", 1)[1]
            counts_by_slug[local] = counts_by_slug.get(local, 0) + 1

    articles_detail: list[dict] = []
    for slug in target_articles:
        md = output_root / "knowledge" / lang / domain_slug / f"{slug}.md"
        present = md.exists()
        q_current = counts_by_slug.get(slug, 0)
        complete = present and q_current >= target_qpa
        articles_detail.append({
            "slug": slug,
            "present": present,
            "questions_current": q_current,
            "questions_target": target_qpa,
            "complete": complete,
        })

    return {
        "articles": articles_detail,
        "articles_present": sum(1 for a in articles_detail if a["present"]),
        "articles_complete": sum(1 for a in articles_detail if a["complete"]),
        "all_complete": all(a["complete"] for a in articles_detail),
    }


__all__ = [
    "PackEntry", "DomainInPack", "CampaignInPack", "PackDetail",
    "load_index", "find_pack", "load_detail",
    "SPEC_SCHEMA_VERSION", "load_spec", "save_spec", "validate_pack",
    "create_pack", "update_pack", "delete_pack", "delete_pack_artifact",
    "find_spec_pack", "compute_pack_readiness",
]

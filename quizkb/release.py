"""Release gate: decide which packs are ready, copy ready packs into a release directory."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import (
    SOURCE_LANG, TARGET_LANGS, DOMAINS, CAMPAIGNS,
)
from .io_utils import load_json, save_json

MIN_MD_BYTES = 2000


@dataclass
class PackStatus:
    lang: str
    domain: str
    ok: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    articles: int = 0
    questions: int = 0


@dataclass
class CampaignStatus:
    lang: str
    slug: str
    ok: bool
    blockers: list[str] = field(default_factory=list)


@dataclass
class ReleaseReport:
    packs: list[PackStatus]
    campaigns: list[CampaignStatus]
    release_root: Path

    @property
    def ready_packs(self) -> int:
        return sum(1 for p in self.packs if p.ok)

    @property
    def total_packs(self) -> int:
        return len(self.packs)

    @property
    def ready_campaigns(self) -> int:
        return sum(1 for c in self.campaigns if c.ok)

    @property
    def total_campaigns(self) -> int:
        return len(self.campaigns)


def check_pack(
    output_root: Path,
    lang: str,
    domain_slug: str,
    *,
    selection: dict | None = None,
) -> PackStatus:
    """Admin readiness check. Delegates to shared _readiness_blockers.

    If `selection` is provided (dict with `articles: list[str]` and
    `questions_per_article: int`), additionally validates that each selected
    article exists in `lang` and has at least `questions_per_article`
    questions. Missing articles or insufficient questions become blockers
    (same shape as data-integrity blockers).
    """
    from quizkb.state import _readiness_blockers

    blockers = list(_readiness_blockers(output_root, lang, domain_slug))

    if selection:
        target_articles = selection.get("articles", [])
        target_qpa = selection.get("questions_per_article", 0)
        qs = load_json(
            output_root / "datasets" / lang / f"{domain_slug}.json", []
        ) or []
        counts_by_slug: dict[str, int] = {}
        for q in qs:
            rel = q.get("relatedArticleSlug", "")
            if "/" in rel:
                local = rel.split("/", 1)[1]
                counts_by_slug[local] = counts_by_slug.get(local, 0) + 1

        for slug in target_articles:
            md = output_root / "knowledge" / lang / domain_slug / f"{slug}.md"
            if not md.exists():
                blockers.append(f"selection: article {slug!r} missing in {lang}")
                continue
            have = counts_by_slug.get(slug, 0)
            if have < target_qpa:
                blockers.append(
                    f"selection: article {slug!r} has {have}/{target_qpa} questions in {lang}"
                )

    idx_all = load_json(output_root / "knowledge" / lang / "index.json", []) or []
    idx_slugs = {
        e["slug"].split("/", 1)[1]
        for e in idx_all
        if e.get("domainSlug") == domain_slug
    }
    qs = load_json(
        output_root / "datasets" / lang / f"{domain_slug}.json", []
    ) or []

    return PackStatus(
        lang=lang,
        domain=domain_slug,
        ok=(not blockers),
        blockers=blockers,
        warnings=[],  # field kept for compatibility; always empty going forward
        articles=len(idx_slugs),
        questions=len(qs),
    )


def check_campaign(output_root: Path, lang: str, campaign: dict) -> CampaignStatus:
    blockers: list[str] = []
    for ds in campaign["domainSlugs"]:
        status = check_pack(output_root, lang, ds)
        if not status.ok:
            blockers.extend(f"{ds}: {b}" for b in status.blockers)
    return CampaignStatus(
        lang=lang, slug=campaign["slug"],
        ok=(not blockers), blockers=blockers,
    )


def build_release(
    output_root: Path,
    release_root: Path,
    *,
    languages: list[str] | None = None,
    domains: list[dict] | None = None,
    campaigns: list[dict] | None = None,
    selection: dict | None = None,
    verbose: bool = True,
) -> ReleaseReport:
    langs = languages if languages is not None else [SOURCE_LANG] + list(TARGET_LANGS)
    dom_list = domains if domains is not None else DOMAINS
    camp_list = campaigns if campaigns is not None else CAMPAIGNS

    if release_root.exists():
        shutil.rmtree(release_root)
    release_root.mkdir(parents=True)

    packs: list[PackStatus] = []
    campaign_statuses: list[CampaignStatus] = []

    if verbose:
        print(f"{'lang':<4} {'domain':<22} {'status':<11} {'art':>4} {'q':>4}  notes")
        print("-" * 80)

    for lang in langs:
        for d in dom_list:
            ds = d["slug"]
            sel = selection.get(ds) if selection else None
            status = check_pack(output_root, lang, ds, selection=sel)
            packs.append(status)
            if verbose:
                notes = ""
                if status.blockers:
                    notes += "BLOCK: " + "; ".join(status.blockers)
                if status.warnings:
                    notes += ("  " if notes else "") + "warn: " + "; ".join(status.warnings)
                mark = "ready" if status.ok else "incomplete"
                print(f"{lang:<4} {ds:<22} {mark:<11} "
                      f"{status.articles:>4} {status.questions:>4}  {notes}")
            if status.ok:
                _copy_pack(output_root, release_root, lang, ds, selection=sel)

    if verbose:
        print()
        print(f"{'lang':<4} {'campaign':<16} {'status':<11}  notes")
        print("-" * 62)

    campaign_status_by_key: dict[tuple[str, str], bool] = {}
    for lang in langs:
        for c in camp_list:
            cstatus = check_campaign(output_root, lang, c)
            campaign_statuses.append(cstatus)
            campaign_status_by_key[(lang, c["slug"])] = cstatus.ok
            if verbose:
                notes = "BLOCK: " + "; ".join(cstatus.blockers) if cstatus.blockers else ""
                mark = "ready" if cstatus.ok else "incomplete"
                print(f"{lang:<4} {c['slug']:<16} {mark:<11}  {notes}")

    for lang in langs:
        campaigns_all = load_json(
            output_root / "datasets" / lang / "campaigns.json", []
        ) or []
        ready = [c for c in campaigns_all
                 if campaign_status_by_key.get((lang, c["slug"]), False)]
        if not ready:
            continue
        dst = release_root / "datasets" / lang / "campaigns.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        save_json(dst, ready)

    report = ReleaseReport(
        packs=packs, campaigns=campaign_statuses, release_root=release_root,
    )

    manifest = {
        "generatedAt": datetime.now().isoformat(),
        "sourceLang": SOURCE_LANG,
        "packsTotal": report.total_packs,
        "packsReady": report.ready_packs,
        "campaignsTotal": report.total_campaigns,
        "campaignsReady": report.ready_campaigns,
    }
    save_json(release_root / "manifest.json", manifest)

    if verbose:
        print()
        print("=" * 80)
        print(f"packs ready:      {report.ready_packs}/{report.total_packs}")
        print(f"campaigns ready:  {report.ready_campaigns}/{report.total_campaigns}")
        print(f"release root:     {release_root.resolve()}")
        print("=" * 80)

    return report


def _copy_pack(
    output_root: Path,
    release_root: Path,
    lang: str,
    domain_slug: str,
    *,
    selection: dict | None = None,
) -> None:
    src_q = output_root / "datasets" / lang / f"{domain_slug}.json"
    dst_q = release_root / "datasets" / lang / f"{domain_slug}.json"
    dst_q.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_q, dst_q)

    domains_all = load_json(output_root / "datasets" / lang / "domains.json", []) or []
    entry = [d for d in domains_all if d["slug"] == domain_slug]
    domains_dst = release_root / "datasets" / lang / "domains.json"
    existing = load_json(domains_dst, []) or []
    existing = [d for d in existing if d["slug"] != domain_slug] + entry
    existing.sort(key=lambda d: d["slug"])
    save_json(domains_dst, existing)

    idx_all = load_json(output_root / "knowledge" / lang / "index.json", []) or []
    idx_here = [e for e in idx_all if e["domainSlug"] == domain_slug]
    idx_dst = release_root / "knowledge" / lang / "index.json"
    existing_idx = load_json(idx_dst, []) or []
    existing_idx = [e for e in existing_idx if e["domainSlug"] != domain_slug] + idx_here
    existing_idx.sort(key=lambda e: (e["domainSlug"], e["order"]))
    save_json(idx_dst, existing_idx)

    for e in idx_here:
        src_md = output_root / "knowledge" / lang / e["path"]
        dst_md = release_root / "knowledge" / lang / e["path"]
        dst_md.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_md, dst_md)

    if selection:
        target_articles = set(selection.get("articles", []))
        if not target_articles:
            return

        all_qs = load_json(dst_q, []) or []
        filtered_qs = [
            q for q in all_qs
            if _slug_from_rel(q.get("relatedArticleSlug", "")) in target_articles
        ]
        save_json(dst_q, filtered_qs)

        idx_now = load_json(idx_dst, []) or []
        filtered_idx = [
            e for e in idx_now
            if e.get("domainSlug") != domain_slug
            or _slug_from_rel(e.get("slug", "")) in target_articles
        ]
        save_json(idx_dst, filtered_idx)

        md_dir = release_root / "knowledge" / lang / domain_slug
        if md_dir.exists():
            for md in md_dir.glob("*.md"):
                if md.stem not in target_articles:
                    md.unlink()


def _slug_from_rel(rel: str) -> str:
    return rel.split("/", 1)[1] if "/" in rel else rel


__all__ = [
    "PackStatus", "CampaignStatus", "ReleaseReport",
    "check_pack", "check_campaign", "build_release",
    "MIN_MD_BYTES",
]

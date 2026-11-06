#!/usr/bin/env python3
"""Package a release directory into a .qap bundle (format v2)."""
from __future__ import annotations
import argparse, hashlib, json, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path

QAP_FORMAT_VERSION = 2
SUPPORTED_KINDS = {"campaign", "bundle", "translation"}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def collect_files(release: Path) -> list[Path]:
    files = []
    for sub in ("datasets", "knowledge"):
        root = release / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if ".ipynb_checkpoints" in p.parts:
                continue
            if p.name.endswith(".bak"):
                continue
            if ".tmp" in p.suffixes:
                continue
            files.append(p)
    return files


def collect_domains(release: Path) -> list[dict]:
    datasets = release / "datasets"
    by_slug: dict[str, dict] = {}
    for lang_dir in sorted(datasets.iterdir()):
        if not lang_dir.is_dir():
            continue
        lang = lang_dir.name
        path = lang_dir / "domains.json"
        if not path.exists():
            continue
        for e in json.loads(path.read_text(encoding="utf-8")):
            slug = e["slug"]
            d = by_slug.setdefault(slug, {
                "slug": slug,
                "kind": e.get("kind", "unknown"),
                "name": {}, "description": {}, "languages": [],
            })
            d["name"][lang] = e["name"]
            d["description"][lang] = e.get("description", "")
            if lang not in d["languages"]:
                d["languages"].append(lang)
    for d in by_slug.values():
        d["languages"].sort()
    return sorted(by_slug.values(), key=lambda d: d["slug"])


def collect_campaigns(release: Path) -> list[dict]:
    datasets = release / "datasets"
    by_slug: dict[str, dict] = {}
    for lang_dir in sorted(datasets.iterdir()):
        if not lang_dir.is_dir():
            continue
        lang = lang_dir.name
        path = lang_dir / "campaigns.json"
        if not path.exists():
            continue
        for e in json.loads(path.read_text(encoding="utf-8")):
            slug = e["slug"]
            c = by_slug.setdefault(slug, {
                "slug": slug,
                "order": e.get("order", 0),
                "name": {}, "description": {},
                "domainSlugs": list(e["domainSlugs"]),
                "prerequisiteDomainSlugs": list(e.get("prerequisiteDomainSlugs", [])),
                "prerequisiteCampaignSlugs": list(e.get("prerequisiteCampaignSlugs", [])),
            })
            c["name"][lang] = e["name"]
            c["description"][lang] = e.get("description", "")
    return sorted(by_slug.values(), key=lambda c: (c["order"], c["slug"]))


def build(
    release: Path,
    out: Path,
    *,
    pack_id: str,
    pack_version: str,
    kind: str = "campaign",
) -> Path:
    if kind not in SUPPORTED_KINDS:
        sys.exit(f"unsupported --kind: {kind}; expected one of {sorted(SUPPORTED_KINDS)}")
    if not release.is_dir():
        sys.exit(f"release dir not found: {release}")

    files = collect_files(release)
    if not files:
        sys.exit("no files found under datasets/ or knowledge/")

    checksums = {p.relative_to(release).as_posix(): sha256_of(p) for p in files}
    datasets = release / "datasets"
    languages = sorted(p.name for p in datasets.iterdir() if p.is_dir())

    manifest = {
        "qapFormatVersion": QAP_FORMAT_VERSION,
        "id": pack_id,
        "version": pack_version,
        "kind": kind,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "publisher": {"name": "QuizApp Core", "url": None},
        "signature": None,
        "languages": languages,
        "domains": collect_domains(release),
        "campaigns": collect_campaigns(release),
        "checksums": checksums,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for p in files:
            z.write(p, arcname=p.relative_to(release).as_posix())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--id", default="quiz-content")
    ap.add_argument("--version", default=None)
    ap.add_argument("--kind", default="campaign", choices=sorted(SUPPORTED_KINDS))
    args = ap.parse_args()

    version = args.version or datetime.now(timezone.utc).strftime("%Y.%m.%d")
    path = build(
        args.release, args.out,
        pack_id=args.id, pack_version=version, kind=args.kind,
    )
    size_mb = path.stat().st_size / (1 << 20)
    print(f"wrote {path} ({size_mb:.1f} MB, kind={args.kind})")
    with zipfile.ZipFile(path) as z:
        print(f"  entries: {len(z.namelist())}")


if __name__ == "__main__":
    main()
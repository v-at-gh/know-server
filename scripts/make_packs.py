#!/usr/bin/env python3
"""Build every .qap listed in packs.json."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

TMP_DIR = REPO_ROOT / "tmp"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))
import build_qap

from quizkb.config import PACKS_JSON, QAPS_DIR
from quizkb.io_utils import load_json
from quizkb.release import build_release

def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack-id", default=None, help="Build only the specified pack ID")
    args = ap.parse_args()

    if not PACKS_JSON.exists():
        sys.exit(f"packs.json not found: {PACKS_JSON}")

    output_root = REPO_ROOT / "output"
    if not output_root.is_dir():
        sys.exit(f"output root not found: {output_root}\nRun the pipeline first.")

    from quizkb.config import STATE_DIR
    config_domains_path = STATE_DIR / "config" / "domains.json"
    config_campaigns_path = STATE_DIR / "config" / "campaigns.json"

    if config_domains_path.exists():
        domains_meta = load_json(config_domains_path, [])
    else:
        domains_meta = load_json(output_root / "datasets" / "en" / "domains.json", [])
    domains_by_slug = {d["slug"]: d for d in domains_meta}

    if config_campaigns_path.exists():
        campaigns_meta = load_json(config_campaigns_path, [])
    else:
        campaigns_meta = load_json(output_root / "datasets" / "en" / "campaigns.json", [])
    campaigns_by_slug = {c["slug"]: c for c in campaigns_meta}

    specs = json.loads(PACKS_JSON.read_text(encoding="utf-8")).get("packs", [])
    if not specs:
        sys.exit("packs.json: empty or missing 'packs' array")

    if args.pack_id:
        specs = [s for s in specs if s["id"] == args.pack_id]
        if not specs:
            sys.exit(f"pack-id '{args.pack_id}' not found in packs.json")

    version = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    QAPS_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    built: list[tuple[str, int]] = []

    for spec in specs:
        pack_id = spec["id"]
        kind = spec.get("kind", "campaign")

        # 1. FIX: Extract languages from spec
        target_languages = spec.get("languages")
        if target_languages is not None:
            target_languages = list(target_languages)
            
        campaign_slugs = list(spec.get("campaigns", []) or [])

        valid_campaign_slugs = [cs for cs in campaign_slugs if cs in campaigns_by_slug]

        domain_slugs = dedupe(
            [ds for cs in valid_campaign_slugs for ds in campaigns_by_slug[cs].get("domainSlugs", [])]
            + list(spec.get("domains", []) or [])
        )

        valid_domain_slugs = [ds for ds in domain_slugs if ds in domains_by_slug]

        if not valid_domain_slugs:
            missing = [ds for ds in domain_slugs if ds not in domains_by_slug]
            print(f"[{pack_id}] No valid domains found. Missing from output/datasets/en/domains.json: {missing}")
            print(f"[{pack_id}] Run sync or bootstrap the domain first.")
            continue

        # 2. FIX: Auto-infer campaigns if all their domains are selected
        inferred_campaign_slugs = set(valid_campaign_slugs)
        for c in campaigns_meta:
            c_domains = c.get("domainSlugs", [])
            if not c_domains:
                continue
            if all(ds in valid_domain_slugs for ds in c_domains):
                inferred_campaign_slugs.add(c["slug"])

        valid_campaign_slugs = list(inferred_campaign_slugs)

        domains = [domains_by_slug[ds] for ds in valid_domain_slugs]
        campaigns = [campaigns_by_slug[cs] for cs in valid_campaign_slugs]

        release_dir = TMP_DIR / pack_id
        print(f"\n[{pack_id}] kind={kind} domains={valid_domain_slugs}")
        print(f"[{pack_id}] languages={target_languages or 'ALL'} campaigns={valid_campaign_slugs}")

        # Pass languages and selection to build_release
        report = build_release(
            output_root=output_root,
            release_root=release_dir,
            languages=target_languages,
            domains=domains,
            campaigns=campaigns,
            selection=spec.get("selection"),
            verbose=True,
        )

        if report.ready_packs == 0:
            print(f"[{pack_id}] nothing ready, skipping")
            shutil.rmtree(release_dir, ignore_errors=True)
            continue

        out = QAPS_DIR / f"{pack_id}.qap"
        build_qap.build(
            release_dir, out,
            pack_id=pack_id, pack_version=version, kind=kind,
        )
        size = out.stat().st_size
        print(f"[{pack_id}] wrote {out.name} ({size / (1 << 20):.1f} MB)")
        built.append((pack_id, size))
        shutil.rmtree(release_dir, ignore_errors=True)

    print(f"\nbuilt {len(built)} of {len(specs)} packs:")
    for pid, size in built:
        print(f"  {pid:<20} {size / (1 << 20):>6.1f} MB")

    if not built:
        print("\n[ERROR] No packs were built. Check the logs above for BLOCK reasons.")
        return 1

    print("\nnext: run scripts/update_manifest.py")
    return 0

if __name__ == "__main__":
    sys.exit(main())
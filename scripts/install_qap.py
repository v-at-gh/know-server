#!/usr/bin/env python3
"""Extract a .qap and merge into Android assets without destroying unrelated files.

Merge semantics:
  - metadata files (domains.json, campaigns.json, index.json): merged by slug,
    QAP entries replace matching slugs, non-matching existing entries preserved
  - content files (<domain>.json, *.md): overwritten where QAP has them,
    existing files not present in QAP are left alone
"""
from __future__ import annotations
import argparse, json, shutil, sys, tempfile, zipfile
from pathlib import Path


def merge_json_array(existing_path: Path, incoming: list[dict], key: str) -> list[dict]:
    existing = []
    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    incoming_keys = {e[key] for e in incoming}
    kept = [e for e in existing if e.get(key) not in incoming_keys]
    merged = kept + incoming
    merged.sort(key=lambda e: (e.get("domainSlug", ""), e.get("order", 0), e.get(key, "")))
    return merged


def install(qap: Path, assets: Path) -> None:
    if not assets.is_dir():
        sys.exit(f"assets directory not found: {assets}")

    with tempfile.TemporaryDirectory() as td:
        stage = Path(td)
        with zipfile.ZipFile(qap) as z:
            z.extractall(stage)

        # 1. Manifest — single-slot, always overwrite
        manifest_src = stage / "manifest.json"
        if manifest_src.exists():
            manifest_dst = assets / "qap" / "manifest.json"
            manifest_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest_src, manifest_dst)
            print(f"  manifest: {manifest_dst.relative_to(assets)}")
        else:
            print("  [warn] manifest.json not found in archive")

        # 2. Legacy metadata: merge domains.json / campaigns.json / index.json
        meta_files = []
        datasets_stage = stage / "datasets"
        if datasets_stage.is_dir():
            for lang_dir in datasets_stage.iterdir():
                if not lang_dir.is_dir():
                    continue
                for name in ("domains.json", "campaigns.json"):
                    src = lang_dir / name
                    if src.exists():
                        meta_files.append(
                            (src, assets / "datasets" / lang_dir.name / name, "slug")
                        )
        knowledge_stage = stage / "knowledge"
        if knowledge_stage.is_dir():
            for lang_dir in knowledge_stage.iterdir():
                if not lang_dir.is_dir():
                    continue
                src = lang_dir / "index.json"
                if src.exists():
                    meta_files.append(
                        (src, assets / "knowledge" / lang_dir.name / "index.json", "slug")
                    )

        for src, dst, key in meta_files:
            incoming = json.loads(src.read_text(encoding="utf-8"))
            merged = merge_json_array(dst, incoming, key)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  merged: {dst.relative_to(assets)} ({len(incoming)} incoming)")

        # Non-metadata files: overwrite in place
        copied = 0
        for root_name in ("datasets", "knowledge"):
            src_root = stage / root_name
            if not src_root.is_dir():
                continue
            for p in src_root.rglob("*"):
                if not p.is_file():
                    continue
                if p.name in ("domains.json", "campaigns.json", "index.json"):
                    continue
                rel = p.relative_to(stage)
                dst = assets / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                copied += 1
        print(f"  copied: {copied} files")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("qap", type=Path)
    ap.add_argument("assets", type=Path)
    args = ap.parse_args()
    if not args.qap.exists():
        sys.exit(f"not found: {args.qap}")
    install(args.qap, args.assets)


if __name__ == "__main__":
    main()

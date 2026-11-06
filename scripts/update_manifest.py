#!/usr/bin/env python3
"""Rebuild manifest.json — the rich index of all .qap archives in qaps/.

For every *.qap we open the archive, read its embedded manifest.json, and
emit a single index entry that carries both the file metadata (name, size,
sha256) and a flattened copy of the archive manifest (id, version, kind,
languages, campaigns, domains, totals). The admin panel reads this index to
render /pack/<id> without touching the archives on every request.

Run this after every build_qap.py invocation.
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from quizkb.config import QAPS_DIR, QAPS_INDEX


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()

def _totals(z: zipfile.ZipFile, inner: dict) -> dict:
    checksums = inner.get("checksums", {}) or {}
    article_paths = set()
    question_counts_by_lang = {}

    for name in checksums:
        if name.startswith("knowledge/") and name.endswith(".md"):
            parts = name.split("/")
            if len(parts) >= 4:
                # parts[0]=knowledge, parts[1]=lang, parts[2]=domain, parts[3]=slug.md
                article_paths.add("/".join(parts[2:]))
        elif name.startswith("datasets/") and name.endswith(".json"):
            if "domains.json" in name or "campaigns.json" in name:
                continue
            try:
                data = json.loads(z.read(name))
                if isinstance(data, list):
                    lang = name.split("/")[1]
                    question_counts_by_lang[lang] = question_counts_by_lang.get(lang, 0) + len(data)
            except Exception:
                pass

    if "en" in question_counts_by_lang:
        question_count = question_counts_by_lang["en"]
    elif question_counts_by_lang:
        question_count = max(question_counts_by_lang.values())
    else:
        question_count = 0

    return {
        "domains": len(inner.get("domains", []) or []),
        "campaigns": len(inner.get("campaigns", []) or []),
        "articles": len(article_paths),
        "questions": question_count,
        "languages": len(inner.get("languages", []) or []),
    }

def _index_entry(qap: Path) -> dict:
    base = {
        "file": qap.name,
        "size": qap.stat().st_size,
        "sha256": sha256_of(qap),
    }
    try:
        with zipfile.ZipFile(qap) as z:
            with z.open("manifest.json") as f:
                inner = json.loads(f.read().decode("utf-8"))
            totals = _totals(z, inner)
    except Exception as e:
        base["error"] = f"failed to read manifest: {type(e).__name__}: {e}"
        return base

    base.update({
        "id": inner.get("id") or qap.stem,
        "version": inner.get("version", ""),
        "kind": inner.get("kind", "campaign"),
        "createdAt": inner.get("createdAt", ""),
        "languages": list(inner.get("languages", []) or []),
        "campaigns": [c.get("slug", "") for c in inner.get("campaigns", []) or []],
        "domains": [d.get("slug", "") for d in inner.get("domains", []) or []],
        "totals": totals,
    })
    return base

def build_index() -> dict:
    if not QAPS_DIR.is_dir():
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "packs": [],
            "error": f"qaps directory not found: {QAPS_DIR}",
        }
    packs = [_index_entry(p) for p in sorted(QAPS_DIR.glob("*.qap"))]
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "packs": packs,
    }

def main() -> int:
    index = build_index()
    QAPS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    QAPS_INDEX.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    n = len(index.get("packs", []))
    print(f"wrote {QAPS_INDEX} ({n} pack{'s' if n != 1 else ''})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
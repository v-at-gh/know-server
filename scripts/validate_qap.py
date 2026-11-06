#!/usr/bin/env python3
"""Server-side QAP validation (format v2). Exit code 0 = valid, 1 = problems."""
from __future__ import annotations
import argparse, hashlib, json, re, sys, zipfile
from pathlib import Path

SUPPORTED_FORMATS = {2}
SUPPORTED_KINDS = {"campaign", "bundle", "translation"}
REQUIRED_TOP_FIELDS = {"qapFormatVersion", "id", "version", "kind",
                       "createdAt", "languages", "domains", "campaigns", "checksums"}
REQUIRED_DOMAIN_FIELDS = {"slug", "name", "description", "languages"}
REQUIRED_CAMPAIGN_FIELDS = {"slug", "order", "name", "description", "domainSlugs"}
REQUIRED_Q_FIELDS = {"uuid", "text", "answer", "distractors", "hints", "difficulty"}
DISTRACTOR_WHY_FORBIDDEN = re.compile(
    r"\b(the article|the text|as mentioned|as stated|according to (the|this)|see above)\b",
    re.I,
)


def sha256_of_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def check_top_level(manifest: dict, problems: list[str]) -> None:
    version = manifest.get("qapFormatVersion")
    if version not in SUPPORTED_FORMATS:
        problems.append(f"unsupported qapFormatVersion: {version}")
    for field in REQUIRED_TOP_FIELDS:
        if field not in manifest:
            problems.append(f"manifest missing field: {field}")
    for field in ("id", "version", "createdAt"):
        v = manifest.get(field)
        if not isinstance(v, str) or not v.strip():
            problems.append(f"manifest.{field} must be a non-blank string")
    kind = manifest.get("kind")
    if kind not in SUPPORTED_KINDS:
        problems.append(f"unsupported kind: {kind!r}; expected one of {sorted(SUPPORTED_KINDS)}")


def check_domains(manifest: dict, problems: list[str]) -> None:
    domains = manifest.get("domains")
    if not isinstance(domains, list):
        problems.append("manifest.domains must be a list")
        return
    seen = set()
    for i, d in enumerate(domains):
        if not isinstance(d, dict):
            problems.append(f"domains[{i}] must be an object")
            continue
        missing = REQUIRED_DOMAIN_FIELDS - set(d.keys())
        if missing:
            problems.append(f"domains[{i}]: missing {sorted(missing)}")
        slug = d.get("slug")
        if not slug:
            problems.append(f"domains[{i}]: blank slug")
            continue
        if slug in seen:
            problems.append(f"domains[{i}]: duplicate slug {slug}")
        seen.add(slug)
        if not isinstance(d.get("name"), dict) or not d["name"]:
            problems.append(f"domains[{i}] ({slug}): name must be a non-empty dict")
        if not isinstance(d.get("description"), dict):
            problems.append(f"domains[{i}] ({slug}): description must be a dict")
        langs = d.get("languages")
        if not isinstance(langs, list) or not langs:
            problems.append(f"domains[{i}] ({slug}): languages must be a non-empty list")
        else:
            for lang in langs:
                if lang not in (d.get("name") or {}):
                    problems.append(f"domains[{i}] ({slug}): language {lang} has no name")


def check_campaigns(manifest: dict, problems: list[str]) -> None:
    campaigns = manifest.get("campaigns")
    if not isinstance(campaigns, list):
        problems.append("manifest.campaigns must be a list")
        return
    domain_slugs = {d.get("slug") for d in manifest.get("domains", []) if isinstance(d, dict)}
    seen = set()
    for i, c in enumerate(campaigns):
        if not isinstance(c, dict):
            problems.append(f"campaigns[{i}] must be an object")
            continue
        missing = REQUIRED_CAMPAIGN_FIELDS - set(c.keys())
        if missing:
            problems.append(f"campaigns[{i}]: missing {sorted(missing)}")
        slug = c.get("slug")
        if not slug:
            problems.append(f"campaigns[{i}]: blank slug")
            continue
        if slug in seen:
            problems.append(f"campaigns[{i}]: duplicate slug {slug}")
        seen.add(slug)
        if not isinstance(c.get("name"), dict) or not c["name"]:
            problems.append(f"campaigns[{i}] ({slug}): name must be a non-empty dict")
        if not isinstance(c.get("description"), dict):
            problems.append(f"campaigns[{i}] ({slug}): description must be a dict")
        for ds in c.get("domainSlugs", []):
            if ds not in domain_slugs:
                problems.append(f"campaigns[{i}] ({slug}): unknown domain {ds}")


def check_files_and_checksums(z: zipfile.ZipFile, manifest: dict, problems: list[str]) -> None:
    declared = set(manifest.get("checksums", {}).keys())
    actual = {n for n in z.namelist() if n != "manifest.json"}
    for missing in sorted(declared - actual):
        problems.append(f"declared in checksums but missing from archive: {missing}")
    for orphan in sorted(actual - declared):
        problems.append(f"in archive but not in checksums: {orphan}")

    for name, expected in manifest.get("checksums", {}).items():
        try:
            data = z.read(name)
        except KeyError:
            continue
        if sha256_of_bytes(data) != expected:
            problems.append(f"checksum mismatch: {name}")


def check_domain_files(z: zipfile.ZipFile, manifest: dict, problems: list[str]) -> None:
    files = set(z.namelist())
    declared_pairs = set()
    for d in manifest.get("domains", []):
        if not isinstance(d, dict):
            continue
        slug = d.get("slug")
        for lang in d.get("languages", []):
            declared_pairs.add((lang, slug))
            path = f"datasets/{lang}/{slug}.json"
            if path not in files:
                problems.append(f"declared domain {slug}/{lang}: missing {path}")

    for name in files:
        m = re.match(r"^datasets/([^/]+)/([^/]+)\.json$", name)
        if not m:
            continue
        lang, domain = m.groups()
        if domain in ("domains", "campaigns"):
            continue
        if (lang, domain) not in declared_pairs:
            problems.append(f"{name}: undeclared (not in manifest.domains)")


def check_questions(z: zipfile.ZipFile, manifest: dict, problems: list[str]) -> None:
    for name in manifest.get("checksums", {}):
        m = re.match(r"^datasets/([^/]+)/([^/]+)\.json$", name)
        if not m:
            continue
        _, domain = m.groups()
        if domain in ("domains", "campaigns"):
            continue
        try:
            arr = json.loads(z.read(name))
        except Exception as e:
            problems.append(f"{name}: not valid JSON ({e})")
            continue
        if not isinstance(arr, list):
            problems.append(f"{name}: top-level is not an array")
            continue
        seen_uuids: set[str] = set()
        for i, q in enumerate(arr):
            prefix = f"{name}[{i}]"
            missing = REQUIRED_Q_FIELDS - set(q.keys())
            if missing:
                problems.append(f"{prefix}: missing fields {missing}")
                continue
            if q["uuid"] in seen_uuids:
                problems.append(f"{prefix}: duplicate uuid {q['uuid']}")
            seen_uuids.add(q["uuid"])
            dtexts = [d["text"] for d in q["distractors"]]
            if not (2 <= len(dtexts) <= 6):
                problems.append(f"{prefix}: {len(dtexts)} distractors (need 2..6)")
            if q["answer"] in dtexts:
                problems.append(f"{prefix}: answer among distractors")
            if len(set(dtexts)) != len(dtexts):
                problems.append(f"{prefix}: duplicate distractors")
            for d in q["distractors"]:
                if d.get("why") and DISTRACTOR_WHY_FORBIDDEN.search(d["why"]):
                    problems.append(f"{prefix}: why references article: {d['why'][:40]!r}")


def check_articles(z: zipfile.ZipFile, manifest: dict, problems: list[str]) -> None:
    files = set(z.namelist())
    for lang in manifest.get("languages", []):
        idx_path = f"knowledge/{lang}/index.json"
        if idx_path not in files:
            continue
        try:
            idx = json.loads(z.read(idx_path))
        except Exception as e:
            problems.append(f"{idx_path}: not valid JSON ({e})")
            continue
        for entry in idx:
            path = f"knowledge/{lang}/{entry['path']}"
            if path not in files:
                problems.append(f"{idx_path}: references missing file {path}")


def check_cross_language(z: zipfile.ZipFile, manifest: dict, problems: list[str]) -> None:
    # Translation packs carry a single target language per domain and are
    # applied on top of an already-installed source; UUID parity with EN is
    # checked against the device's existing content, not against the pack.
    if manifest.get("kind") == "translation":
        return
    if "en" not in manifest.get("languages", []):
        return
    for d in manifest.get("domains", []):
        if not isinstance(d, dict):
            continue
        slug = d.get("slug")
        en_path = f"datasets/en/{slug}.json"
        if en_path not in z.namelist():
            continue
        try:
            src = json.loads(z.read(en_path))
        except Exception:
            continue
        src_uuids = {q["uuid"] for q in src}
        for lang in d.get("languages", []):
            if lang == "en":
                continue
            lang_path = f"datasets/{lang}/{slug}.json"
            try:
                dst = json.loads(z.read(lang_path))
            except KeyError:
                problems.append(f"{lang_path}: missing")
                continue
            missing = src_uuids - {q["uuid"] for q in dst}
            if missing:
                problems.append(f"{lang_path}: {len(missing)} EN UUIDs missing")


def validate(path: Path) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as z:
        try:
            manifest = json.loads(z.read("manifest.json"))
        except KeyError:
            return ["manifest.json not found in archive"]
        except Exception as e:
            return [f"manifest.json is not valid JSON: {e}"]

        check_top_level(manifest, problems)
        if manifest.get("qapFormatVersion") not in SUPPORTED_FORMATS:
            return problems

        check_domains(manifest, problems)
        check_campaigns(manifest, problems)
        check_files_and_checksums(z, manifest, problems)
        check_domain_files(z, manifest, problems)
        check_questions(z, manifest, problems)
        check_articles(z, manifest, problems)
        check_cross_language(z, manifest, problems)
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("qap", type=Path)
    args = ap.parse_args()
    if not args.qap.exists():
        sys.exit(f"not found: {args.qap}")
    problems = validate(args.qap)
    if problems:
        print(f"FAIL: {len(problems)} problems")
        for p in problems[:50]:
            print(f"  {p}")
        sys.exit(1)
    print(f"OK: {args.qap.name} is valid")


if __name__ == "__main__":
    main()
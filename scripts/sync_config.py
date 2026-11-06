#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from web.db import get_app_db, init_app_db
from quizkb.config import DOMAINS as DEFAULT_DOMAINS, CAMPAIGNS as DEFAULT_CAMPAIGNS

CONFIG_DIR = REPO_ROOT / "state" / "config"
DOMAINS_JSON = CONFIG_DIR / "domains.json"
CAMPAIGNS_JSON = CONFIG_DIR / "campaigns.json"

def export_to_json():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with get_app_db() as conn:
        domains = [dict(r) for r in conn.execute("SELECT * FROM domains ORDER BY slug")]
        campaigns = []
        for row in conn.execute("SELECT * FROM campaigns ORDER BY \"order\", slug"):
            c = dict(row)
            c["prerequisiteDomainSlugs"] = json.loads(c.pop("prerequisite_domain_slugs"))
            c["domainSlugs"] = [r["domain_slug"] for r in conn.execute(
                "SELECT domain_slug FROM campaign_domains WHERE campaign_slug = ? ORDER BY position", (c["slug"],)
            )]
            campaigns.append(c)

    DOMAINS_JSON.write_text(json.dumps(domains, indent=2, ensure_ascii=False), encoding="utf-8")
    CAMPAIGNS_JSON.write_text(json.dumps(campaigns, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported {len(domains)} domains, {len(campaigns)} campaigns to {CONFIG_DIR}")

def import_from_json():
    init_app_db()
    with get_app_db() as conn:
        # Если БД пуста, заполняем её
        if conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0] == 0:
            domains_data = []
            campaigns_data = []

            if DOMAINS_JSON.exists() and CAMPAIGNS_JSON.exists():
                domains_data = json.loads(DOMAINS_JSON.read_text(encoding="utf-8"))
                campaigns_data = json.loads(CAMPAIGNS_JSON.read_text(encoding="utf-8"))
            else:
                # Fallback на хардкод из config.py
                domains_data = DEFAULT_DOMAINS
                campaigns_data = DEFAULT_CAMPAIGNS

            for d in domains_data:
                conn.execute(
                    "INSERT INTO domains (slug, name, description, kind) VALUES (?, ?, ?, ?)",
                    (d["slug"], d["name"], d["description"], d.get("kind", "unknown"))
                )

            for c in campaigns_data:
                prereqs = json.dumps(c.get("prerequisiteDomainSlugs", []))
                conn.execute(
                    "INSERT INTO campaigns (slug, name, description, \"order\", prerequisite_domain_slugs) VALUES (?, ?, ?, ?, ?)",
                    (c["slug"], c["name"], c["description"], c.get("order", 0), prereqs)
                )
                for pos, ds in enumerate(c.get("domainSlugs", [])):
                    conn.execute(
                        "INSERT INTO campaign_domains (campaign_slug, domain_slug, position) VALUES (?, ?, ?)",
                        (c["slug"], ds, pos)
                    )
            conn.commit()
            print(f"Initialized DB with {len(domains_data)} domains, {len(campaigns_data)} campaigns.")
            export_to_json()
        else:
            print("DB already contains data. Skipping import.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["import", "export"], required=True)
    args = ap.parse_args()

    if args.action == "import":
        import_from_json()
    else:
        export_to_json()
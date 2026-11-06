#!/usr/bin/env python3
# DEPRECATED: use `know users bootstrap --username X --password Y` instead.
# Kept for backwards compatibility; same behavior.
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from web.auth import init_db, create_user
from quizkb.config import DOMAINS, SOURCE_LANG, TARGET_LANGS

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("username", help="Username for the admin panel")
    ap.add_argument("password", help="Password")
    args = ap.parse_args()

    init_db()

    all_domains = [d["slug"] for d in DOMAINS]
    all_langs = [SOURCE_LANG] + list(TARGET_LANGS)

    if create_user(args.username, args.password, all_domains, all_langs):
        print(f"User '{args.username}' created successfully.")
    else:
        print(f"Error: User '{args.username}' already exists.")

if __name__ == "__main__":
    main()
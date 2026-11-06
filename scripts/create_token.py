#!/usr/bin/env python3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from web import auth

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_token.py <username>")
        sys.exit(1)

    username = sys.argv[1]
    auth.init_db()
    with auth.get_db() as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not user:
            print(f"User {username} not found.")
            sys.exit(1)

        token = auth.create_api_token(user["id"])
        print(f"Created API token for {username}:")
        print(token)

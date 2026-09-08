"""Refresh the latest web punishment authors from Discord server nicknames.

This is a one-time data maintenance command.  It defaults to the 30 latest
punishments linked to an admin and never touches game-server-created rows.

Usage:
    python scripts/refresh_recent_punishment_issuers.py
    python scripts/refresh_recent_punishment_issuers.py --limit 30
"""

from __future__ import annotations

import argparse

from database import SessionLocal
from services import refresh_recent_punishment_issuers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        updated = refresh_recent_punishment_issuers(db, limit=args.limit)
        print(f"updated punishment issuers: {updated}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

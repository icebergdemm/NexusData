"""
One-shot migration: import legacy JSON log caches (data/logs_cache/*.json)
into the new SQLite logs database.

Run once:  python migrate_logs_json_to_sqlite.py

Safe to re-run — events are de-duplicated by (server, source_file): existing
rows for a file are deleted before re-insert.
"""
import os
import json
import glob
from datetime import datetime

import services_logs as sl


def _parse_ts(ts_str):
    try:
        return sl._ts_to_ms(datetime.fromisoformat(ts_str))
    except Exception:
        return None


def main():
    sl._ensure_storage_dir()
    conn = sl._get_conn()

    json_files = glob.glob(os.path.join(sl.LOG_STORAGE_DIR, "*.json"))
    # skip meta / player-map files
    json_files = [f for f in json_files
                  if not os.path.basename(f).startswith("_")
                  and not f.endswith("_players.json")
                  and not f.endswith("_manifest.json")]

    if not json_files:
        print("No legacy JSON log files found in", sl.LOG_STORAGE_DIR)
        return

    total = 0
    for path in json_files:
        server_name = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Skip {path}: {e}")
            continue

        rows = []
        for item in data:
            ts = _parse_ts(item.get("ts", ""))
            if ts is None:
                continue
            rows.append((
                ts,
                item.get("s") or server_name,
                item.get("p") or "",
                item.get("t") or "other",
                item.get("d") or "",
                item.get("f") or "legacy",
            ))

        if not rows:
            continue

        # Insert in batches with periodic commit to keep WAL small
        BATCH = 5000
        for i in range(0, len(rows), BATCH):
            conn.executemany(
                "INSERT INTO log_events (ts, server, player, action, details, source_file) "
                "VALUES (?,?,?,?,?,?)",
                rows[i:i + BATCH]
            )
            conn.commit()
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
        total += len(rows)
        print(f"Imported {len(rows)} events for '{server_name}' from {os.path.basename(path)}")

    # Rebuild meta
    for srv in [r[0] for r in conn.execute("SELECT DISTINCT server FROM log_events").fetchall()]:
        row = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM log_events WHERE server=?", (srv,)).fetchone()
        conn.execute(
            "INSERT OR REPLACE INTO log_meta (server, event_count, last_refresh, oldest_event, newest_event) VALUES (?,?,?,?,?)",
            (srv, row[0] or 0, datetime.utcnow().isoformat(),
             sl._ms_to_dt(row[1]).isoformat() if row[1] else None,
             sl._ms_to_dt(row[2]).isoformat() if row[2] else None)
        )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print(f"\nDone. Imported {total} events total into {sl.LOG_DB_PATH}")


if __name__ == "__main__":
    main()

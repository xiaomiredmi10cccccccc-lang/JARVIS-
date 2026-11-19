"""Puts a backup made by /admin/backup back into an (empty) database.

Use:  DATABASE_URL="postgresql://..." python restore_backup.py backup.json.gz
Start the server once first, so that it creates the tables. Rows that already exist are kept as they are.
"""
import gzip
import json
import os
import sys

import psycopg
from psycopg import sql


def main(path: str) -> None:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
        for table in ("users", "chats"):
            part = data["tables"][table]
            columns = part["columns"]
            query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                sql.Identifier(table),
                sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            )
            for row in part["rows"]:
                conn.execute(query, row)
            print(table + ":", len(part["rows"]), "rows")
        conn.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE((SELECT MAX(id) FROM users), 1))")
    print("Done")


if __name__ == "__main__":
    if len(sys.argv) != 2 or "DATABASE_URL" not in os.environ:
        sys.exit("Usage: DATABASE_URL=... python restore_backup.py backup.json.gz")
    main(sys.argv[1])

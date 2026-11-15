"""Command-line tool: export the users table as CSV to standard output."""
import argparse
import csv
import sys

from app.users import get_connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Export users as CSV")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    cur = get_connection().cursor()
    cur.execute("SELECT id, name, email FROM users LIMIT ?", (args.limit,))
    writer = csv.writer(sys.stdout)
    rows = cur.fetchall()
    writer.writerows(rows)
    print(f"Exported {len(rows)} users.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

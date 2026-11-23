"""One-off: copy an old local Friction Finder store (data/friction.sqlite) and its export files into Postgres.

    python scripts/migrate_sqlite_to_postgres.py [path/to/friction.sqlite] [--replace]

Needs ANALYST_DATABASE_URL in .env. Refuses to run when the Postgres store already has findings, unless
--replace is given (which empties the Postgres store first). The SQLite file is only read, never changed.
The analytics copy is not migrated: run a sync instead (python -m bookr_analyst.sync).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookr_analyst.config import PROJECT_ROOT, settings  # noqa: E402
from bookr_analyst.friction.store import conn, now_iso  # noqa: E402

TABLES = ["runs", "findings", "items", "exports", "issues", "briefs"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    replace = "--replace" in sys.argv
    src_path = Path(args[0]) if args else PROJECT_ROOT / "data" / "friction.sqlite"
    if not src_path.is_file():
        sys.exit(f"{src_path} not found")
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    with conn() as c:
        existing = sum(c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES)
        if existing and not replace:
            sys.exit(f"The Postgres store already holds {existing} rows. Re-run with --replace to overwrite it.")
        for t in TABLES + ["export_files"]:
            c.execute(f"DELETE FROM {t}")
        for t in TABLES:
            have = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
            if not have:
                print(f"  {t:10s} not in the SQLite file, skipped")
                continue
            want = {r[0] for r in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'friction' AND table_name = ?", (t,))}
            cols = [x for x in have if x in want]
            quoted = ", ".join(f'"{x}"' for x in cols)
            data = src.execute(f"SELECT {quoted} FROM {t}").fetchall()
            with c.pg.cursor().copy(f"COPY {t} ({quoted}) FROM STDIN") as cp:
                for row in data:
                    cp.write_row(row)
            # continue the id sequence after the copied rows
            c.execute(f"SELECT setval(pg_get_serial_sequence('friction.{t}', 'id'), (SELECT coalesce(max(id), 0) + 1 FROM {t}), false)")
            print(f"  {t:10s} {len(data):>7,} rows")
        n = 0
        if settings.exports_dir.is_dir():
            for f in sorted(settings.exports_dir.iterdir()):
                if f.suffix in (".jsonl", ".md"):
                    c.execute("INSERT INTO export_files (name, content, created_at) VALUES (?, ?, ?)",
                              (f.name, f.read_bytes().decode("utf-8"), now_iso()))
                    n += 1
        print(f"  export files {n:>5,}")
    print("Done.")


if __name__ == "__main__":
    main()

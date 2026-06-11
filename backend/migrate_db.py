"""One-time migration: copy all data from SQLite to PostgreSQL."""
import sqlite3
import json
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from backend.models import Base

load_dotenv()
PG_URL = os.getenv("DATABASE_URL")
SQLITE_PATH = "Noah_Events.db"

def main():
    print(f"Migrating from SQLite ({SQLITE_PATH}) to PostgreSQL...")

    pg_engine = create_engine(PG_URL)
    with pg_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    Base.metadata.create_all(bind=pg_engine)
    print("  PostgreSQL tables created")

    sl = sqlite3.connect(SQLITE_PATH)
    sl.row_factory = sqlite3.Row
    pg = pg_engine.connect()

    for table, id_col in [("users", "id"), ("events", "id"), ("bookings", "id")]:
        rows = sl.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: 0 rows (no data)")
            continue

        columns = list(rows[0].keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        col_list = ", ".join(columns)

        count = 0
        for row in rows:
            data = dict(row)
            for k, v in data.items():
                if isinstance(v, str):
                    try:
                        parsed = json.loads(v)
                        if isinstance(parsed, (list, dict)):
                            data[k] = json.dumps(parsed)
                    except (json.JSONDecodeError, TypeError):
                        pass
            pg.execute(
                text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT ({id_col}) DO NOTHING"),
                data
            )
            count += 1

        pg.commit()
        print(f"  {table}: {count} rows migrated")

    sl.close()
    pg.close()
    print("Done.")

if __name__ == "__main__":
    main()

"""Aplica supabase/schema.sql no banco apontado por DATABASE_URL."""
import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

SCHEMA = pathlib.Path(__file__).with_name("schema.sql")


def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL não definida no .env (Supabase > Connect).")
    with psycopg.connect(url) as conn:
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
    print("Schema aplicado.")


if __name__ == "__main__":
    main()

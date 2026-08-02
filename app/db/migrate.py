"""Minimal forward-only migration runner.

Applies every unapplied `migrations/*.sql` file in filename order and records it
in `schema_migrations`, so running it repeatedly is a no-op. Called from the
FastAPI lifespan; also runnable directly:

    python -m app.db.migrate
"""

import logging
from pathlib import Path

from app.db.session import engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def run_migrations() -> list[str]:
    """Apply pending migrations. Returns the filenames that were applied."""
    with engine.begin() as conn:
        conn.exec_driver_sql(_TRACKING_TABLE)
        already = {row[0] for row in conn.exec_driver_sql("SELECT filename FROM schema_migrations")}

    applied: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in already:
            continue
        # One transaction per file: a failure rolls that migration back whole
        # and leaves it unrecorded, so the next start retries it.
        with engine.begin() as conn:
            conn.exec_driver_sql(path.read_text())
            conn.exec_driver_sql(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )
        logger.info("applied migration %s", path.name)
        applied.append(path.name)

    return applied


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    done = run_migrations()
    print(f"applied {len(done)} migration(s): {', '.join(done) or 'none pending'}")

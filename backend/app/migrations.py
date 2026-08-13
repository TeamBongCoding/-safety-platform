"""Schema migrations for both SQLite (dev/test) and PostgreSQL (Supabase).

Strategy
--------
- For SQLite: use ALTER TABLE to safely add missing columns to existing tables.
  SQLite does not support DROP COLUMN on older versions so we only add.
- For PostgreSQL/Supabase: Base.metadata.create_all() handles new tables.
  Column additions on existing tables are done via IF NOT EXISTS in raw SQL.
- New tables (event_episodes, exposure_hourly, …) are always created by
  create_all(); no extra work needed here.
"""

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def migrate_legacy_schema(engine) -> None:
    """Create missing columns on pre-existing tables without losing data."""
    if engine.dialect.name == "sqlite":
        _migrate_sqlite(engine)
    else:
        _migrate_postgresql(engine)


# ── SQLite ────────────────────────────────────────────────────────────────────

def _migrate_sqlite(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    alterations = {
        "users": [
            ("role",           "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"),
            ("status",         "ALTER TABLE users ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'"),
            ("last_login_at",  "ALTER TABLE users ADD COLUMN last_login_at DATETIME"),
            ("suspended_at",   "ALTER TABLE users ADD COLUMN suspended_at DATETIME"),
        ],
        "sites": [
            ("latitude",  "ALTER TABLE sites ADD COLUMN latitude FLOAT"),
            ("longitude", "ALTER TABLE sites ADD COLUMN longitude FLOAT"),
        ],
        "zones": [
            ("site_id",     "ALTER TABLE zones ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
            ("risk_level",  "ALTER TABLE zones ADD COLUMN risk_level VARCHAR(20) NOT NULL DEFAULT 'high'"),
            ("description", "ALTER TABLE zones ADD COLUMN description TEXT NOT NULL DEFAULT ''"),
            ("precautions", "ALTER TABLE zones ADD COLUMN precautions TEXT NOT NULL DEFAULT ''"),
            ("visible",     "ALTER TABLE zones ADD COLUMN visible BOOLEAN NOT NULL DEFAULT 1"),
            ("updated_at",  "ALTER TABLE zones ADD COLUMN updated_at DATETIME"),
        ],
        "events": [
            ("site_id",  "ALTER TABLE events ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
            ("track_id", "ALTER TABLE events ADD COLUMN track_id VARCHAR(50)"),
        ],
    }

    with engine.begin() as conn:
        for table_name, columns in alterations.items():
            if table_name not in table_names:
                continue
            existing = {col["name"] for col in inspector.get_columns(table_name)}
            for column_name, statement in columns:
                if column_name not in existing:
                    conn.exec_driver_sql(statement)
                    logger.info("SQLite migration: added %s.%s", table_name, column_name)

        # Indexes
        if "zones" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_zones_site_id ON zones (site_id)"
            )
        if "events" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_events_site_id ON events (site_id)"
            )
        if "users" in table_names:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_role ON users (role)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_status ON users (status)"
            )
        conn.exec_driver_sql("PRAGMA optimize")


# ── PostgreSQL / Supabase ─────────────────────────────────────────────────────

def _migrate_postgresql(engine) -> None:
    """Add columns that may be missing on an existing PostgreSQL schema.

    New tables are handled by create_all(). This function only deals with
    ADD COLUMN IF NOT EXISTS for existing tables so deployments are idempotent.
    """
    alterations = {
        "users": [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMP",
        ],
        "sites": [
            "ALTER TABLE sites ADD COLUMN IF NOT EXISTS latitude FLOAT",
            "ALTER TABLE sites ADD COLUMN IF NOT EXISTS longitude FLOAT",
        ],
        "zones": [
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id)",
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS risk_level VARCHAR(20) NOT NULL DEFAULT 'high'",
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS precautions TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS visible BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        ],
        "events": [
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id)",
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS track_id VARCHAR(50)",
        ],
    }

    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        existing_tables = set()

    if not callable(getattr(engine, "begin", None)):
        return  # 단위 테스트에서 SimpleNamespace 등 mock 엔진이 올 경우 조용히 종료

    with engine.begin() as conn:
        # Enable pgvector if available
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("PostgreSQL: pgvector extension ensured")
        except Exception as exc:
            logger.warning("Could not ensure pgvector extension: %s", exc)

        for table_name, stmts in alterations.items():
            if table_name not in existing_tables:
                continue
            for stmt in stmts:
                try:
                    conn.execute(text(stmt))
                except Exception as exc:
                    logger.warning("Migration stmt skipped (%s): %s", exc, stmt[:60])

        # Indexes (idempotent)
        index_stmts = [
            "CREATE INDEX IF NOT EXISTS ix_zones_site_id ON zones (site_id)",
            "CREATE INDEX IF NOT EXISTS ix_events_site_id ON events (site_id)",
            "CREATE INDEX IF NOT EXISTS ix_users_role ON users (role)",
            "CREATE INDEX IF NOT EXISTS ix_users_status ON users (status)",
            "CREATE INDEX IF NOT EXISTS ix_event_episodes_site_id ON event_episodes (site_id)",
            "CREATE INDEX IF NOT EXISTS ix_event_episodes_event_type ON event_episodes (event_type)",
            "CREATE INDEX IF NOT EXISTS ix_exposure_hourly_site_id ON exposure_hourly (site_id)",
            "CREATE INDEX IF NOT EXISTS ix_risk_predictions_site_id ON risk_predictions (site_id)",
            "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_site_id ON knowledge_documents (site_id)",
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_document_id ON document_chunks (document_id)",
        ]
        for stmt in index_stmts:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass

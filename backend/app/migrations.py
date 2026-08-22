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
            ("is_ephemeral",    "ALTER TABLE users ADD COLUMN is_ephemeral BOOLEAN NOT NULL DEFAULT 0"),
            ("last_seen_at",    "ALTER TABLE users ADD COLUMN last_seen_at DATETIME"),
            ("expires_at",      "ALTER TABLE users ADD COLUMN expires_at DATETIME"),
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
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_is_ephemeral ON users (is_ephemeral)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_expires_at ON users (expires_at)"
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
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_ephemeral BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE",
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
            "ALTER TABLE zones ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE",
        ],
        "events": [
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id)",
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS track_id VARCHAR(50)",
        ],
    }

    if not callable(getattr(engine, "begin", None)):
        return  # 단위 테스트에서 SimpleNamespace 등 mock 엔진이 올 경우 조용히 종료

    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        existing_columns = {
            table_name: {column["name"] for column in inspector.get_columns(table_name)}
            for table_name in existing_tables
        }
        existing_indexes = {
            table_name: {
                index["name"]
                for index in inspector.get_indexes(table_name)
                if index.get("name")
            }
            for table_name in existing_tables
        }
    except Exception as exc:
        logger.warning("PostgreSQL schema inspection skipped: %s", exc)
        return

    def execute_ddl(statement: str, description: str) -> None:
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '3s'"))
                conn.execute(text("SET LOCAL statement_timeout = '15s'"))
                conn.execute(text(statement))
        except Exception as exc:
            logger.warning("PostgreSQL migration skipped (%s): %s", description, exc)

    # 설치된 확장을 매번 다시 생성하려 하면 불필요한 DDL 잠금이 발생할 수 있다.
    try:
        with engine.connect() as conn:
            vector_exists = bool(conn.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )))
    except Exception as exc:
        logger.warning("Could not inspect pgvector extension: %s", exc)
        vector_exists = True
    if not vector_exists:
        execute_ddl("CREATE EXTENSION IF NOT EXISTS vector", "pgvector extension")

    # Normalize legacy naive timestamps to timezone-aware UTC. Existing rows are
    # interpreted according to the historical convention used by each column.
    timestamp_columns = {
        "users": {"last_login_at": "UTC", "suspended_at": "UTC", "last_seen_at": "UTC", "expires_at": "UTC", "created_at": "UTC"},
        "sites": {"created_at": "UTC"},
        "login_sessions": {"expires_at": "UTC", "created_at": "UTC"},
        "admin_audit_logs": {"created_at": "UTC"},
        "zones": {"updated_at": "UTC"},
        "events": {"timestamp": "Asia/Seoul"},
        "event_episodes": {"started_at": "UTC", "ended_at": "UTC", "created_at": "UTC", "updated_at": "UTC"},
        "risk_predictions": {"generated_at": "UTC", "valid_until": "UTC"},
        "knowledge_documents": {"created_at": "UTC"},
        "document_chunks": {"created_at": "UTC"},
    }
    try:
        with engine.begin() as conn:
            for table_name, columns in timestamp_columns.items():
                if table_name not in existing_tables:
                    continue
                for column_name, timezone_name in columns.items():
                    if column_name not in existing_columns.get(table_name, set()):
                        continue
                    row = conn.execute(text("""
                        SELECT data_type FROM information_schema.columns
                        WHERE table_schema = current_schema() AND table_name = :table_name
                          AND column_name = :column_name
                    """), {"table_name": table_name, "column_name": column_name}).first()
                    if row and row[0] == "timestamp without time zone":
                        conn.execute(text(
                            f'ALTER TABLE "{table_name}" ALTER COLUMN "{column_name}" '
                            f"TYPE TIMESTAMP WITH TIME ZONE USING \"{column_name}\" AT TIME ZONE '{timezone_name}'"
                        ))
                        logger.info("PostgreSQL migration: normalized %s.%s as %s", table_name, column_name, timezone_name)
    except Exception as exc:
        logger.warning("PostgreSQL timestamp normalization skipped: %s", exc)

    for table_name, stmts in alterations.items():
        if table_name not in existing_tables:
            continue
        for stmt in stmts:
            column_name = stmt.split()[8]
            if column_name not in existing_columns.get(table_name, set()):
                execute_ddl(stmt, f"{table_name}.{column_name}")

    index_stmts = [
        ("zones", "ix_zones_site_id", "CREATE INDEX IF NOT EXISTS ix_zones_site_id ON zones (site_id)"),
        ("events", "ix_events_site_id", "CREATE INDEX IF NOT EXISTS ix_events_site_id ON events (site_id)"),
        ("users", "ix_users_role", "CREATE INDEX IF NOT EXISTS ix_users_role ON users (role)"),
        ("users", "ix_users_status", "CREATE INDEX IF NOT EXISTS ix_users_status ON users (status)"),
        ("event_episodes", "ix_event_episodes_site_id", "CREATE INDEX IF NOT EXISTS ix_event_episodes_site_id ON event_episodes (site_id)"),
        ("event_episodes", "ix_event_episodes_event_type", "CREATE INDEX IF NOT EXISTS ix_event_episodes_event_type ON event_episodes (event_type)"),
        ("exposure_hourly", "ix_exposure_hourly_site_id", "CREATE INDEX IF NOT EXISTS ix_exposure_hourly_site_id ON exposure_hourly (site_id)"),
        ("risk_predictions", "ix_risk_predictions_site_id", "CREATE INDEX IF NOT EXISTS ix_risk_predictions_site_id ON risk_predictions (site_id)"),
        ("knowledge_documents", "ix_knowledge_documents_site_id", "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_site_id ON knowledge_documents (site_id)"),
        ("users", "ix_users_is_ephemeral", "CREATE INDEX IF NOT EXISTS ix_users_is_ephemeral ON users (is_ephemeral)"),
        ("users", "ix_users_expires_at", "CREATE INDEX IF NOT EXISTS ix_users_expires_at ON users (expires_at)"),
        ("document_chunks", "ix_document_chunks_document_id", "CREATE INDEX IF NOT EXISTS ix_document_chunks_document_id ON document_chunks (document_id)"),
    ]
    for table_name, index_name, stmt in index_stmts:
        if (
            table_name in existing_tables
            and index_name not in existing_indexes.get(table_name, set())
        ):
            execute_ddl(stmt, index_name)

from sqlalchemy import inspect


def migrate_legacy_schema(engine) -> None:
    """create_all이 추가하지 못하는 기존 SQLite 컬럼을 안전하게 보강한다."""
    dialect = engine.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        return

    datetime_type = "DATETIME" if dialect == "sqlite" else "TIMESTAMP"
    false_literal = "0" if dialect == "sqlite" else "FALSE"
    true_literal = "1" if dialect == "sqlite" else "TRUE"

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    alterations = {
        "users": [
            ("role", "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"),
            ("status", "ALTER TABLE users ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'"),
            ("last_login_at", f"ALTER TABLE users ADD COLUMN last_login_at {datetime_type}"),
            ("suspended_at", f"ALTER TABLE users ADD COLUMN suspended_at {datetime_type}"),
        ],
        "sites": [
            ("is_outdoor", f"ALTER TABLE sites ADD COLUMN is_outdoor BOOLEAN NOT NULL DEFAULT {false_literal}"),
            ("latitude", "ALTER TABLE sites ADD COLUMN latitude FLOAT"),
            ("longitude", "ALTER TABLE sites ADD COLUMN longitude FLOAT"),
        ],
        "zones": [
            ("site_id", "ALTER TABLE zones ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
            ("risk_level", "ALTER TABLE zones ADD COLUMN risk_level VARCHAR(20) NOT NULL DEFAULT 'high'"),
            ("description", "ALTER TABLE zones ADD COLUMN description TEXT NOT NULL DEFAULT ''"),
            ("precautions", "ALTER TABLE zones ADD COLUMN precautions TEXT NOT NULL DEFAULT ''"),
            ("visible", f"ALTER TABLE zones ADD COLUMN visible BOOLEAN NOT NULL DEFAULT {true_literal}"),
            ("updated_at", f"ALTER TABLE zones ADD COLUMN updated_at {datetime_type}"),
        ],
        "events": [
            ("site_id", "ALTER TABLE events ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
            ("track_id", "ALTER TABLE events ADD COLUMN track_id VARCHAR(50)"),
        ],
    }

    with engine.begin() as connection:
        for table_name, columns in alterations.items():
            if table_name not in table_names:
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, statement in columns:
                if column_name not in existing:
                    connection.exec_driver_sql(statement)

        if "zones" in table_names:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_zones_site_id ON zones (site_id)"
            )
        if "events" in table_names:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_events_site_id ON events (site_id)"
            )
        if "users" in table_names:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_role ON users (role)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_users_status ON users (status)"
            )
        if dialect == "sqlite":
            connection.exec_driver_sql("PRAGMA optimize")

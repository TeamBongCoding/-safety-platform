from sqlalchemy import inspect


def migrate_legacy_schema(engine) -> None:
    """create_all이 추가하지 못하는 기존 SQLite 컬럼을 안전하게 보강한다."""
    # These statements and PRAGMA are only valid for databases created by
    # older local SQLite versions. PostgreSQL/Supabase is created from the
    # SQLAlchemy metadata in app.main instead.
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    alterations = {
        "users": [
            ("role", "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"),
            ("status", "ALTER TABLE users ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'"),
            ("last_login_at", "ALTER TABLE users ADD COLUMN last_login_at DATETIME"),
            ("suspended_at", "ALTER TABLE users ADD COLUMN suspended_at DATETIME"),
        ],
        "sites": [
            ("latitude", "ALTER TABLE sites ADD COLUMN latitude FLOAT"),
            ("longitude", "ALTER TABLE sites ADD COLUMN longitude FLOAT"),
        ],
        "cameras": [
            ("is_outdoor", "ALTER TABLE cameras ADD COLUMN is_outdoor BOOLEAN NOT NULL DEFAULT 0"),
        ],
        "zones": [
            ("site_id", "ALTER TABLE zones ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
            ("camera_id", "ALTER TABLE zones ADD COLUMN camera_id INTEGER REFERENCES cameras(id)"),
            ("risk_level", "ALTER TABLE zones ADD COLUMN risk_level VARCHAR(20) NOT NULL DEFAULT 'high'"),
            ("description", "ALTER TABLE zones ADD COLUMN description TEXT NOT NULL DEFAULT ''"),
            ("precautions", "ALTER TABLE zones ADD COLUMN precautions TEXT NOT NULL DEFAULT ''"),
            ("visible", "ALTER TABLE zones ADD COLUMN visible BOOLEAN NOT NULL DEFAULT 1"),
            ("updated_at", "ALTER TABLE zones ADD COLUMN updated_at DATETIME"),
        ],
        "events": [
            ("site_id", "ALTER TABLE events ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
            ("camera_id", "ALTER TABLE events ADD COLUMN camera_id INTEGER REFERENCES cameras(id)"),
            ("worker_id", "ALTER TABLE events ADD COLUMN worker_id INTEGER REFERENCES workers(id)"),
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
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_zones_site_camera_id ON zones (site_id, camera_id)"
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
        connection.exec_driver_sql("PRAGMA optimize")

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.config import normalize_database_url
from app.migrations import _migrate_postgresql, migrate_legacy_schema
from app.models import Zone


class DeploymentConfigurationTests(unittest.TestCase):
    def test_supabase_url_uses_psycopg_v3(self):
        self.assertEqual(
            normalize_database_url(
                "postgresql://postgres.example:secret@pooler.example.com/postgres"
            ),
            "postgresql+psycopg://postgres.example:secret@pooler.example.com/postgres",
        )

    def test_explicit_driver_and_sqlite_urls_are_preserved(self):
        for url in (
            "postgresql+psycopg://user:password@example.com/database",
            "sqlite:///./safety.db",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_database_url(url), url)

    def test_postgresql_skips_legacy_sqlite_migration(self):
        engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        migrate_legacy_schema(engine)
    @patch("app.migrations.inspect")
    def test_postgresql_existing_schema_skips_ddl(self, inspect_mock):
        inspector = inspect_mock.return_value
        inspector.get_table_names.return_value = ["users"]
        inspector.get_columns.return_value = [
            {"name": "role"},
            {"name": "status"},
            {"name": "last_login_at"},
            {"name": "suspended_at"},
        ]
        inspector.get_indexes.return_value = [
            {"name": "ix_users_role"},
            {"name": "ix_users_status"},
        ]
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.scalar.return_value = True

        _migrate_postgresql(engine)

        engine.begin.assert_not_called()


    def test_fresh_schema_contains_zone_updated_at(self):
        self.assertIn("updated_at", Zone.__table__.columns)


if __name__ == "__main__":
    unittest.main()

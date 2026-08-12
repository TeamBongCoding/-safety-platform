import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.config import normalize_database_url
from app.migrations import migrate_legacy_schema
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

    def test_postgresql_checks_existing_schema(self):
        engine = SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql"),
            begin=MagicMock(),
        )
        inspector = SimpleNamespace(get_table_names=lambda: [])
        with patch("app.migrations.inspect", return_value=inspector):
            migrate_legacy_schema(engine)

    def test_fresh_schema_contains_zone_updated_at(self):
        self.assertIn("updated_at", Zone.__table__.columns)


if __name__ == "__main__":
    unittest.main()

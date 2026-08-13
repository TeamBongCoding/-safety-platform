import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Site, User
from app.routers.sites import delete_site


class SiteDeletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _user_with_sites(self, count: int):
        db = self.Session()
        user = User(
            email="manager@example.com",
            password_hash="test",
            company_name="테스트 건설",
            manager_name="관리자",
        )
        db.add(user)
        db.flush()
        sites = [Site(user_id=user.id, name=f"현장 {index + 1}") for index in range(count)]
        db.add_all(sites)
        db.flush()
        user.current_site_id = sites[0].id
        db.commit()
        return db, user, sites

    @patch("app.routers.sites.heat_registry.stop_site")
    @patch("app.routers.sites.analysis_registry.stop_site")
    def test_user_can_delete_last_site(self, stop_analysis, stop_heat):
        db, user, sites = self._user_with_sites(1)
        result = delete_site(sites[0].id, user, db)

        self.assertEqual(result.sites, [])
        self.assertIsNone(result.current_site)
        self.assertIsNone(user.current_site_id)
        self.assertIsNone(db.get(Site, sites[0].id))
        stop_analysis.assert_called_once_with(sites[0].id)
        stop_heat.assert_called_once_with(sites[0].id)
        db.close()

    @patch("app.routers.sites.heat_registry.stop_site")
    @patch("app.routers.sites.analysis_registry.stop_site")
    def test_deleting_current_site_selects_remaining_site(self, _stop_analysis, _stop_heat):
        db, user, sites = self._user_with_sites(2)
        result = delete_site(sites[0].id, user, db)

        self.assertEqual(len(result.sites), 1)
        self.assertEqual(result.current_site.id, sites[1].id)
        self.assertEqual(user.current_site_id, sites[1].id)
        db.close()


if __name__ == "__main__":
    unittest.main()

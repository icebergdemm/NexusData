import unittest

from main import create_app


class UIV2RouteTests(unittest.TestCase):
    def test_v2_is_opt_in_and_classic_admin_remains_default(self):
        app = create_app()
        route_names = {
            route.path: route.name
            for route in app.routes
            if getattr(route, "path", None) in {
                "/",
                "/admin",
                "/admin/{full_path:path}",
                "/v2",
                "/v2/{full_path:path}",
            }
        }

        self.assertEqual(route_names["/"], "root_redirect")
        self.assertEqual(route_names["/admin"], "spa_root")
        self.assertEqual(route_names["/admin/{full_path:path}"], "spa_catchall")
        self.assertEqual(route_names["/v2"], "spa_v2_root")
        self.assertEqual(route_names["/v2/{full_path:path}"], "spa_v2_catchall")


if __name__ == "__main__":
    unittest.main()

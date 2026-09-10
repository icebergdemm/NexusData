import json
import unittest
from types import SimpleNamespace

from healthz import build_healthz_response, build_liveness_response, build_readiness_response


class HealthzTests(unittest.TestCase):
    def test_ok_health_is_200_and_not_cached(self):
        state = SimpleNamespace(
            maintenance_mode=False,
            db_healthy=True,
            api_enabled=True,
            web_enabled=True,
            workers_enabled=True,
            maintenance_message="",
        )
        response = build_healthz_response(state)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["status"], "ok")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_degraded_and_maintenance_health_are_503(self):
        base = dict(
            maintenance_mode=False,
            db_healthy=True,
            api_enabled=True,
            web_enabled=True,
            workers_enabled=True,
            maintenance_message="",
        )
        degraded = SimpleNamespace(**{**base, "db_healthy": False})
        maintenance = SimpleNamespace(**{**base, "maintenance_mode": True, "maintenance_message": "Плановые работы"})

        degraded_response = build_healthz_response(degraded)
        maintenance_response = build_healthz_response(maintenance)

        self.assertEqual(degraded_response.status_code, 503)
        self.assertEqual(json.loads(degraded_response.body)["status"], "degraded")
        self.assertEqual(maintenance_response.status_code, 503)
        self.assertEqual(json.loads(maintenance_response.body)["status"], "maintenance")

    def test_liveness_does_not_depend_on_database(self):
        response = build_liveness_response()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"status": "ok"})

    def test_readiness_preserves_maintenance_message(self):
        state = SimpleNamespace(
            maintenance_mode=True,
            db_healthy=True,
            api_enabled=True,
            web_enabled=True,
            workers_enabled=True,
            maintenance_message="Плановые работы",
        )
        response = build_readiness_response(state)
        body = json.loads(response.body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(body["status"], "maintenance")
        self.assertEqual(body["maintenance_message"], "Плановые работы")


if __name__ == "__main__":
    unittest.main()

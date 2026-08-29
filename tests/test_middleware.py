import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware import InjectionGuardMiddleware


class InjectionGuardTests(unittest.TestCase):
    def test_valid_service_key_bypasses_prior_ip_block(self):
        app = FastAPI()

        @app.get("/probe")
        async def probe():
            return {"status": "ok"}

        app.add_middleware(InjectionGuardMiddleware)

        with (
            patch("middleware.settings.api_key", "ingest-secret"),
            patch("middleware.settings.client_api_key", "client-secret"),
            TestClient(app) as client,
        ):
            blocked = client.get("/probe?cmd=%3B+echo+probe")
            authenticated = client.get(
                "/probe",
                headers={"X-API-Key": "client-secret"},
            )
            anonymous = client.get("/probe")

        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(authenticated.status_code, 200)
        self.assertEqual(authenticated.json(), {"status": "ok"})
        self.assertEqual(anonymous.status_code, 403)


if __name__ == "__main__":
    unittest.main()

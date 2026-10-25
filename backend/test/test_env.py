import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import Response

from app.api.auth import _set_session_cookie
from app.env import settings


class SettingsOriginTests(unittest.TestCase):
    def test_cors_origins_add_https_to_render_hostnames(self):
        with patch.dict(
            os.environ,
            {"CORS_ORIGINS": "frontend.onrender.com,http://localhost:5173"},
        ):
            self.assertEqual(
                settings.cors_origins,
                ["https://frontend.onrender.com", "http://localhost:5173"],
            )

    def test_frontend_url_adds_https_to_render_hostname(self):
        with patch.dict(os.environ, {"FRONTEND_URL": "frontend.onrender.com"}):
            self.assertEqual(settings.frontend_url, "https://frontend.onrender.com")

    def test_secure_session_cookie_allows_cross_origin_frontend_requests(self):
        with patch.dict(os.environ, {"AUTH_COOKIE_SECURE": "true"}), patch(
            "app.api.auth.create_session_token",
            return_value="signed-session",
        ):
            response = Response()
            _set_session_cookie(response, MagicMock(id=1))

        cookie = response.headers["set-cookie"]
        self.assertIn("SameSite=none", cookie)
        self.assertIn("Secure", cookie)

    def test_insecure_local_session_cookie_uses_lax_same_site_policy(self):
        with patch.dict(os.environ, {"AUTH_COOKIE_SECURE": "false"}), patch(
            "app.api.auth.create_session_token",
            return_value="signed-session",
        ):
            response = Response()
            _set_session_cookie(response, MagicMock(id=1))

        self.assertIn("SameSite=lax", response.headers["set-cookie"])


if __name__ == "__main__":
    unittest.main()

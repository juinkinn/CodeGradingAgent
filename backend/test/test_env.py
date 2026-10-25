import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()

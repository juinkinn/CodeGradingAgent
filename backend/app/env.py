import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


class Settings:
    @property
    def gemini_api_key(self) -> str | None:
        return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    @property
    def gemini_model(self) -> str:
        return os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    @property
    def langfuse_public_key(self) -> str | None:
        return os.getenv("LANGFUSE_PUBLIC_KEY")

    @property
    def langfuse_secret_key(self) -> str | None:
        return os.getenv("LANGFUSE_SECRET_KEY")

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def langfuse_prompt_label(self) -> str:
        return os.getenv("LANGFUSE_PROMPT_LABEL", "production")

    @property
    def otel_exporter_otlp_endpoint(self) -> str | None:
        return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None

    @property
    def otel_exporter_otlp_protocol(self) -> str:
        return os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    @property
    def otel_exporter_otlp_headers(self) -> str | None:
        return os.getenv("OTEL_EXPORTER_OTLP_HEADERS") or None

    @property
    def otel_service_name(self) -> str:
        return os.getenv("OTEL_SERVICE_NAME", "codegradingagent-api")

    @property
    def rabbitmq_url(self) -> str:
        return os.getenv(
            "RABBITMQ_URL",
            "amqp://guest:guest@localhost:5672/%2F",
        )

    @property
    def submission_upload_queue(self) -> str:
        return os.getenv("SUBMISSION_UPLOAD_QUEUE", "submission_uploads")

    @property
    def cors_origins(self) -> list[str]:
        origins = os.getenv("CORS_ORIGINS", "http://localhost:5173")
        return [
            self._normalize_origin(origin.strip())
            for origin in origins.split(",")
            if origin.strip()
        ]

    @property
    def auth_secret_key(self) -> str | None:
        return os.getenv("AUTH_SECRET_KEY") or None

    @property
    def auth_session_max_age(self) -> int:
        return int(os.getenv("AUTH_SESSION_MAX_AGE", str(60 * 60 * 24 * 14)))

    @property
    def auth_cookie_secure(self) -> bool:
        return os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"

    @property
    def google_client_id(self) -> str | None:
        return os.getenv("GOOGLE_CLIENT_ID") or None

    @property
    def google_client_secret(self) -> str | None:
        return os.getenv("GOOGLE_CLIENT_SECRET") or None

    @property
    def frontend_url(self) -> str:
        return self._normalize_origin(
            os.getenv("FRONTEND_URL", "http://localhost:5173")
        )

    @staticmethod
    def _normalize_origin(origin: str) -> str:
        origin = origin.rstrip("/")
        if not origin.startswith(("http://", "https://")):
            return f"https://{origin}"
        return origin


settings = Settings()

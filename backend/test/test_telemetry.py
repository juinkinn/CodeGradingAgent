import base64
import os
import unittest
from unittest.mock import patch
from urllib.parse import quote

from app.telemetry import (
    _otel_config,
    configure_grafana_otel,
    shutdown_grafana_otel,
)
from fastapi import FastAPI


class GrafanaMetricsConfigTests(unittest.TestCase):
    def test_metrics_are_disabled_without_grafana_configuration(self):
        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "",
                "OTEL_EXPORTER_OTLP_HEADERS": "",
            },
        ):
            self.assertIsNone(_otel_config())

    def test_config_reads_standard_otlp_variables_and_decodes_headers(self):
        authorization = "Basic " + base64.b64encode(
            b"123456:metrics-token"
        ).decode("ascii")
        with patch.dict(
            os.environ,
            {
                "OTEL_SERVICE_NAME": "grading-api",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.example.net/otlp/",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_EXPORTER_OTLP_HEADERS": (
                    "Authorization=" + quote(authorization, safe="")
                ),
            },
        ):
            endpoint, headers = _otel_config()

        self.assertEqual(
            endpoint,
            "https://otlp.example.net/otlp",
        )
        self.assertEqual(headers, {"Authorization": authorization})

    def test_headers_without_endpoint_are_rejected(self):
        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "",
                "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=secret",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "OTEL_EXPORTER_OTLP_ENDPOINT"):
                _otel_config()

    def test_http_endpoint_is_rejected_to_protect_credentials(self):
        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otlp.example.net/otlp",
                "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=secret",
            },
        ):
            with self.assertRaisesRegex(ValueError, "HTTPS URL"):
                _otel_config()

    def test_unsupported_protocol_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.example.net/otlp",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                "OTEL_EXPORTER_OTLP_HEADERS": "",
            },
        ):
            with self.assertRaisesRegex(ValueError, "http/protobuf"):
                _otel_config()

    def test_malformed_headers_are_rejected(self):
        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.example.net/otlp",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_EXPORTER_OTLP_HEADERS": "Authorization",
            },
        ):
            with self.assertRaisesRegex(ValueError, "name=value"):
                _otel_config()

    def test_configure_otel_creates_metrics_and_trace_exporters(self):
        app = FastAPI()
        with (
            patch.dict(
                os.environ,
                {
                    "OTEL_SERVICE_NAME": "grading-api",
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.example.net/otlp",
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20abc",
                },
            ),
            patch("app.telemetry.OTLPMetricExporter") as metric_exporter,
            patch("app.telemetry.OTLPSpanExporter") as span_exporter,
            patch("app.telemetry.metrics.set_meter_provider"),
            patch("app.telemetry.trace.set_tracer_provider"),
            patch("app.telemetry.FastAPIInstrumentor.instrument_app") as instrument,
        ):
            providers = configure_grafana_otel(app)

        self.assertIsNotNone(providers)
        metric_exporter.assert_called_once_with(
            endpoint="https://otlp.example.net/otlp/v1/metrics",
            headers={"Authorization": "Basic abc"},
        )
        span_exporter.assert_called_once_with(
            endpoint="https://otlp.example.net/otlp/v1/traces",
            headers={"Authorization": "Basic abc"},
        )
        instrument.assert_called_once()
        shutdown_grafana_otel(providers)


if __name__ == "__main__":
    unittest.main()

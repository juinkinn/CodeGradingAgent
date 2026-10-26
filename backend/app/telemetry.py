from urllib.parse import unquote, urlsplit

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.env import settings


class TelemetryProviders:
    def __init__(
        self,
        meter_provider: MeterProvider,
        tracer_provider: TracerProvider,
    ) -> None:
        self.meter_provider = meter_provider
        self.tracer_provider = tracer_provider


def _otel_config() -> tuple[str, dict[str, str]] | None:
    endpoint = settings.otel_exporter_otlp_endpoint
    headers = settings.otel_exporter_otlp_headers
    if not endpoint and not headers:
        return None
    if not endpoint:
        raise RuntimeError(
            "OTEL_EXPORTER_OTLP_HEADERS is set without OTEL_EXPORTER_OTLP_ENDPOINT."
        )
    if settings.otel_exporter_otlp_protocol != "http/protobuf":
        raise ValueError(
            "This backend supports OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf."
        )
    parsed_endpoint = urlsplit(endpoint)
    if parsed_endpoint.scheme != "https" or not parsed_endpoint.netloc:
        raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT must be an HTTPS URL.")
    if parsed_endpoint.query or parsed_endpoint.fragment:
        raise ValueError(
            "OTEL_EXPORTER_OTLP_ENDPOINT must not include a query or fragment."
        )

    parsed_headers: dict[str, str] = {}
    if headers:
        for item in headers.split(","):
            name, separator, value = item.partition("=")
            if not separator or not name.strip():
                raise ValueError(
                    "OTEL_EXPORTER_OTLP_HEADERS must contain comma-separated "
                    "name=value pairs."
                )
            parsed_headers[name.strip()] = unquote(value.strip())

    return (
        endpoint.rstrip("/"),
        parsed_headers,
    )


def configure_grafana_otel(app: FastAPI) -> TelemetryProviders | None:
    config = _otel_config()
    if config is None:
        return None

    base_endpoint, headers = config
    resource = Resource.create({"service.name": settings.otel_service_name})
    meter_provider = MeterProvider(
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=f"{base_endpoint}/v1/metrics",
                    headers=headers,
                )
            )
        ],
        resource=resource,
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{base_endpoint}/v1/traces",
                headers=headers,
            )
        )
    )
    metrics.set_meter_provider(meter_provider)
    trace.set_tracer_provider(tracer_provider)
    FastAPIInstrumentor.instrument_app(
        app,
        meter_provider=meter_provider,
        tracer_provider=tracer_provider,
    )
    return TelemetryProviders(meter_provider, tracer_provider)


def shutdown_grafana_otel(provider: TelemetryProviders | None) -> None:
    if provider is not None:
        provider.meter_provider.force_flush()
        provider.tracer_provider.force_flush()
        provider.meter_provider.shutdown()
        provider.tracer_provider.shutdown()

"""
Prometheus metrics registry for the satellite ingestion pipeline.

Centralised here so that config.py remains pure configuration and any
module that needs to record telemetry imports directly from this file.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Summary

registry = CollectorRegistry()

INGESTION_SUCCESS = Counter(
    "satellite_ingestion_success_total",
    "Total number of products successfully ingested and archived",
    registry=registry,
)

INGESTION_FAILURE = Counter(
    "satellite_ingestion_failure_total",
    "Total number of products that failed ingestion for any reason",
    registry=registry,
)

VALIDATION_REJECTED = Counter(
    "satellite_validation_rejected_total",
    "Total number of products rejected by the validation compliance layer",
    registry=registry,
)

BYTES_DOWNLOADED = Counter(
    "satellite_downloaded_bytes_total",
    "Cumulative volume of raw product data downloaded in bytes",
    registry=registry,
)

API_LATENCY = Summary(
    "satellite_api_request_duration_seconds",
    "Time spent waiting for the EUMETSAT collection search response",
    registry=registry,
)

SYSTEM_STATUS = Gauge(
    "satellite_pipeline_healthy",
    "1 if the last pipeline run completed without a fatal error, 0 otherwise",
    registry=registry,
)

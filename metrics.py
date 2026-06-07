"""
Prometheus metrics registry for the satellite ingestion pipeline.

Centralised here so that config.py remains pure configuration and any
module that needs to record telemetry imports directly from this file.
"""

import os
from prometheus_client import CollectorRegistry, Counter, Gauge, Summary

import config

registry = CollectorRegistry()

# ---------------------------------------------------------------------------
# Ingestion lifecycle counters
# ---------------------------------------------------------------------------

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

PRODUCTS_DISCOVERED = Counter(
    "satellite_products_discovered_total",
    "Total number of products returned by the EUMETSAT collection search",
    registry=registry,
)

PRODUCTS_SKIPPED = Counter(
    "satellite_products_skipped_total",
    "Total number of products skipped because they were already ingested or quarantined",
    registry=registry,
)

# ---------------------------------------------------------------------------
# Data volume
# ---------------------------------------------------------------------------

BYTES_DOWNLOADED = Counter(
    "satellite_downloaded_bytes_total",
    "Cumulative volume of raw product data downloaded in bytes",
    registry=registry,
)

# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------

API_LATENCY = Summary(
    "satellite_api_request_duration_seconds",
    "Time spent waiting for the EUMETSAT collection search response",
    registry=registry,
)

DOWNLOAD_DURATION = Summary(
    "satellite_download_duration_seconds",
    "Time spent downloading a single product from the EUMETSAT Data Store",
    registry=registry,
)

# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------

SYSTEM_STATUS = Gauge(
    "satellite_pipeline_healthy",
    "1 if the last pipeline run completed without a fatal error, 0 otherwise",
    registry=registry,
)

QUARANTINE_SIZE = Gauge(
    "satellite_quarantine_file_count",
    "Number of files currently sitting in the quarantine directory",
    registry=registry,
)


def update_quarantine_size() -> None:
    """Refresh the quarantine size gauge from the filesystem."""
    try:
        count = sum(
            1 for f in os.listdir(config.QUARANTINE_DIR)
            if os.path.isfile(os.path.join(config.QUARANTINE_DIR, f))
        )
        QUARANTINE_SIZE.set(count)
    except Exception:
        pass

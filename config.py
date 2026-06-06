import os
from datetime import timedelta
from dotenv import load_dotenv
from prometheus_client import CollectorRegistry, Counter, Gauge, Summary

load_dotenv()

# Ensure standard log directories exist
os.makedirs("logs", exist_ok=True)

# Data Directories
COLLECTION_ID = "EO:EUM:DAT:0408"
INBOUND_DIR = "data/inbound"
ARCHIVE_DIR = "data/archive"       
QUARANTINE_DIR = "data/quarantine"

for path in [INBOUND_DIR, ARCHIVE_DIR, QUARANTINE_DIR]:
    os.makedirs(path, exist_ok=True)

# Telemetry Configuration
PUSHGATEWAY_HOST = os.getenv("PUSHGATEWAY_HOST", "localhost")
PUSHGATEWAY_PORT = os.getenv("PUSHGATEWAY_PORT", "9091")
PUSHGATEWAY_URL = f"http://{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}"

# Database Configuration
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "satellite_metadata")
DB_USER = os.getenv("DB_USER", "pipeline_admin")
DB_PASS = os.getenv("DB_PASS")

RETENTION_PERIOD = timedelta(days=7)

# Validation Thresholds (OL_2_WRR spec: S3IPF PDS 004.3 v1B, Sep 2023)
# Maximum percentage of invalid pixels before a product is rejected
VALIDATION_MAX_INVALID_PIXEL_PCT = 95.0
# Expected column count for Reduced Resolution (RR) products
VALIDATION_RR_EXPECTED_COLUMNS = 1217

# Prometheus Metrics Registry Setup
registry = CollectorRegistry()
METRIC_INGESTION_SUCCESS = Counter('satellite_ingestion_success_total', 'Total successful product ingestions', registry=registry)
METRIC_INGESTION_FAILURE = Counter('satellite_ingestion_failure_total', 'Total failed product ingestions', registry=registry)
METRIC_BYTES_DOWNLOADED = Counter('satellite_downloaded_bytes_total', 'Total volume of data transferred in bytes', registry=registry)
METRIC_API_LATENCY = Summary('satellite_api_request_duration_seconds', 'Time spent querying EUMETSAT API', registry=registry)
METRIC_SYSTEM_STATUS = Gauge('satellite_pipeline_healthy', '1 if pipeline run was successful, 0 if fatal error', registry=registry)
METRIC_VALIDATION_REJECTED = Counter('satellite_validation_rejected_total', 'Total payloads rejected by validation compliance rules', registry=registry)
"""
Runtime configuration for the satellite ingestion pipeline.

All settings are read from environment variables so the same Docker image
can be used in development, staging, and production without code changes.
Required variables that have no default will raise a clear EnvironmentError
at import time rather than surfacing as a cryptic downstream failure.
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require(name: str) -> str:
    """Return the value of an env var, raising clearly if it is absent."""
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            f"Add it to your .env file or Docker environment block."
        )
    return value


def _optional(name: str, default: str) -> str:
    return os.getenv(name, default)


# ---------------------------------------------------------------------------
# Paths — absolute so the pipeline works regardless of the working directory
# ---------------------------------------------------------------------------

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INBOUND_DIR   = os.path.join(_BASE_DIR, "data", "inbound")
ARCHIVE_DIR   = os.path.join(_BASE_DIR, "data", "archive")
QUARANTINE_DIR = os.path.join(_BASE_DIR, "data", "quarantine")
LOG_DIR       = os.path.join(_BASE_DIR, "logs")

for _path in (INBOUND_DIR, ARCHIVE_DIR, QUARANTINE_DIR, LOG_DIR):
    os.makedirs(_path, exist_ok=True)

# ---------------------------------------------------------------------------
# EUMETSAT collection
# ---------------------------------------------------------------------------

COLLECTION_ID = "EO:EUM:DAT:0408"

# ---------------------------------------------------------------------------
# Database — DB_PASS is required; all others have sensible defaults
# ---------------------------------------------------------------------------

DB_HOST = _optional("DB_HOST", "127.0.0.1")
DB_PORT = _optional("DB_PORT", "5432")
DB_NAME = _optional("DB_NAME", "satellite_metadata")
DB_USER = _optional("DB_USER", "pipeline_admin")
DB_PASS = _require("DB_PASS")

# ---------------------------------------------------------------------------
# Pushgateway
# ---------------------------------------------------------------------------

PUSHGATEWAY_HOST = _optional("PUSHGATEWAY_HOST", "localhost")
PUSHGATEWAY_PORT = _optional("PUSHGATEWAY_PORT", "9091")
PUSHGATEWAY_URL  = f"http://{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}"

# ---------------------------------------------------------------------------
# Storage retention
# ---------------------------------------------------------------------------

RETENTION_DAYS   = int(_optional("RETENTION_DAYS", "7"))
RETENTION_PERIOD = timedelta(days=RETENTION_DAYS)

# ---------------------------------------------------------------------------
# Validation thresholds (OL_2_WRR — S3IPF PDS 004.3 v1B, Sep 2023)
# ---------------------------------------------------------------------------

# Products with more than this percentage of invalid pixels are rejected
VALIDATION_MAX_INVALID_PIXEL_PCT = float(
    _optional("VALIDATION_MAX_INVALID_PIXEL_PCT", "95.0")
)

# Expected column count for Reduced Resolution products (§7.1.1.4.1)
VALIDATION_RR_EXPECTED_COLUMNS = 1217

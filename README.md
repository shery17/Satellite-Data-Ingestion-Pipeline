# Satellite Data Ingestion Pipeline

![CI](https://github.com/shery17/Satellite-Data-Ingestion-Pipeline/actions/workflows/ci.yml/badge.svg)

An automated ingestion pipeline for **OLCI Level 2 Ocean Colour Reduced Resolution — Sentinel-3** (`EO:EUM:DAT:0408`) data from the EUMETSAT Data Store.

Every 15 minutes the pipeline discovers new products, validates them against the official product format specification, archives accepted products to warm storage, and records the full product lifecycle — including rejections — in a PostgreSQL audit ledger. Operational metrics are exposed via Prometheus and visualised in Grafana.

---

## Architecture

```
EUMETSAT Data Store (eumdac)
        │
        ▼
  [ Discovery ]  ── searches collection EO:EUM:DAT:0408
        │
        ▼
  [ Download ]   ── atomic temp-file write → rename (prevents partial-write race)
        │
        ▼
  [ Validation ] ── 6-tier compliance check (S3IPF PDS 004.3)
        │
   ┌────┴────┐
   │         │
PASS       FAIL
   │         │
   ▼         ▼
[ Archive ] [ Quarantine ]
.tar.gz     _REJECTED.zip / _ERR.zip
   │         │
   └────┬────┘
        ▼
 [ PostgreSQL ]  ── ingested_products + quarantined_products tables
        │
        ▼
 [ Prometheus / Pushgateway / Grafana ]  ── operational telemetry
```

---

## Validation

Validation is grounded in the **S3IPF PDS 004.3** specification (*Product Data Format Specification — OLCI Level 2 Marine*, EUMETSAT, 11 Sep 2023, Doc No. EUM/RSP/SPE/23/1363219, Issue v1B). Six tiers run on every downloaded product before it is accepted into the archive:

| Tier | Check | Spec Reference |
|------|-------|----------------|
| 1 | SAFE archive integrity — ZIP valid, all 17 mandatory files present, product folder name matches Sentinel-3 naming convention | Table 7-1, §7.1.1.1, §3.2 |
| 2 | Manifest pixel quality — `invalidPixels.percentage` must not exceed 95 % | Table 7-2, §7.1.1.2.1 |
| 3 | NetCDF global attributes — `start_time`, `stop_time`, `absolute_orbit_number` present and correctly formatted | Table 4-1, §4.1.3.1 |
| 4 | Spatial dimensions — column count must equal 1217 (Reduced Resolution grid) | §7.1.1.4.1 |
| 5 | WQSF flag metadata — `WQSF` variable present with `flag_meanings` and `flag_masks` attributes; core classification flags (`INVALID`, `WATER`, `LAND`, `CLOUD`) declared | §7.1.1.4.1, Table 7-13 |
| 6 | Geo-coordinate bounds — latitude ∈ [−90, 90], longitude ∈ [−180, 180] | §4.2.2.2 |

Products that fail any tier are moved to `data/quarantine/` and a record is written to the `quarantined_products` table with the rejection reason, so no product is ever silently lost.

Validation has been tested against real S3A and S3B OL_2_WRR products (collection 004 baseline, March 2026).

---

## Product Lifecycle Tracking

Two database tables maintain a complete audit trail:

**`ingested_products`** — every successfully archived product

| Column | Type | Description |
|--------|------|-------------|
| `product_id` | VARCHAR PRIMARY KEY | EUMETSAT product identifier |
| `ingested_at` | TIMESTAMP | Time of successful ingestion |
| `file_size_mb` | REAL | Downloaded file size |

**`quarantined_products`** — every product that could not be ingested

| Column | Type | Description |
|--------|------|-------------|
| `product_id` | VARCHAR PRIMARY KEY | EUMETSAT product identifier |
| `quarantined_at` | TIMESTAMP | Time of failure |
| `status` | VARCHAR | `REJECTED` (validation failure) or `ERROR` (download failure) |
| `reason` | TEXT | Full error message for diagnostics |

A product recorded in either table is not re-downloaded on subsequent scheduler runs.

---

## Observability

The following Prometheus metrics are pushed to the Pushgateway after every scheduler cycle and displayed in a provisioned Grafana dashboard (loads automatically on `docker compose up` — no manual setup required):

| Metric | Type | Description |
|--------|------|-------------|
| `satellite_ingestion_success_total` | Counter | Successfully ingested products |
| `satellite_ingestion_failure_total` | Counter | Failed ingestions (all causes) |
| `satellite_validation_rejected_total` | Counter | Products rejected by validation |
| `satellite_products_discovered_total` | Counter | Products returned by each EUMETSAT collection search |
| `satellite_products_skipped_total` | Counter | Products skipped — already ingested or quarantined |
| `satellite_downloaded_bytes_total` | Counter | Cumulative bytes downloaded |
| `satellite_api_request_duration_seconds` | Summary | EUMETSAT API search latency |
| `satellite_download_duration_seconds` | Summary | Per-product download duration |
| `satellite_pipeline_healthy` | Gauge | 1 = last cycle healthy, 0 = fatal error |
| `satellite_quarantine_file_count` | Gauge | Files currently in the quarantine directory |

The dashboard covers five sections: Pipeline Health, Ingestion Throughput, Validation, Data Volume, and Performance.

---

## Stack

| Service | Purpose |
|---------|---------|
| `pipeline_worker` | Python scheduler — runs ingestion every 15 minutes |
| `postgres:15-alpine` | Product lifecycle audit ledger |
| `prom/prometheus` | Metrics scraping and storage |
| `prom/pushgateway` | Receives metrics pushed by the worker |
| `grafana/grafana` | Operational dashboard — auto-provisioned, no manual setup required |

All services have Docker healthchecks. The worker waits for Postgres and Pushgateway to be healthy before starting. Grafana waits for Prometheus.

---

## How to Run

### Prerequisites

- Docker Desktop installed and running
- Active EUMETSAT Data Store API credentials ([register here](https://eoportal.eumetsat.int))

### 1. Clone the repository

```bash
git clone https://github.com/shery17/Satellite-Data-Ingestion-Pipeline.git
cd Satellite-Data-Ingestion-Pipeline
```

### 2. Configure environment variables

Create a `.env` file in the root directory:

```bash
# EUMETSAT API credentials
EUMETSAT_CONSUMER_KEY="your_consumer_key"
EUMETSAT_CONSUMER_SECRET="your_consumer_secret"

# Database
DB_HOST=pipeline_database
DB_NAME=satellite_metadata
DB_USER=pipeline_admin
DB_PASS=pipeline_pass
DB_PORT=5432

# Optional overrides
RETENTION_DAYS=7
VALIDATION_MAX_INVALID_PIXEL_PCT=95.0
```

### 3. Start the stack

```bash
docker compose up --build -d
```

The pipeline worker runs immediately on startup and then every 15 minutes.

### 4. Trigger a manual run

```bash
docker compose exec pipeline_worker python main.py
```

### 5. Access dashboards

| Dashboard | URL |
|-----------|-----|
| Grafana | http://localhost:3000 (admin / admin) |
| Prometheus | http://localhost:9090 |
| Pushgateway | http://localhost:9091 |

### 6. Stop the stack

```bash
docker compose down
```

To also remove all stored data and database volumes:

```bash
docker compose down -v
```

---

## Running Tests

Unit tests require no credentials, no database, and no Docker services:

```bash
pip install -r requirements-test.txt
pytest tests/test_storage.py -v
```

Database integration tests require the Postgres container to be running:

```bash
docker compose up -d postgres
pytest tests/ -v -m integration
```

---

## Incident Response

### "CRITICAL: Authentication failed" in logs
1. Check that `EUMETSAT_CONSUMER_KEY` and `EUMETSAT_CONSUMER_SECRET` are set in `.env`
2. Verify credentials have not expired on the [EUMETSAT User Portal](https://eoportal.eumetsat.int)

### Product appears in `data/quarantine/` as `_REJECTED.zip`
1. Query the audit ledger for the rejection reason:
   ```sql
   SELECT product_id, quarantined_at, status, reason
   FROM quarantined_products
   ORDER BY quarantined_at DESC
   LIMIT 10;
   ```
2. The `reason` field contains the full validation tier failure message

### Product appears in `data/quarantine/` as `_ERR.zip`
- Status `ERROR` indicates a download failure rather than a validation failure
- Check `logs/pipeline.log` for the network error at the time of the failure

### Pipeline metric `satellite_pipeline_healthy` is 0
1. Check `logs/pipeline.log` for the most recent fatal error
2. Verify the Postgres container is healthy: `docker compose ps`
3. Restart the worker: `docker compose restart pipeline_worker`

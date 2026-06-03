# Satellite Data Ingestion Pipeline

## 📝 Project Description

This project is an automated data pipeline designed to ingest, process, and monitor OLCI Level 2 Ocean Colour Reduced Resolution - Sentinel-3 (`EO:EUM:DAT:0408`) data from the EUMETSAT API.

Every 15 minutes, the pipeline automatically executes the following workflow:

- **Pull:** Downloads `EO:EUM:DAT:0408` product files directly from the EUMETSAT API using `eumdac`.
- **Validate:** Runs validation and integrity tests on incoming data files.
- **Stage:** Temporarily stores raw data files in a local archive directory.
- **Catalog:** Parses and stores tracking metadata into a PostgreSQL database.
- **Monitor:** Tracks real-time pipeline performance metrics and displays them on a Grafana dashboard.

---

## Features currently need working on

- Script validation testing for the scientific data files
- Continous integration + automated testing via github actions
- Prometheus + grafana monitoring and metrics display

---

## 🏃 How to Run

### Prerequisites

- Docker Desktop installed and running  
- Active EUMETSAT API credentials  

---

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd Satellite-Data-Ingestion-Pipeline
```

---

### 2. Configure Environment Variables

Create a `.env` file in the root directory:

```bash
# EUMETSAT API Credentials
EUMETSAT_CONSUMER_KEY="your_consumer_key"
EUMETSAT_CONSUMER_SECRET="your_consumer_secret"

# Internal Database Configuration
DB_HOST="pipeline_database"
DB_NAME="satellite_metadata"
DB_USER="pipeline_admin"
DB_PASS="pipeline_pass"
DB_PORT="5432"
```

---

### 3. Start the Pipeline Stack

```bash
docker compose up --build -d
```

The pipeline worker will automatically run every 15 minutes.

---

### 4. Trigger a Manual Run (Optional)

```bash
docker compose exec pipeline_worker python pipeline.py
```

---

### 5. Access the Dashboards

- Grafana: http://localhost:3000  
- Prometheus: http://localhost:9090  
- Pushgateway: http://localhost:9091  

---

### 🛑 Stop the Project

```bash
docker compose down
```

## Incident Response & Recovery Playbook (IN PROGRESS)

### Scenario A: Log reports "CRITICAL: Authentication failed"
1. Check if the EUMETSAT consumer credentials have expired on the User Portal.
2. Verify that local environment variables (`EUMETSAT_KEY`) are loaded.

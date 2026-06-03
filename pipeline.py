import os
import json
import logging
import shutil
import tarfile
from datetime import datetime, timedelta
from dotenv import load_dotenv
import eumdac

# 1. SWAP SQLITE FOR THE ENTERPRISE POSTGRES ADAPTER
import psycopg2 

from prometheus_client import CollectorRegistry, Counter, Gauge, Summary, push_to_gateway

load_dotenv()

# Ensure standard log directory structure exists before setting up handlers
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("logs/pipeline.log"),
        logging.StreamHandler()
    ]
)

COLLECTION_ID = "EO:EUM:DAT:0408"
INBOUND_DIR = "data/inbound"
ARCHIVE_DIR = "data/archive"       
QUARANTINE_DIR = "data/quarantine"

# --- PROMETHEUS TELEMETRY CONFIG (LOADED DYNAMICALLY FOR DOCKER BRIDGE) ---
PUSHGATEWAY_HOST = os.getenv("PUSHGATEWAY_HOST", "localhost")
PUSHGATEWAY_PORT = os.getenv("PUSHGATEWAY_PORT", "9091")
PUSHGATEWAY_URL = f"http://{PUSHGATEWAY_HOST}:{PUSHGATEWAY_PORT}"

# --- POSTGRESQL CONNECTION PARAMS (LOADED DYNAMICALLY FROM ENVIRONMENT) ---
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")  # Updated to 5432 internal standard fallback
DB_NAME = os.getenv("DB_NAME", "satellite_metadata")
DB_USER = os.getenv("DB_USER", "pipeline_admin")
DB_PASS = os.getenv("DB_PASS")

RETENTION_PERIOD = timedelta(days=7) 

os.makedirs(INBOUND_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)
os.makedirs(QUARANTINE_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

registry = CollectorRegistry()
METRIC_INGESTION_SUCCESS = Counter('satellite_ingestion_success_total', 'Total successful product ingestions', registry=registry)
METRIC_INGESTION_FAILURE = Counter('satellite_ingestion_failure_total', 'Total failed product ingestions', registry=registry)
METRIC_BYTES_DOWNLOADED = Counter('satellite_downloaded_bytes_total', 'Total volume of data transferred in bytes', registry=registry)
METRIC_API_LATENCY = Summary('satellite_api_request_duration_seconds', 'Time spent querying EUMETSAT API', registry=registry)
METRIC_SYSTEM_STATUS = Gauge('satellite_pipeline_healthy', '1 if pipeline run was successful, 0 if fatal error', registry=registry)

# --- UNIFIED NETWORK CONNECTION FACTORY ---
def get_db_connection():
    """Establishes a live network connection to the PostgreSQL container."""
    return psycopg2.connect(
        host=DB_HOST,
        port=int(DB_PORT),  # Forces it to be an integer
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

def init_database(conn):
    """Creates the structural ledger tracking metadata if it does not exist using an active connection."""
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ingested_products (
                product_id VARCHAR(255) PRIMARY KEY,
                ingested_at TIMESTAMP NOT NULL,
                file_size_mb REAL NOT NULL
            )
        ''')
        conn.commit()
        cursor.close()
        logging.info("PostgreSQL structural initialization check complete.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to initialize database structures: {e}")
        raise e

def is_already_ingested(conn, product_id):
    """Reuses the single connection to check database records quickly."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM ingested_products WHERE product_id = %s", (product_id,))
        result = cursor.fetchone()
        cursor.close()
        return result is not None
    except Exception as e:
        conn.rollback()
        logging.error(f"Error querying product ingestion status for {product_id}: {e}")
        return False

def record_ingestion(conn, product_id, size_mb):
    """Reuses the single connection to commit a production completion entry smoothly.
    Utilizes UPSERT logic to handle unexpected duplicate transaction requests safely.
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ingested_products (product_id, ingested_at, file_size_mb) 
            VALUES (%s, %s, %s)
            ON CONFLICT (product_id) 
            DO NOTHING
            """,
            (product_id, datetime.now(), size_mb)
        )
        conn.commit()
        cursor.close()
    except Exception as e:
        conn.rollback()
        logging.error(f"Database error writing ledger record for {product_id}: {e}")
        raise e

def enforce_data_retention_policy():
    """Scans the warm storage archive and deletes folders older than the retention threshold."""
    logging.info("Executing storage retention check on Warm Storage tier...")
    now = datetime.now()
    purged_count = 0

    for file_name in os.listdir(ARCHIVE_DIR):
        file_path = os.path.join(ARCHIVE_DIR, file_name)
        file_modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
        file_age = now - file_modified_time

        if file_age > RETENTION_PERIOD:
            logging.warning(f"RETENTION POLICY ENFORCED: Removing expired data packet '{file_name}'.")
            try:
                os.remove(file_path)
                purged_count += 1
            except Exception as e:
                logging.error(f"Failed to purge expired file {file_name}: {e}")
                
    if purged_count > 0:
        logging.info(f"Retention policy sweep complete. Purged {purged_count} expired archives.")
    else:
        logging.info("Retention policy sweep complete. All archives are within compliance parameters.")

def archive_and_compress_payload(product_id, source_file_path):
    """Compresses the raw zip/asset into a tarball and moves it to warm storage."""
    archive_tar_name = f"{product_id}.tar.gz"
    destination_path = os.path.join(ARCHIVE_DIR, archive_tar_name)
    
    logging.info(f"Archiving payload to Warm Storage: {archive_tar_name}")
    try:
        with tarfile.open(destination_path, "w:gz") as tar:
            tar.add(source_file_path, arcname=os.path.basename(source_file_path))
        os.remove(source_file_path)
        logging.info(f"Archival compression successful for {product_id}.")
    except Exception as e:
        logging.error(f"Archival compression failed for {product_id}: {e}")
        if os.path.exists(source_file_path):
            os.remove(source_file_path)

def run_ingestion():
    """Executes a single-pass ingestion iteration cycle. 
    Can be called directly or imported safely by an external scheduler daemon.
    """
    logging.info("Waking up worker: Initiating single-pass ingestion cycle...")
    
    # Establish single shared persistent connection block for this script run
    try:
        db_conn = get_db_connection()
    except Exception as db_err:
        logging.critical(f"CRITICAL: Failed to connect to database host {DB_HOST}: {db_err}")
        METRIC_SYSTEM_STATUS.set(0)
        raise db_err

    try:
        init_database(db_conn)
        enforce_data_retention_policy()
        
        METRIC_SYSTEM_STATUS.set(1)
        consumer_key = os.getenv("EUMETSAT_CONSUMER_KEY")
        consumer_secret = os.getenv("EUMETSAT_CONSUMER_SECRET")
        
        if not consumer_key or not consumer_secret:
            logging.error("CRITICAL: Environment keys missing.")
            METRIC_SYSTEM_STATUS.set(0)
            return

        try:
            credentials = (consumer_key, consumer_secret)
            token = eumdac.AccessToken(credentials)
            datastore = eumdac.DataStore(token)
            collection = datastore.get_collection(COLLECTION_ID)
        except Exception as e:
            logging.error(f"CRITICAL: Handshake failed: {e}")
            METRIC_SYSTEM_STATUS.set(0)
            return

        start_time = datetime.now() - timedelta(days=1)
        
        try:
            with METRIC_API_LATENCY.time():
                products = list(collection.search(
                    dtstart=start_time,
                    geo="POLYGON((0 50, 10 50, 10 60, 0 60, 0 50))"
                ))
            logging.info(f"Discovery complete. Found {len(products)} products.")
        except Exception as e:
            logging.error(f"API Query Failure: {e}")
            METRIC_SYSTEM_STATUS.set(0)
            return

        for product in products:
            product_id = str(product)
            
            # Pass persistent database connection reference
            if is_already_ingested(db_conn, product_id):
                continue
                
            logging.info(f"New data packet discovered: {product_id}")
            
            download_path = os.path.join(INBOUND_DIR, f"{product_id}.zip")
            try:
                # Optimized explicit chunk-buffered streaming safe for container volumes
                with product.open() as fsrc, open(download_path, mode='wb') as fdst:
                    while True:
                        chunk = fsrc.read(1024 * 1024) # 1MB chunks
                        if not chunk:
                            break
                        fdst.write(chunk)
                    
                bytes_size = os.path.getsize(download_path)
                file_size_mb = bytes_size / (1024 * 1024)
                
                METRIC_BYTES_DOWNLOADED.inc(bytes_size)
                METRIC_INGESTION_SUCCESS.inc()
                
                # Pass persistent database connection reference
                record_ingestion(db_conn, product_id, file_size_mb)
                
                logging.info(f"SUCCESS: Ingested {product_id} ({file_size_mb:.2f} MB)")
                archive_and_compress_payload(product_id, download_path)
                
            except Exception as err:
                logging.error(f"Network processing failure: {err}")
                METRIC_INGESTION_FAILURE.inc()
                if os.path.exists(download_path):
                    shutil.move(download_path, os.path.join(QUARANTINE_DIR, f"{product_id}_ERR.zip"))
                    
    finally:
        # Guarantee network connection closure on completion
        db_conn.close()
        logging.info("Internal database socket closed securely.")

if __name__ == "__main__":
    # This block executes ONLY if someone runs `python pipeline.py` directly.
    # When `scheduler.py` calls run_ingestion(), this block is safely bypassed.
    try:
        run_ingestion()
    except Exception as runtime_fatal:
        logging.critical(f"Fatal operational exception: {runtime_fatal}")
        METRIC_SYSTEM_STATUS.set(0)
    finally:
        logging.info(f"Worker manual cycle complete. Synchronizing telemetry metrics with Pushgateway at {PUSHGATEWAY_URL}...")
        try:
            push_to_gateway(PUSHGATEWAY_URL, job='satellite_pipeline_worker_manual', registry=registry)
            logging.info("Telemetry network sync successful. Exiting execution script.")
        except Exception as push_err:
            logging.error(f"Failed to transmit operational telemetry to gateway: {push_err}")
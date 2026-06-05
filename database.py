import logging
from datetime import datetime
import psycopg2
import config

def get_db_connection():
    """Establishes a live network connection to the PostgreSQL container."""
    return psycopg2.connect(
        host=config.DB_HOST,
        port=int(config.DB_PORT),
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASS
    )

def init_database(conn):
    """Creates the structural ledger tracking metadata if it does not exist."""
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ingested_products (
                    product_id VARCHAR(255) PRIMARY KEY,
                    ingested_at TIMESTAMP NOT NULL,
                    file_size_mb REAL NOT NULL
                )
            ''')
            conn.commit()
            logging.info("PostgreSQL structural initialization check complete.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to initialize database structures: {e}")
        raise e

def is_already_ingested(conn, product_id):
    """Checks database records for existing ingestion state."""
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM ingested_products WHERE product_id = %s", (product_id,))
            return cursor.fetchone() is not None
    except Exception as e:
        conn.rollback()
        logging.error(f"Error querying product ingestion status for {product_id}: {e}")
        return False

def record_ingestion(conn, product_id, size_mb):
    """Commits a production completion entry utilizing safe UPSERT logic."""
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ingested_products (product_id, ingested_at, file_size_mb) 
                VALUES (%s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
                """,
                (product_id, datetime.now(), size_mb)
            )
            conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Database error writing ledger record for {product_id}: {e}")
        raise e
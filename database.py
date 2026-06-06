"""
Database layer for the satellite ingestion pipeline.

Two tables are maintained:

  ingested_products  — ledger of every product successfully ingested
  quarantined_products — audit trail of every product rejected or errored,
                         with the failure reason recorded for diagnostics

Having both tables means the full product lifecycle is traceable:
discovery → ingestion success  (ingested_products)
discovery → validation failure (quarantined_products, status='REJECTED')
discovery → download error     (quarantined_products, status='ERROR')
"""

import logging
from datetime import datetime

import psycopg2

import config


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_db_connection():
    """Establish a connection to the PostgreSQL container."""
    return psycopg2.connect(
        host=config.DB_HOST,
        port=int(config.DB_PORT),
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASS,
    )


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_database(conn) -> None:
    """
    Create the pipeline schema if it does not already exist.
    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingested_products (
                    product_id    VARCHAR(255) PRIMARY KEY,
                    ingested_at   TIMESTAMP    NOT NULL,
                    file_size_mb  REAL         NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quarantined_products (
                    product_id    VARCHAR(255) PRIMARY KEY,
                    quarantined_at TIMESTAMP   NOT NULL,
                    status        VARCHAR(20)  NOT NULL,  -- 'REJECTED' | 'ERROR'
                    reason        TEXT         NOT NULL
                )
            """)
        conn.commit()
        logging.info("Database schema initialisation complete.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to initialise database schema: {e}")
        raise


# ---------------------------------------------------------------------------
# Ingested products
# ---------------------------------------------------------------------------

def is_already_ingested(conn, product_id: str) -> bool:
    """
    Return True if this product has previously been ingested successfully
    or has already been quarantined — either way there is nothing to do.
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM ingested_products WHERE product_id = %s",
                (product_id,),
            )
            if cursor.fetchone() is not None:
                return True
            cursor.execute(
                "SELECT 1 FROM quarantined_products WHERE product_id = %s",
                (product_id,),
            )
            return cursor.fetchone() is not None
    except Exception as e:
        conn.rollback()
        logging.error(f"Error checking ingestion status for {product_id}: {e}")
        return False


def record_ingestion(conn, product_id: str, size_mb: float) -> None:
    """Write a successful ingestion entry. Silently ignores duplicates."""
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ingested_products (product_id, ingested_at, file_size_mb)
                VALUES (%s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
                """,
                (product_id, datetime.now(), size_mb),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to record ingestion for {product_id}: {e}")
        raise


# ---------------------------------------------------------------------------
# Quarantined products
# ---------------------------------------------------------------------------

def record_quarantine(conn, product_id: str, reason: str, status: str = "REJECTED") -> None:
    """
    Write an audit entry for a product that could not be ingested.

    Args:
        product_id: The EUMETSAT product identifier.
        reason:     The validation error message or exception string.
        status:     'REJECTED' for validation failures, 'ERROR' for
                    download or unexpected processing failures.
    """
    if status not in ("REJECTED", "ERROR"):
        raise ValueError(f"Invalid quarantine status '{status}'. Must be 'REJECTED' or 'ERROR'.")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO quarantined_products (product_id, quarantined_at, status, reason)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (product_id) DO NOTHING
                """,
                (product_id, datetime.now(), status, reason),
            )
        conn.commit()
        logging.info(f"Quarantine record written for {product_id} [{status}].")
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to write quarantine record for {product_id}: {e}")
        raise

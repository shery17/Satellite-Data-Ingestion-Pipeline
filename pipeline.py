import os
import logging
import shutil
import tempfile
from datetime import datetime, timedelta
import eumdac

import config
import database
import metrics
import storage
import validation


def run_ingestion():
    """Executes a single-pass ingestion iteration cycle."""
    logging.info("Waking up worker: Initiating single-pass ingestion cycle...")

    try:
        db_conn = database.get_db_connection()
    except Exception as db_err:
        logging.critical(f"CRITICAL: Failed to connect to database host {config.DB_HOST}: {db_err}")
        metrics.SYSTEM_STATUS.set(0)
        raise db_err

    try:
        database.init_database(db_conn)
        storage.enforce_data_retention_policy()
        metrics.SYSTEM_STATUS.set(1)

        consumer_key = os.getenv("EUMETSAT_CONSUMER_KEY")
        consumer_secret = os.getenv("EUMETSAT_CONSUMER_SECRET")

        if not consumer_key or not consumer_secret:
            logging.error("CRITICAL: Environment keys missing.")
            metrics.SYSTEM_STATUS.set(0)
            return

        try:
            credentials = (consumer_key, consumer_secret)
            token = eumdac.AccessToken(credentials)
            datastore = eumdac.DataStore(token)
            collection = datastore.get_collection(config.COLLECTION_ID)
        except Exception as e:
            logging.error(f"CRITICAL: Handshake failed: {e}")
            metrics.SYSTEM_STATUS.set(0)
            return

        start_time = datetime.now() - timedelta(days=1)

        try:
            with metrics.API_LATENCY.time():
                products = list(collection.search(
                    dtstart=start_time,
                    geo="POLYGON((0 50, 10 50, 10 60, 0 60, 0 50))"
                ))
            logging.info(f"Discovery complete. Found {len(products)} products.")
        except Exception as e:
            logging.error(f"API Query Failure: {e}")
            metrics.SYSTEM_STATUS.set(0)
            return

        for product in products:
            product_id = str(product)

            if database.is_already_ingested(db_conn, product_id):
                continue

            logging.info(f"New data packet discovered: {product_id}")
            _ingest_product(db_conn, product, product_id)

    finally:
        db_conn.close()
        logging.info("Internal database socket closed securely.")


def _ingest_product(db_conn, product, product_id: str) -> None:
    """
    Downloads, validates, and archives a single product.

    Downloads to a private temp file inside INBOUND_DIR so that
    concurrent scheduler runs never share a write path. The temp file
    is atomically renamed to its final name only after the download
    completes successfully, preventing a partial write from being
    presented to the validation layer.
    """
    final_path = os.path.join(config.INBOUND_DIR, f"{product_id}.zip")
    tmp_fd, tmp_path = tempfile.mkstemp(dir=config.INBOUND_DIR, suffix=".tmp")

    try:
        # --- Download ---------------------------------------------------
        try:
            with os.fdopen(tmp_fd, "wb") as fdst, product.open() as fsrc:
                tmp_fd = None  # fdopen takes ownership; avoid double-close
                while True:
                    chunk = fsrc.read(1024 * 1024)
                    if not chunk:
                        break
                    fdst.write(chunk)
        except Exception as dl_err:
            logging.error(f"Download failed for {product_id}: {dl_err}")
            metrics.INGESTION_FAILURE.inc()
            database.record_quarantine(db_conn, product_id, reason=str(dl_err), status="ERROR")
            return
        finally:
            if tmp_fd is not None:
                os.close(tmp_fd)

        # Atomic rename: the file is now a complete ZIP before anything reads it
        os.replace(tmp_path, final_path)
        tmp_path = None  # cleanup guard — file has moved

        # --- Validation -------------------------------------------------
        try:
            validation.validate_sentinel_package(final_path)
        except validation.ValidationError as val_err:
            logging.error(f"Validation rejected {product_id}: {val_err}")
            metrics.INGESTION_FAILURE.inc()
            metrics.VALIDATION_REJECTED.inc()
            quarantine_path = os.path.join(config.QUARANTINE_DIR, f"{product_id}_REJECTED.zip")
            shutil.move(final_path, quarantine_path)
            logging.warning(f"Payload quarantined as: {product_id}_REJECTED.zip")
            database.record_quarantine(db_conn, product_id, reason=str(val_err), status="REJECTED")
            return

        # --- Ingest -----------------------------------------------------
        bytes_size = os.path.getsize(final_path)
        file_size_mb = bytes_size / (1024 * 1024)

        metrics.BYTES_DOWNLOADED.inc(bytes_size)
        metrics.INGESTION_SUCCESS.inc()

        database.record_ingestion(db_conn, product_id, file_size_mb)
        logging.info(f"SUCCESS: Ingested {product_id} ({file_size_mb:.2f} MB)")

        storage.archive_and_compress_payload(product_id, final_path)

    except Exception as err:
        logging.error(f"Unexpected error processing {product_id}: {err}")
        metrics.INGESTION_FAILURE.inc()
        for stale in (final_path, tmp_path):
            if stale and os.path.exists(stale):
                dest = os.path.join(config.QUARANTINE_DIR, f"{product_id}_ERR.zip")
                shutil.move(stale, dest)
                logging.warning(f"Stale file moved to quarantine: {dest}")
                break

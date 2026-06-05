import os
import logging
import shutil
from datetime import datetime, timedelta
import eumdac

import config
import database
import storage
import validation  # Imported the new validation engine

def run_ingestion():
    """Executes a single-pass ingestion iteration cycle."""
    logging.info("Waking up worker: Initiating single-pass ingestion cycle...")
    
    try:
        db_conn = database.get_db_connection()
    except Exception as db_err:
        logging.critical(f"CRITICAL: Failed to connect to database host {config.DB_HOST}: {db_err}")
        config.METRIC_SYSTEM_STATUS.set(0)
        raise db_err

    try:
        database.init_database(db_conn)
        storage.enforce_data_retention_policy()
        config.METRIC_SYSTEM_STATUS.set(1)
        
        consumer_key = os.getenv("EUMETSAT_CONSUMER_KEY")
        consumer_secret = os.getenv("EUMETSAT_CONSUMER_SECRET")
        
        if not consumer_key or not consumer_secret:
            logging.error("CRITICAL: Environment keys missing.")
            config.METRIC_SYSTEM_STATUS.set(0)
            return

        try:
            credentials = (consumer_key, consumer_secret)
            token = eumdac.AccessToken(credentials)
            datastore = eumdac.DataStore(token)
            collection = datastore.get_collection(config.COLLECTION_ID)
        except Exception as e:
            logging.error(f"CRITICAL: Handshake failed: {e}")
            config.METRIC_SYSTEM_STATUS.set(0)
            return

        start_time = datetime.now() - timedelta(days=1)
        
        try:
            with config.METRIC_API_LATENCY.time():
                products = list(collection.search(
                    dtstart=start_time,
                    geo="POLYGON((0 50, 10 50, 10 60, 0 60, 0 50))"
                ))
            logging.info(f"Discovery complete. Found {len(products)} products.")
        except Exception as e:
            logging.error(f"API Query Failure: {e}")
            config.METRIC_SYSTEM_STATUS.set(0)
            return

        for product in products:
            product_id = str(product)
            
            if database.is_already_ingested(db_conn, product_id):
                continue
                
            logging.info(f"New data packet discovered: {product_id}")
            download_path = os.path.join(config.INBOUND_DIR, f"{product_id}.zip")
            
            try:
                with product.open() as fsrc, open(download_path, mode='wb') as fdst:
                    while True:
                        chunk = fsrc.read(1024 * 1024)
                        if not chunk:
                            break
                        fdst.write(chunk)
                
                # ============================================================
                # 🔬 VALIDATION LAYER INTERCEPT
                # ============================================================
                try:
                    validation.validate_sentinel_package(download_path)
                except validation.ValidationError as val_err:
                    logging.error(f"❌ VALIDATION REJECTED for {product_id}: {val_err}")
                    # Escalates to the exception handler to securely isolate this asset
                    raise val_err
                # ============================================================

                bytes_size = os.path.getsize(download_path)
                file_size_mb = bytes_size / (1024 * 1024)
                
                config.METRIC_BYTES_DOWNLOADED.inc(bytes_size)
                config.METRIC_INGESTION_SUCCESS.inc()
                
                database.record_ingestion(db_conn, product_id, file_size_mb)
                logging.info(f"SUCCESS: Ingested {product_id} ({file_size_mb:.2f} MB)")
                
                storage.archive_and_compress_payload(product_id, download_path)
                
            except Exception as err:
                logging.error(f"Network processing failure or validation drop: {err}")
                config.METRIC_INGESTION_FAILURE.inc()
                
                # Dynamic routing based on the exception type
                if isinstance(err, validation.ValidationError):
                    config.METRIC_VALIDATION_REJECTED.inc()
                    quarantine_filename = f"{product_id}_REJECTED.zip"
                else:
                    quarantine_filename = f"{product_id}_ERR.zip"
                    
                if os.path.exists(download_path):
                    shutil.move(download_path, os.path.join(config.QUARANTINE_DIR, quarantine_filename))
                    logging.warning(f"Payload safely diverted to Quarantine Tier as: {quarantine_filename}")
                    
    finally:
        db_conn.close()
        logging.info("Internal database socket closed securely.")
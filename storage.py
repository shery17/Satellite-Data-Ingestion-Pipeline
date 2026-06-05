import os
import logging
import tarfile
from datetime import datetime
import config

def enforce_data_retention_policy():
    """Scans the warm storage archive and deletes folders older than the retention threshold."""
    logging.info("Executing storage retention check on Warm Storage tier...")
    now = datetime.now()
    purged_count = 0

    for file_name in os.listdir(config.ARCHIVE_DIR):
        file_path = os.path.join(config.ARCHIVE_DIR, file_name)
        file_modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
        
        if (now - file_modified_time) > config.RETENTION_PERIOD:
            logging.warning(f"RETENTION POLICY ENFORCED: Removing expired data packet '{file_name}'.")
            try:
                os.remove(file_path)
                purged_count += 1
            except Exception as e:
                logging.error(f"Failed to purge expired file {file_name}: {e}")
                
    logging.info(f"Retention policy sweep complete. Purged {purged_count} archives.")

def archive_and_compress_payload(product_id, source_file_path):
    """Compresses the raw zip/asset into a tarball and moves it to warm storage."""
    archive_tar_name = f"{product_id}.tar.gz"
    destination_path = os.path.join(config.ARCHIVE_DIR, archive_tar_name)
    
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
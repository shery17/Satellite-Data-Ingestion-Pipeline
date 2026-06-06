"""
Storage management for the satellite ingestion pipeline.

Handles two responsibilities:
  - Archiving validated inbound products to warm storage as .tar.gz
  - Enforcing the retention policy across both the archive and quarantine
    directories so neither grows unbounded
"""

import logging
import os
import tarfile
from datetime import datetime

import config


# ---------------------------------------------------------------------------
# Retention policy
# ---------------------------------------------------------------------------

def enforce_data_retention_policy() -> None:
    """
    Sweep both the archive and quarantine directories and remove files
    older than config.RETENTION_PERIOD.

    Both directories are swept because quarantined products (rejected or
    errored) should not accumulate on disk indefinitely any more than
    successfully archived products should.
    """
    dirs_to_sweep = {
        "archive":    config.ARCHIVE_DIR,
        "quarantine": config.QUARANTINE_DIR,
    }
    total_purged = 0

    for tier_name, directory in dirs_to_sweep.items():
        purged = _sweep_directory(directory, tier_name)
        total_purged += purged

    logging.info(f"Retention sweep complete. Total files purged: {total_purged}.")


def _sweep_directory(directory: str, tier_name: str) -> int:
    """
    Delete files in `directory` whose mtime exceeds RETENTION_PERIOD.
    Returns the number of files removed.
    """
    now = datetime.now()
    purged = 0

    for file_name in os.listdir(directory):
        file_path = os.path.join(directory, file_name)
        if not os.path.isfile(file_path):
            continue
        age = now - datetime.fromtimestamp(os.path.getmtime(file_path))
        if age > config.RETENTION_PERIOD:
            try:
                os.remove(file_path)
                purged += 1
                logging.warning(
                    f"Retention policy [{tier_name}]: removed '{file_name}' "
                    f"(age {age.days}d, threshold {config.RETENTION_PERIOD.days}d)."
                )
            except Exception as e:
                logging.error(f"Failed to remove '{file_name}' from {tier_name}: {e}")

    return purged


# ---------------------------------------------------------------------------
# Archival compression
# ---------------------------------------------------------------------------

def archive_and_compress_payload(product_id: str, source_file_path: str) -> None:
    """
    Compress the validated inbound file into a .tar.gz in ARCHIVE_DIR
    and remove the original.

    If compression fails the source file is still removed so it does not
    remain as an orphan in the inbound directory.
    """
    archive_name = f"{product_id}.tar.gz"
    destination_path = os.path.join(config.ARCHIVE_DIR, archive_name)

    logging.info(f"Archiving {product_id} → {archive_name}")
    try:
        with tarfile.open(destination_path, "w:gz") as tar:
            tar.add(source_file_path, arcname=os.path.basename(source_file_path))
        logging.info(f"Archival compression successful for {product_id}.")
    except Exception as e:
        logging.error(f"Archival compression failed for {product_id}: {e}")
    finally:
        # Always clean up the inbound file regardless of compression outcome
        if os.path.exists(source_file_path):
            os.remove(source_file_path)

"""
Tests for storage.py — data retention policy and archival compression.

No external dependencies. All filesystem operations run inside pytest's
tmp_path fixture so nothing touches the real data/ directories.
"""

import os
import sys
import tarfile
import time
from unittest.mock import patch

import pytest

# Ensure the project root is importable when running pytest from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage
import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(directory: str, name: str, content: bytes = b"data") -> str:
    """Write a file into directory and return its full path."""
    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _age_file(path: str, days: float) -> None:
    """Backdate a file's mtime by the given number of days."""
    old_time = time.time() - (days * 86400)
    os.utime(path, (old_time, old_time))


# ---------------------------------------------------------------------------
# enforce_data_retention_policy — archive directory
# ---------------------------------------------------------------------------

class TestRetentionArchive:

    def test_recent_file_is_kept(self, tmp_path, monkeypatch):
        """A file modified 1 day ago is within the 7-day window and must not be deleted."""
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(tmp_path / "q"))
        (tmp_path / "q").mkdir()

        f = _make_file(str(tmp_path), "recent.tar.gz")
        _age_file(f, days=1)

        storage.enforce_data_retention_policy()

        assert os.path.exists(f)

    def test_expired_file_is_deleted(self, tmp_path, monkeypatch):
        """A file older than RETENTION_PERIOD must be removed."""
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(tmp_path / "q"))
        (tmp_path / "q").mkdir()

        f = _make_file(str(tmp_path), "old.tar.gz")
        _age_file(f, days=8)

        storage.enforce_data_retention_policy()

        assert not os.path.exists(f)

    def test_file_just_under_boundary_is_kept(self, tmp_path, monkeypatch):
        """A file aged 6 days 23 hours is under the 7-day threshold and must be kept."""
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(tmp_path / "q"))
        (tmp_path / "q").mkdir()

        f = _make_file(str(tmp_path), "boundary.tar.gz")
        _age_file(f, days=6.99)

        storage.enforce_data_retention_policy()

        assert os.path.exists(f)

    def test_only_expired_files_are_deleted(self, tmp_path, monkeypatch):
        """Only files past the threshold are removed; recent ones survive."""
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(tmp_path / "q"))
        (tmp_path / "q").mkdir()

        old = _make_file(str(tmp_path), "old.tar.gz")
        new = _make_file(str(tmp_path), "new.tar.gz")
        _age_file(old, days=10)
        _age_file(new, days=2)

        storage.enforce_data_retention_policy()

        assert not os.path.exists(old)
        assert os.path.exists(new)

    def test_empty_archive_dir_does_not_raise(self, tmp_path, monkeypatch):
        """An empty archive directory should complete without errors."""
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(tmp_path / "q"))
        (tmp_path / "q").mkdir()

        storage.enforce_data_retention_policy()  # must not raise


# ---------------------------------------------------------------------------
# enforce_data_retention_policy — quarantine directory
# ---------------------------------------------------------------------------

class TestRetentionQuarantine:

    def test_expired_quarantine_file_is_deleted(self, tmp_path, monkeypatch):
        """Expired files in the quarantine directory must also be purged."""
        archive = tmp_path / "archive"
        archive.mkdir()
        quarantine = tmp_path / "quarantine"
        quarantine.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(quarantine))

        f = _make_file(str(quarantine), "S3A_PRODUCT_REJECTED.zip")
        _age_file(f, days=8)

        storage.enforce_data_retention_policy()

        assert not os.path.exists(f)

    def test_recent_quarantine_file_is_kept(self, tmp_path, monkeypatch):
        """A recently quarantined file within the window must not be deleted."""
        archive = tmp_path / "archive"
        archive.mkdir()
        quarantine = tmp_path / "quarantine"
        quarantine.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(quarantine))

        f = _make_file(str(quarantine), "S3A_PRODUCT_REJECTED.zip")
        _age_file(f, days=1)

        storage.enforce_data_retention_policy()

        assert os.path.exists(f)

    def test_archive_and_quarantine_swept_independently(self, tmp_path, monkeypatch):
        """An expired archive file and a recent quarantine file are handled correctly."""
        archive = tmp_path / "archive"
        archive.mkdir()
        quarantine = tmp_path / "quarantine"
        quarantine.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))
        monkeypatch.setattr(config, "QUARANTINE_DIR", str(quarantine))

        old_archive = _make_file(str(archive), "old.tar.gz")
        new_quarantine = _make_file(str(quarantine), "new_REJECTED.zip")
        _age_file(old_archive, days=10)
        _age_file(new_quarantine, days=1)

        storage.enforce_data_retention_policy()

        assert not os.path.exists(old_archive)
        assert os.path.exists(new_quarantine)


# ---------------------------------------------------------------------------
# archive_and_compress_payload
# ---------------------------------------------------------------------------

class TestArchiveAndCompress:

    def test_tar_gz_is_created_in_archive_dir(self, tmp_path, monkeypatch):
        """After archiving, a .tar.gz must appear in ARCHIVE_DIR."""
        archive = tmp_path / "archive"
        archive.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))

        source = _make_file(str(tmp_path), "product.zip", content=b"zip content")
        storage.archive_and_compress_payload("S3A_TEST_PRODUCT", source)

        assert (archive / "S3A_TEST_PRODUCT.tar.gz").exists()

    def test_source_file_is_removed_after_archiving(self, tmp_path, monkeypatch):
        """The original inbound file must be deleted once archived."""
        archive = tmp_path / "archive"
        archive.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))

        source = _make_file(str(tmp_path), "product.zip", content=b"zip content")
        storage.archive_and_compress_payload("S3A_TEST_PRODUCT", source)

        assert not os.path.exists(source)

    def test_tarball_contains_source_filename(self, tmp_path, monkeypatch):
        """The tarball must contain the original file under its basename."""
        archive = tmp_path / "archive"
        archive.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))

        source = _make_file(str(tmp_path), "product.zip", content=b"payload")
        storage.archive_and_compress_payload("S3A_TEST_PRODUCT", source)

        with tarfile.open(str(archive / "S3A_TEST_PRODUCT.tar.gz"), "r:gz") as tar:
            assert "product.zip" in tar.getnames()

    def test_tarball_content_matches_original_bytes(self, tmp_path, monkeypatch):
        """Extracted content must be byte-for-byte identical to the original."""
        archive = tmp_path / "archive"
        archive.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))

        payload = b"sentinel-3 olci wrr payload bytes"
        source = _make_file(str(tmp_path), "product.zip", content=payload)
        storage.archive_and_compress_payload("S3A_TEST_PRODUCT", source)

        with tarfile.open(str(archive / "S3A_TEST_PRODUCT.tar.gz"), "r:gz") as tar:
            extracted = tar.extractfile("product.zip").read()

        assert extracted == payload

    def test_source_cleaned_up_even_when_compression_fails(self, tmp_path, monkeypatch):
        """
        If tarfile.open raises (e.g. disk full), the source file must still
        be removed — it should not be left as an orphan in the inbound dir.
        """
        archive = tmp_path / "archive"
        archive.mkdir()
        monkeypatch.setattr(config, "ARCHIVE_DIR", str(archive))

        source = _make_file(str(tmp_path), "product.zip", content=b"data")

        with patch("tarfile.open", side_effect=OSError("simulated disk full")):
            storage.archive_and_compress_payload("S3A_TEST_PRODUCT", source)

        assert not os.path.exists(source)

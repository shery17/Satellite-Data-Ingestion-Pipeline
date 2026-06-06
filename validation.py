import os
import re
import hashlib
import logging
import zipfile
import xml.etree.ElementTree as ET
import netCDF4 as nc
import numpy as np

import config

# ============================================================
# Reference: S3IPF PDS 004.3 - i2r5, EUMETSAT, 11 Sep 2023
# Product Data Format Specification - OLCI Level 2 Marine
# Doc No: EUM/RSP/SPE/23/1363219, Issue v1B
# ============================================================

class ValidationError(Exception):
    """Raised when a product package fails a compliance check."""
    pass


# Mandatory files per Table 7-1 (§7.1.1.1).
# Reflectance files (Oa##_reflectance.nc) are excluded — they are
# marked N.O. (Not Operational) in the spec and may not be present.
_MANDATORY_MEASUREMENT_FILES = {
    "chlor_a.nc",   # OC4Me chlorophyll — named chlor_a.nc in collection 004 baseline
    "chl_nn.nc",
    "tsm_nn.nc",
    "trsp.nc",
    "iop_lsd.nc",
    "iop_nn.nc",
    "par.nc",
    "w_aer.nc",
    "iwv.nc",
}

_MANDATORY_ANNOTATION_FILES = {
    "wqsf.nc",
    "geo_coordinates.nc",
    "time_coordinates.nc",
    "tie_geo_coordinates.nc",
    "tie_geometries.nc",
    "tie_meteo.nc",
    "instrument_data.nc",
}

_MANDATORY_FILES = _MANDATORY_MEASUREMENT_FILES | _MANDATORY_ANNOTATION_FILES | {"xfdumanifest.xml"}

# Sentinel-3 OLCI product filename pattern (§3.2, S3IPF PDS 004.3).
# Groups: platform, level, type, sensing_start, sensing_stop
# Instance ID fields after the three timestamps can contain blanks (underscores)
# when optional fields are unpopulated — validated loosely after the timestamps.
_FILENAME_PATTERN = re.compile(
    r"^(S3[AB])_OL_(2)_(W[FR]R)_{4}"
    r"(\d{8}T\d{6})_(\d{8}T\d{6})_\d{8}T\d{6}_"
    r"[\w_]+\.SEN3$"
)

# Global attributes required in every NetCDF file (Table 4-1, §4.1.3.1).
_REQUIRED_NC_ATTRIBUTES = {"start_time", "stop_time", "absolute_orbit_number"}


def validate_sentinel_package(file_path: str) -> bool:
    """
    Master validation entry point for an OL_2_WFR/OL_2_WRR SAFE package.

    Runs six compliance tiers derived from S3IPF PDS 004.3 (EUMETSAT, Sep 2023):
      1. SAFE archive and mandatory file structure
      2. Manifest XML quality summary (invalid pixel threshold)
      3. NetCDF global attributes (start_time, stop_time, orbit number)
      4. Spatial dimension sanity (RR columns = 1217)
      5. WQSF flag metadata integrity (flag_meanings / flag_masks present)
      6. Geo-coordinate bounds (lat/lon physically valid ranges)

    Returns True on success. Raises ValidationError on any failure.
    """
    product_name = os.path.basename(file_path)
    logging.info(f"Starting validation for: {product_name}")

    if not zipfile.is_zipfile(file_path):
        raise ValidationError("Archive integrity failure: file is not a valid ZIP.")

    with zipfile.ZipFile(file_path, "r") as zf:
        file_list = zf.namelist()

        # ZIP files from EUMETSAT do not include explicit directory entries —
        # extract the .SEN3 root from the file paths themselves.
        root_dirs = set()
        for f in file_list:
            parts = f.replace("\\", "/").split("/")
            if len(parts) > 1 and parts[0].endswith(".SEN3"):
                root_dirs.add(parts[0] + "/")
        if not root_dirs:
            raise ValidationError("SAFE structure failure: no root '.SEN3' directory found.")
        root = next(iter(root_dirs))

        _check_mandatory_files(file_list, root, product_name)
        _check_manifest_quality(zf, root)
        _check_global_attributes(zf, root)
        _check_spatial_dimensions(zf, root)
        _check_wqsf_integrity(zf, root)
        _check_geo_bounds(zf, root)

    logging.info(f"Validation passed: {product_name}")
    return True


# ------------------------------------------------------------
# TIER 1 — SAFE Structure and Mandatory File Presence
# Source: Table 7-1, §7.1.1.1 (PDS 004.3)
# ------------------------------------------------------------

def _check_mandatory_files(file_list: list, root: str, product_name: str) -> None:
    """Verifies all mandatory files from Table 7-1 are present in the package."""
    present = {os.path.basename(f.rstrip("/")) for f in file_list}

    missing = []
    for required in _MANDATORY_FILES:
        if required not in present:
            missing.append(required)

    if missing:
        raise ValidationError(
            f"SAFE structure failure: {len(missing)} mandatory file(s) missing: "
            f"{', '.join(sorted(missing))}"
        )

    # Validate the product folder name matches the Sentinel-3 naming convention (§3.2)
    folder_name = root.rstrip("/\\").split("/")[-1]
    if not _FILENAME_PATTERN.match(folder_name):
        raise ValidationError(
            f"Naming convention failure: '{folder_name}' does not conform to "
            f"the Sentinel-3 OLCI L2 filename pattern (S3IPF PDS 004.3 §3.2)."
        )

    logging.info(f"Tier 1 passed: all {len(_MANDATORY_FILES)} mandatory files present.")


# ------------------------------------------------------------
# TIER 2 — Manifest XML Pixel Quality Summary
# Source: Table 7-2 pixelQualitySummary, §7.1.1.2.1 (PDS 004.3)
#
# The processor writes invalidPixels.percentage into the manifest.
# Reading it here avoids opening any NetCDF file for this check.
# ------------------------------------------------------------

def _check_manifest_quality(zf: zipfile.ZipFile, root: str) -> None:
    """
    Reads pre-computed quality statistics from xfdumanifest.xml and
    rejects products where invalid pixel coverage exceeds the configured
    threshold (config.VALIDATION_MAX_INVALID_PIXEL_PCT).
    """
    manifest_path = f"{root}xfdumanifest.xml"
    manifest_bytes = zf.read(manifest_path)
    tree = ET.fromstring(manifest_bytes)

    # The manifest uses a default namespace; strip it for simple XPath queries
    xml_str = manifest_bytes.decode("utf-8")
    # Find invalidPixels percentage — appears as:
    # <sentinel3:invalidPixels><sentinel3:percentage>X.X</sentinel3:percentage>
    # Namespace-agnostic search via iteration
    invalid_pct = None
    for elem in tree.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "invalidPixels":
            for child in elem:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child_tag == "percentage":
                    try:
                        invalid_pct = float(child.text)
                    except (TypeError, ValueError):
                        pass
                    break
            if invalid_pct is not None:
                break

    if invalid_pct is None:
        # Not all products populate this field; log a warning but do not reject
        logging.warning("Tier 2: invalidPixels.percentage not found in manifest — skipping threshold check.")
        return

    logging.info(f"Tier 2: manifest reports {invalid_pct:.1f}% invalid pixels.")

    if invalid_pct > config.VALIDATION_MAX_INVALID_PIXEL_PCT:
        raise ValidationError(
            f"Pixel quality failure: {invalid_pct:.1f}% of pixels are invalid "
            f"(threshold: {config.VALIDATION_MAX_INVALID_PIXEL_PCT}%). "
            f"Product is operationally unusable."
        )

    logging.info("Tier 2 passed: invalid pixel percentage within acceptable range.")


# ------------------------------------------------------------
# TIER 3 — NetCDF Global Attributes
# Source: Table 4-1, §4.1.3.1 (PDS 004.3)
# Required: start_time, stop_time, absolute_orbit_number
# ------------------------------------------------------------

def _check_global_attributes(zf: zipfile.ZipFile, root: str) -> None:
    """
    Verifies required global attributes are present in wqsf.nc.
    Checks start_time and stop_time parse as valid ISO-8601 timestamps.
    """
    nc_bytes = zf.read(f"{root}wqsf.nc")
    with nc.Dataset("wqsf_attr_check", mode="r", memory=nc_bytes) as ds:
        missing = [attr for attr in _REQUIRED_NC_ATTRIBUTES if attr not in ds.ncattrs()]
        if missing:
            raise ValidationError(
                f"Global attribute failure: {', '.join(missing)} missing from wqsf.nc. "
                f"Required by Table 4-1 (PDS 004.3 §4.1.3.1)."
            )

        # Validate timestamp format: yyyy-mm-ddThh:mm:ss.ssssssZ
        ts_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")
        for attr in ("start_time", "stop_time"):
            val = str(getattr(ds, attr, ""))
            if not ts_pattern.match(val):
                raise ValidationError(
                    f"Global attribute failure: '{attr}' value '{val}' does not match "
                    f"required format yyyy-mm-ddThh:mm:ss.ssssssZ (PDS 004.3 Table 4-1)."
                )

        logging.info(
            f"Tier 3 passed: orbit {ds.absolute_orbit_number}, "
            f"start={ds.start_time}, stop={ds.stop_time}."
        )


# ------------------------------------------------------------
# TIER 4 — Spatial Dimension Sanity Check
# Source: §7.1.1.3.1 and §7.1.1.4.1 (PDS 004.3)
# RR products: columns = 1217, FR products: columns = 4865
# ------------------------------------------------------------

def _check_spatial_dimensions(zf: zipfile.ZipFile, root: str) -> None:
    """
    Verifies the spatial grid dimensions of wqsf.nc match the expected
    column count for a Reduced Resolution (RR) product (1217 columns).
    """
    nc_bytes = zf.read(f"{root}wqsf.nc")
    with nc.Dataset("wqsf_dim_check", mode="r", memory=nc_bytes) as ds:
        if "rows" not in ds.dimensions or "columns" not in ds.dimensions:
            raise ValidationError(
                "Spatial dimension failure: 'rows' or 'columns' dimensions missing from wqsf.nc."
            )

        num_rows = len(ds.dimensions["rows"])
        num_cols = len(ds.dimensions["columns"])

        if num_rows == 0 or num_cols == 0:
            raise ValidationError(
                f"Spatial dimension failure: empty grid detected ({num_rows}x{num_cols})."
            )

        if num_cols != config.VALIDATION_RR_EXPECTED_COLUMNS:
            raise ValidationError(
                f"Spatial dimension failure: expected {config.VALIDATION_RR_EXPECTED_COLUMNS} columns "
                f"for an RR product but found {num_cols} (PDS 004.3 §7.1.1.4.1)."
            )

        logging.info(f"Tier 4 passed: spatial grid is {num_rows}x{num_cols} pixels.")


# ------------------------------------------------------------
# TIER 5 — WQSF Flag Metadata Integrity
# Source: Table 7-12/7-13, §7.1.1.4.1 (PDS 004.3)
#
# The spec requires flag_meanings and flag_masks to be stored as
# variable attributes on the WQSF variable. Reading bit definitions
# from the file (not hardcoding them) is the spec-compliant approach
# — flag coding has changed between collections and may change again.
# ------------------------------------------------------------

def _check_wqsf_integrity(zf: zipfile.ZipFile, root: str) -> None:
    """
    Verifies the WQSF variable exists and carries the flag_meanings and
    flag_masks attributes that define the bit encoding. Also confirms the
    expected classification flags (INVALID, WATER, LAND, CLOUD) are
    declared, as a minimum sanity check against a corrupted flags file.
    """
    nc_bytes = zf.read(f"{root}wqsf.nc")
    with nc.Dataset("wqsf_flag_check", mode="r", memory=nc_bytes) as ds:
        if "WQSF" not in ds.variables:
            raise ValidationError(
                "WQSF integrity failure: 'WQSF' variable not found in wqsf.nc."
            )

        wqsf_var = ds.variables["WQSF"]
        var_attrs = wqsf_var.ncattrs()

        if "flag_meanings" not in var_attrs:
            raise ValidationError(
                "WQSF integrity failure: 'flag_meanings' attribute missing from WQSF variable. "
                "Cannot determine flag bit encoding (PDS 004.3 §7.1.1.4.1)."
            )
        if "flag_masks" not in var_attrs:
            raise ValidationError(
                "WQSF integrity failure: 'flag_masks' attribute missing from WQSF variable. "
                "Cannot determine flag bit encoding (PDS 004.3 §7.1.1.4.1)."
            )

        # Verify the four core classification flags are declared (Table 7-13)
        declared_flags = str(wqsf_var.flag_meanings).split()
        required_flags = {"INVALID", "WATER", "LAND", "CLOUD"}
        missing_flags = required_flags - set(declared_flags)
        if missing_flags:
            raise ValidationError(
                f"WQSF integrity failure: core classification flag(s) not declared in "
                f"flag_meanings: {', '.join(sorted(missing_flags))} (PDS 004.3 Table 7-13)."
            )

        logging.info(
            f"Tier 5 passed: WQSF variable declares {len(declared_flags)} flags "
            f"with flag_meanings and flag_masks attributes."
        )


# ------------------------------------------------------------
# TIER 6 — Geo-Coordinate Bounds Check
# Source: §4.2.2.2, geo_coordinates.nc (PDS 004.3)
# Variables: latitude [-90, 90], longitude [-180, 180]
# ------------------------------------------------------------

def _check_geo_bounds(zf: zipfile.ZipFile, root: str) -> None:
    """
    Reads a sample of the latitude and longitude arrays from
    geo_coordinates.nc and verifies values are within physically
    valid ranges. Out-of-range values indicate a corrupted
    geolocation grid (a known L2 failure mode).
    """
    nc_bytes = zf.read(f"{root}geo_coordinates.nc")
    with nc.Dataset("geo_check", mode="r", memory=nc_bytes) as ds:
        if "latitude" not in ds.variables or "longitude" not in ds.variables:
            raise ValidationError(
                "Geo-coordinate failure: 'latitude' or 'longitude' variables missing "
                "from geo_coordinates.nc (PDS 004.3 §4.2.2.2)."
            )

        # Sample the first 100 rows to avoid loading the full grid into memory
        lat_var = ds.variables["latitude"]
        lon_var = ds.variables["longitude"]
        num_rows = lat_var.shape[0]
        sample_slice = slice(0, min(100, num_rows))

        lat_sample = np.ma.filled(lat_var[sample_slice, :], fill_value=0)
        lon_sample = np.ma.filled(lon_var[sample_slice, :], fill_value=0)

        if np.any(lat_sample < -90) or np.any(lat_sample > 90):
            raise ValidationError(
                "Geo-coordinate failure: latitude values outside valid range [-90, 90]. "
                "Geolocation grid is corrupted."
            )
        if np.any(lon_sample < -180) or np.any(lon_sample > 180):
            raise ValidationError(
                "Geo-coordinate failure: longitude values outside valid range [-180, 180]. "
                "Geolocation grid is corrupted."
            )

        lat_min, lat_max = float(lat_sample.min()), float(lat_sample.max())
        lon_min, lon_max = float(lon_sample.min()), float(lon_sample.max())
        logging.info(
            f"Tier 6 passed: geo bounds lat=[{lat_min:.2f}, {lat_max:.2f}] "
            f"lon=[{lon_min:.2f}, {lon_max:.2f}]."
        )

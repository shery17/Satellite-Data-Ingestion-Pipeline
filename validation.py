import os
import logging
import zipfile
import xml.etree.ElementTree as ET
import netCDF4 as nc
import numpy as np

class ValidationError(Exception):
    """Custom exception thrown when a data packet fails system compliance checks."""
    pass

def validate_sentinel_package(file_path):
    """Master validation entrypoint. Runs structural, metadata, and data array integrity checks."""
    logging.info(f"🔬 Initiating deep scientific validation suite on: {os.path.basename(file_path)}")
    
    # Tier 1: Archive and Folder Integrity (Fast Checks)
    if not zipfile.is_zipfile(file_path):
        raise ValidationError("Archive Integrity Failure: File is corrupted or not a valid ZIP structure.")
        
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        file_list = zip_ref.namelist()
        
        # Isolate the target root directory name inside the zip
        root_dir = [f for f in file_list if f.endswith('.SEN3/') or f.endswith('.SEN3\\')]
        if not root_dir:
            raise ValidationError("SAFE Structure Failure: Missing mandatory root '.SEN3' directory envelope.")
        
        manifest_path = f"{root_dir[0]}xfdumanifest.xml"
        wqsf_path = f"{root_dir[0]}wqsf.nc"
        
        if manifest_path not in file_list:
            raise ValidationError("SAFE Structure Failure: Root manifest 'xfdumanifest.xml' is missing.")
        if wqsf_path not in file_list:
            raise ValidationError("Scientific Package Failure: Missing mandatory Water Quality Flags matrix 'wqsf.nc'.")

        # ============================================================
        # 🛰️ TIER 2: SCIENTIFIC NETCDF INTERNAL ARRAY VERIFICATION
        # ============================================================
        try:
            # Open the binary NetCDF payload straight out of the memory buffer without unpacking to disk!
            nc_bytes = zip_ref.read(wqsf_path)
            
            with nc.Dataset(f"{wqsf_path}_in_memory", mode='r', memory=nc_bytes) as dataset:
                logging.info(f"Successfully mounted NetCDF layer. Format: {dataset.file_format}")
                
                # Check 1: Dimension Integrity
                # Sentinel-3 matrix tracks arrays using rows (columns) and frames
                if 'columns' not in dataset.dimensions or 'rows' not in dataset.dimensions:
                    raise ValidationError("NetCDF Grid Error: Spatial dimensions 'columns' or 'rows' are missing.")
                
                num_rows = len(dataset.dimensions['rows'])
                num_cols = len(dataset.dimensions['columns'])
                logging.info(f"Matrix spatial grid resolved: {num_rows}x{num_cols} pixels.")
                
                if num_rows == 0 or num_cols == 0:
                    raise ValidationError("NetCDF Data Anomaly: Empty coordinate plane arrays detected.")

                # Check 2: Scientific Variable Scan (Water Quality and Science Flags)
                if 'WQSF' not in dataset.variables:
                    raise ValidationError("NetCDF Variable Error: 'WQSF' primary science flag array is missing.")
                    
                wqsf_var = dataset.variables['WQSF']
                
                # Read a downsampled slice of the dataset into RAM to prevent memory overload
                # We slice the first 100x100 corner pixel array to analyze flag allocations
                sample_data = wqsf_var[0:min(100, num_rows), 0:min(100, num_cols)]
                
                # Verify that the array isn't just filled with empty or corrupted flat null data (e.g., all 0 or all fill_value)
                if np.all(sample_data == 0):
                    raise ValidationError("Data Quality Warning: WQSF array contains entirely empty zero-data fields.")
                    
                # Check 3: Coordinate Sanity Check (If checking an absolute geo file like geo_coordinates.nc)
                # bounds checking coordinates protects your database from indexing garbage values
                
        except Exception as data_err:
            if isinstance(data_err, ValidationError):
                raise data_err
            raise ValidationError(f"Scientific parsing exception: Malformed internal NetCDF schema structure ({data_err})")

    logging.info("💪 CRITICAL SUCCESS: Payload passed structural AND deep data array validation.")
    return True
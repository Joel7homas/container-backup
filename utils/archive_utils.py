#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Archive utilities for service-oriented Docker backup system.
Provides functions for archive operations including compression and extraction.
"""

import os
import shutil
import tarfile
import zipfile
import glob
from pathlib import Path
from typing import List, Optional, Union, Tuple, Set

# Import logger from parent directory
import sys
sys.path.append(str(Path(__file__).parent.parent))
from logger import get_logger

logger = get_logger(__name__)


def compress_directory(directory: str, output_file: str) -> bool:
    """
    Compress a directory to a file.
    
    Args:
        directory (str): Directory to compress.
        output_file (str): Output file path.
        
    Returns:
        bool: True if successful, False otherwise.
    """
    directory = Path(directory)
    output_file = Path(output_file)
    
    if not directory.exists():
        logger.error(f"Directory does not exist: {directory}")
        return False
    
    try:
        # Determine compression type based on extension
        suffix = output_file.suffix.lower()
        if suffix in ['.tgz', '.tar.gz', '.gz']:
            return create_tar_gz(directory, output_file)
        elif suffix == '.zip':
            return create_zip(directory, output_file)
        else:
            logger.error(f"Unsupported compression format: {suffix}")
            return False
            
    except Exception as e:
        logger.error(f"Error compressing directory {directory}: {str(e)}")
        return False


def extract_archive(archive_file: str, output_dir: str) -> bool:
    """
    Extract an archive to a directory.
    
    Args:
        archive_file (str): Archive file path.
        output_dir (str): Output directory path.
        
    Returns:
        bool: True if successful, False otherwise.
    """
    archive_file = Path(archive_file)
    output_dir = Path(output_dir)
    
    if not archive_file.exists():
        logger.error(f"Archive file does not exist: {archive_file}")
        return False
    
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        # Determine archive type based on extension
        suffix = archive_file.suffix.lower()
        
        if suffix == '.zip':
            with zipfile.ZipFile(archive_file, 'r') as zip_ref:
                zip_ref.extractall(output_dir)
                logger.info(f"Extracted ZIP archive to {output_dir}")
                return True
                
        elif archive_file.name.endswith(('.tar.gz', '.tgz')) or suffix == '.gz':
            with tarfile.open(archive_file, 'r:gz') as tar_ref:
                tar_ref.extractall(output_dir)
                logger.info(f"Extracted TAR.GZ archive to {output_dir}")
                return True
                
        else:
            logger.error(f"Unsupported archive format: {suffix}")
            return False
            
    except Exception as e:
        logger.error(f"Error extracting archive {archive_file}: {str(e)}")
        return False


def create_tar_gz(source_dir: str, output_file: str, 
                 exclusions: Optional[List[str]] = None) -> bool:
    """
    Create a tar.gz archive.
    
    Args:
        source_dir (str): Source directory.
        output_file (str): Output file path.
        exclusions (list, optional): Exclusion patterns.
        
    Returns:
        bool: True if successful, False otherwise.
    """
    source_dir = Path(source_dir)
    output_file = Path(output_file)
    exclusions = exclusions or []
    
    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        return False
    
    try:
        # Create parent directory for output file if it doesn't exist
        os.makedirs(output_file.parent, exist_ok=True)
        
        # Process exclusions
        excluded_files = _get_excluded_files(source_dir, exclusions)
        
        # Create tar.gz archive
        with tarfile.open(output_file, 'w:gz') as tar:
            for item in source_dir.rglob('*'):
                if item.is_file() and item not in excluded_files:
                    arcname = item.relative_to(source_dir)
                    tar.add(item, arcname=arcname)
                    logger.debug(f"Added to archive: {arcname}")
        
        logger.info(f"Created tar.gz archive: {output_file}")
        return True
        
    except Exception as e:
        logger.error(f"Error creating tar.gz archive: {str(e)}")
        return False


def create_zip(source_dir: str, output_file: str, 
              exclusions: Optional[List[str]] = None) -> bool:
    """
    Create a zip archive.
    
    Args:
        source_dir (str): Source directory.
        output_file (str): Output file path.
        exclusions (list, optional): Exclusion patterns.
        
    Returns:
        bool: True if successful, False otherwise.
    """
    source_dir = Path(source_dir)
    output_file = Path(output_file)
    exclusions = exclusions or []
    
    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        return False
    
    try:
        # Create parent directory for output file if it doesn't exist
        os.makedirs(output_file.parent, exist_ok=True)
        
        # Process exclusions
        excluded_files = _get_excluded_files(source_dir, exclusions)
        
        # Create zip archive
        with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for item in source_dir.rglob('*'):
                if item.is_file() and item not in excluded_files:
                    arcname = item.relative_to(source_dir)
                    zipf.write(item, arcname)
                    logger.debug(f"Added to archive: {arcname}")
        
        logger.info(f"Created zip archive: {output_file}")
        return True
        
    except Exception as e:
        logger.error(f"Error creating zip archive: {str(e)}")
        return False


def _get_excluded_files(base_dir: Path, exclusion_patterns: List[str]) -> Set[Path]:
    """
    Get set of files to exclude based on patterns.
    
    Args:
        base_dir (Path): Base directory.
        exclusion_patterns (list): List of glob patterns for exclusion.
        
    Returns:
        set: Set of Path objects to exclude.
    """
    excluded_files = set()
    
    for pattern in exclusion_patterns:
        # Normalize pattern
        pattern = pattern.replace('/', os.sep).replace('\\', os.sep)
        
        # Make pattern absolute if not already
        if not pattern.startswith(os.sep):
            pattern = os.path.join(str(base_dir), pattern)
            
        # Get matching files
        for match in glob.glob(pattern, recursive=True):
            excluded_files.add(Path(match))
    
    logger.debug(f"Excluded {len(excluded_files)} files/directories")
    return excluded_files

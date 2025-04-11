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
    Extract an archive to a directory with optimized handling for large files.
    
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
    try:
        os.makedirs(output_dir, exist_ok=True)
    except (PermissionError, OSError) as e:
        logger.error(f"Failed to create output directory {output_dir}: {str(e)}")
        return False
    
    # Create a temporary extraction directory to ensure consistency
    temp_dir = Path(f"{output_dir}.tmp")
    
    try:
        # Remove temp dir if it exists from a previous failed attempt
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            
        # Create temporary directory
        os.makedirs(temp_dir, exist_ok=True)
        
        # Get archive size for progress reporting
        archive_size = archive_file.stat().st_size
        logger.debug(f"Extracting archive: {archive_file} ({archive_size / (1024*1024):.2f} MB)")
        
        # Determine archive type based on extension
        suffix = archive_file.suffix.lower()
        
        if suffix == '.zip':
            with zipfile.ZipFile(archive_file, 'r') as zip_ref:
                # Get file count for progress reporting
                file_count = len(zip_ref.infolist())
                logger.debug(f"ZIP archive contains {file_count} files")
                
                # Extract file by file with progress reporting
                for i, file_info in enumerate(zip_ref.infolist()):
                    zip_ref.extract(file_info, temp_dir)
                    if file_count > 100 and i % int(file_count / 10) == 0:
                        logger.debug(f"Extraction progress: {(i / file_count) * 100:.1f}%")
                
                logger.info(f"Extracted ZIP archive to {output_dir}")
                
        elif archive_file.name.endswith(('.tar.gz', '.tgz')) or suffix == '.gz':
            with tarfile.open(archive_file, 'r:gz') as tar_ref:
                # Get member count for progress logging
                members = tar_ref.getmembers()
                member_count = len(members)
                logger.debug(f"TAR archive contains {member_count} members")
                
                # Extract file by file with progress reporting
                for i, member in enumerate(members):
                    tar_ref.extract(member, temp_dir)
                    if member_count > 100 and i % int(member_count / 10) == 0:
                        logger.debug(f"Extraction progress: {(i / member_count) * 100:.1f}%")
                
                logger.info(f"Extracted TAR.GZ archive to {output_dir}")
                
        else:
            logger.error(f"Unsupported archive format: {suffix}")
            shutil.rmtree(temp_dir)
            return False
        
        # If existing output directory has content, move it to a backup
        if output_dir.exists() and any(output_dir.iterdir()):
            backup_dir = Path(f"{output_dir}.bak")
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.move(output_dir, backup_dir)
            logger.debug(f"Created backup of existing contents at {backup_dir}")
        
        # Move temporary directory to final location
        # First ensure the output directory exists but is empty
        if output_dir.exists():
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        
        # Move content from temp dir to final location
        for item in temp_dir.iterdir():
            shutil.move(str(item), str(output_dir))
        
        # Clean up temporary directory
        shutil.rmtree(temp_dir)
        
        return True
            
    except Exception as e:
        logger.error(f"Error extracting archive {archive_file}: {str(e)}")
        # Clean up temp dir if exists
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        return False

def create_tar_gz(source_dir: str, output_file: str, 
                 exclusions: Optional[List[str]] = None) -> bool:
    """
    Create a tar.gz archive with optimized handling for large files.
    
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
    
    # Create a temporary output file to ensure atomic writes
    temp_output_file = Path(f"{output_file}.tmp")
    
    try:
        # Create parent directory for output file if it doesn't exist
        os.makedirs(output_file.parent, exist_ok=True)
        
        # Process exclusions
        excluded_files = _get_excluded_files(source_dir, exclusions)
        
        # Estimate archive size for progress reporting
        total_size = 0
        file_count = 0
        for item in source_dir.rglob('*'):
            if item.is_file() and item not in excluded_files:
                total_size += item.stat().st_size
                file_count += 1
        
        # Create tar.gz archive with streaming (chunk-based processing)
        logger.debug(f"Archiving approximately {file_count} files ({total_size / (1024*1024):.2f} MB)")
        
        # Set compression level based on file size
        # Use faster compression for large archives
        compression_level = 1 if total_size > 100 * 1024 * 1024 else 6
        
        processed_size = 0
        with tarfile.open(temp_output_file, f'w:gz', compresslevel=compression_level) as tar:
            for item in source_dir.rglob('*'):
                if item.is_file() and item not in excluded_files:
                    arcname = item.relative_to(source_dir)
                    tar.add(item, arcname=arcname)
                    
                    processed_size += item.stat().st_size
                    if total_size > 0:
                        progress = (processed_size / total_size) * 100
                        if file_count > 100 and int(progress) % 10 == 0:
                            logger.debug(f"Archive progress: {progress:.1f}% ({processed_size / (1024*1024):.2f} MB)")
        
        # Move the temporary file to the final location (atomic operation)
        shutil.move(temp_output_file, output_file)
        
        logger.info(f"Created tar.gz archive: {output_file} ({os.path.getsize(output_file) / (1024*1024):.2f} MB)")
        return True
        
    except Exception as e:
        logger.error(f"Error creating tar.gz archive: {str(e)}")
        # Clean up temporary file if exists
        if temp_output_file.exists():
            try:
                temp_output_file.unlink()
            except Exception:
                pass
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

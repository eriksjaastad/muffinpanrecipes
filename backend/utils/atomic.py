"""
Atomic file writing utilities.

This module provides safe file write operations using the temp-and-rename pattern,
preventing data loss from interrupted writes.
"""

import os
import tempfile
import shutil
from pathlib import Path
from typing import Union
from send2trash import send2trash

from backend.utils.logging import get_logger

logger = get_logger(__name__)


def atomic_write(path: Union[str, Path], content: str) -> None:
    """
    Write file atomically using temp-and-rename pattern.
    
    This ensures that if the process is interrupted, the original file
    remains unchanged and intact.
    
    Args:
        path: Target file path (can be string or Path)
        content: Content to write
        
    Raises:
        OSError: If write or rename fails
        
    Example:
        >>> atomic_write("/tmp/data.json", '{"key": "value"}')
    """
    path = Path(path)
    
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Create temp file in same directory for atomic rename
        fd, temp_path = tempfile.mkstemp(
            dir=path.parent,
            suffix='.tmp',
            prefix=f'.{path.name}'
        )
        
        try:
            # Write to temp file
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Atomic rename (platform-dependent behavior)
            # On POSIX: atomic if on same filesystem
            # On Windows: dest must not exist (ensured by mkstemp)
            shutil.move(temp_path, str(path))
            
            logger.debug(f"Atomically wrote: {path}")
            
        except Exception as e:
            # Clean up temp file on failure (send to trash, don't hard delete)
            try:
                send2trash(temp_path)
            except Exception:
                pass
            raise
            
    except Exception as e:
        logger.error(f"Failed to atomically write {path}: {e}")
        raise


def atomic_write_json(path: Union[str, Path], data: dict, **json_kwargs) -> None:
    """
    Write JSON file atomically.
    
    Convenience wrapper for atomic_write with JSON serialization.
    
    Args:
        path: Target file path
        data: Dictionary to serialize to JSON
        **json_kwargs: Additional kwargs for json.dump (indent, sort_keys, etc.)
        
    Example:
        >>> atomic_write_json("/tmp/config.json", {"debug": True}, indent=2)
    """
    import json
    
    # Default to pretty-printed JSON
    if 'indent' not in json_kwargs:
        json_kwargs['indent'] = 2
    
    content = json.dumps(data, **json_kwargs)
    atomic_write(path, content)

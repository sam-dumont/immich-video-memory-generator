"""Run ID generation utilities."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime


def generate_run_id(timestamp: datetime | None = None) -> str:
    """Generate a unique run ID.

    Format: YYYYMMDD_HHMMSS_XXXX
    - YYYYMMDD_HHMMSS: Timestamp for human readability and sorting
    - XXXX: 4-character hash for uniqueness within the same second

    Args:
        timestamp: Optional timestamp to use. Defaults to now.

    Returns:
        A unique run ID string.

    Examples:
        >>> run_id = generate_run_id()
        >>> print(run_id)  # e.g., "20260105_143052_a7b3"
    """
    if timestamp is None:
        timestamp = datetime.now()

    # Format timestamp
    ts_str = timestamp.strftime("%Y%m%d_%H%M%S")

    # Generate 4-char hash from random bytes + timestamp for uniqueness
    # This handles the case where multiple runs start in the same second
    random_bytes = os.urandom(8)
    hash_input = f"{ts_str}{random_bytes.hex()}".encode()
    hash_suffix = hashlib.sha256(hash_input).hexdigest()[:4]

    return f"{ts_str}_{hash_suffix}"


def parse_run_id(run_id: str) -> datetime | None:
    """Parse a run ID to extract its timestamp.

    Args:
        run_id: A run ID in the format YYYYMMDD_HHMMSS_XXXX

    Returns:
        The datetime from the run ID, or None if parsing fails.

    Examples:
        >>> ts = parse_run_id("20260105_143052_a7b3")
        >>> print(ts)  # 2026-01-05 14:30:52
    """
    try:
        # Extract timestamp portion (first 15 characters: YYYYMMDD_HHMMSS)
        ts_str = run_id[:15]
        return datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
    except (ValueError, IndexError):
        return None


def is_valid_run_id(run_id: str) -> bool:
    """Check if a string is a valid run ID format.

    Args:
        run_id: String to check.

    Returns:
        True if the string matches the run ID format.

    Examples:
        >>> is_valid_run_id("20260105_143052_a7b3")
        True
        >>> is_valid_run_id("invalid")
        False
    """
    if len(run_id) != 20:  # YYYYMMDD_HHMMSS_XXXX = 8+1+6+1+4 = 20
        return False

    # Check format: YYYYMMDD_HHMMSS_XXXX
    parts = run_id.split("_")
    if len(parts) != 3:
        return False

    date_part, time_part, hash_part = parts

    # Validate lengths
    if len(date_part) != 8 or len(time_part) != 6 or len(hash_part) != 4:
        return False

    # Validate that timestamp is parseable
    return parse_run_id(run_id) is not None

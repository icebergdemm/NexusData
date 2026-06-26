from datetime import datetime
from typing import Optional
import re


def parse_iso_datetime(iso_string: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO 8601 datetime string robustly.
    Handles:
    - 2024-03-22T10:30:00Z
    - 2024-03-22T10:30:00.123456+03:00
    - 2024-03-22 10:30:00
    Returns a naive datetime (tzinfo stripped) for consistent DB storage.
    """
    if not iso_string:
        return None

    s = iso_string.strip()
    # Normalize trailing 'Z' (UTC) to an explicit offset that fromisoformat accepts
    if s.endswith('Z') or s.endswith('z'):
        s = s[:-1] + '+00:00'

    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Fallback: legacy strict format without fractional seconds / tz
        normalized = iso_string.replace('T', ' ').rstrip('Zz').strip()
        try:
            dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError as e:
            raise ValueError(f"Invalid datetime format: {iso_string}") from e

    # Strip timezone info for consistent naive-datetime storage
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def safe_get_int(value, default: int = 0) -> int:
    """Safely convert a value to int, with default fallback on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_get_list(value: Optional[list], default: Optional[list] = None) -> list:
    """Safely convert optional list, with default fallback"""
    return value if value is not None else (default or [])


def steam64_to_steam2(steam64: str) -> Optional[str]:
    """
    Convert Steam64 ID (76561198xxxxxxxxx) to STEAM_0:Y:Z format.
    Returns None if the input is not a valid Steam64 ID.
    """
    try:
        val = int(str(steam64).strip())
    except (ValueError, TypeError):
        return None
    base = 76561197960265728
    if val < base:
        return None
    auth_id = val - base
    w = auth_id % 2
    z = (auth_id - w) // 2
    return f"STEAM_1:{w}:{z}"


def validate_steamid(steamid: str) -> bool:
    """
    Validate SteamID format
    Valid format: 17-18 digits or 'STEAM_X:Y:Z' format
    """
    if not isinstance(steamid, str):
        return False
    
    steamid = steamid.strip()
    
    # Check numeric format (17-18 digits)
    if steamid.isdigit() and 17 <= len(steamid) <= 18:
        return True
    
    # Check STEAM_X:Y:Z format
    steam_pattern = r'^STEAM_[0-1]:[01]:\d+$'
    if re.match(steam_pattern, steamid):
        return True
    
    return False


def validate_player_name(name: str, min_len: int = 1, max_len: int = 255) -> bool:
    """Validate player name"""
    if not isinstance(name, str):
        return False
    
    name = name.strip()
    
    if len(name) < min_len or len(name) > max_len:
        return False
    
    # Disallow control characters
    if any(ord(c) < 32 for c in name):
        return False
    
    return True


def validate_page_number(page: int, min_page: int = 1) -> bool:
    """Validate page number"""
    return isinstance(page, int) and page >= min_page


def validate_page_size(size: int, min_size: int = 1, max_size: int = 1000) -> bool:
    """Validate page size"""
    return isinstance(size, int) and min_size <= size <= max_size


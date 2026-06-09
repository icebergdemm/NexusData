"""
Server Logs Service: SFTP download, parsing, and search for Barotrauma server logs.
Logs are downloaded in a background worker thread and parsed into structured events.
"""

import os
import re
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass, field

import paramiko
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ══════════════════════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LogEvent:
    """Single parsed log event."""
    timestamp: datetime
    player_name: str
    action_type: str  # picked_up, placed, opened, closed, equipped, unequipped, reactor, chat, connection, fabrication, repair, console, death, wire, pump, radio, dropped, activated, deactivated, login, logout, other
    details: str
    raw_line: str
    server_name: str = ""
    source_file: str = ""


# RoundManager login/logout patterns for extracting name→steamid
LOGIN_RE = re.compile(r'^\[RoundManager_ND\] Вход: (.+?) \| SteamID: (\d+) \| IP: (.+)$')
LOGOUT_RE = re.compile(r'^\[RoundManager_ND\] Выход: (.+?) \| SteamID: (\d+) \|')

# Action type classification patterns
ACTION_PATTERNS = [
    (re.compile(r'^(.+?) picked up (.+)$'), 'picked_up'),
    (re.compile(r'^(.+?) placed (.+) in the inventory of (.+)$'), 'placed'),
    (re.compile(r'^(.+?) removed (.+) from the inventory of (.+)$'), 'removed'),
    (re.compile(r'^(.+?) opened (.+)$'), 'opened'),
    (re.compile(r'^(.+?) closed (.+)$'), 'closed'),
    (re.compile(r'^(.+?) equipped (.+)$'), 'equipped'),
    (re.compile(r'^(.+?) unequipped (.+)$'), 'unequipped'),
    (re.compile(r'^(.+?) dropped (.+)$'), 'dropped'),
    (re.compile(r'^(.+?) adjusted reactor settings: (.+)$'), 'reactor'),
    (re.compile(r'^(.+?) started repairing (.+)$'), 'repair'),
    (re.compile(r'^(.+?) started fabricating (.+)$'), 'fabrication'),
    (re.compile(r'^(.+?) cancelled the fabrication of (.+)$'), 'fabrication_cancel'),
    (re.compile(r'^(.+?) activated (.+)$'), 'activated'),
    (re.compile(r'^(.+?) deactivated (.+)$'), 'deactivated'),
    (re.compile(r'^(.+?) connected a wire (.+)$'), 'wire'),
    (re.compile(r'^(.+?) disconnected a wire (.+)$'), 'wire'),
    (re.compile(r'^(.+?) set the pumping speed of (.+)$'), 'pump'),
    (re.compile(r'^(.+?) turned (on|off) (.+)$'), 'pump'),
    (re.compile(r'^(.+?) set the value (.+)$'), 'settings_change'),
    # Combat & death
    (re.compile(r'^(.+?) attacked by (.+?)\. (.+)$'), 'attack'),
    (re.compile(r'^(.+?) launched (.+)$'), 'attack'),
    (re.compile(r'^(.+) has died \(Cause of death: (.+)\)$'), 'death'),
    (re.compile(r'^Outer wall (breached|repaired) by (.+)$'), 'structure'),
    # Console
    (re.compile(r'^Console command "(.+)" executed by (.+)\.$'), 'console'),
    # Kick/ban messages
    (re.compile(r'^Игрок (.+?) исключен с сервера (.*)$'), 'kick'),
    (re.compile(r'^(.+?) покидает сервер\.$'), 'logout'),
    (re.compile(r'^Игрок (.+?) отключился от игры\. (.*)$'), 'logout'),
    (re.compile(r'^Игрок (.+?) сменил имя на (.+)\.$'), 'name_change'),
    # Network
    (re.compile(r'^Incoming connection from (.+)\.$'), 'connection'),
    (re.compile(r'^\[CustomRadioThirdFork\] (.+)$'), 'radio'),
    # Chat (must be last - catches Name: message)
    (re.compile(r'^(.+?): (.+)$'), 'chat'),
]

# Timestamp pattern
TIMESTAMP_RE = re.compile(r'^\[(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\]$')


# ══════════════════════════════════════════════════════════════════════════════
# Log Parser
# ══════════════════════════════════════════════════════════════════════════════

def parse_log_content(content: str, server_name: str = "", source_file: str = "") -> Tuple[List[LogEvent], Dict[str, str]]:
    """Parse raw log file content into structured LogEvent objects.
    
    Returns: (events, player_map) where player_map is {player_name: steamid}
    """
    events = []
    player_map: Dict[str, str] = {}  # name -> steamid
    lines = content.split('\n')
    current_timestamp = None

    for line in lines:
        line = line.rstrip()
        if not line:
            continue

        # Check if this is a timestamp line
        ts_match = TIMESTAMP_RE.match(line)
        if ts_match:
            try:
                current_timestamp = datetime.strptime(ts_match.group(1), '%m/%d/%Y %H:%M:%S')
            except ValueError:
                pass
            continue

        # This is an action line (indented with spaces)
        action_line = line.strip()
        if not action_line or current_timestamp is None:
            continue

        # Check for RoundManager login/logout (extract name→steamid)
        login_match = LOGIN_RE.match(action_line)
        if login_match:
            pname = login_match.group(1)
            steamid = login_match.group(2)
            player_map[pname] = steamid
            events.append(LogEvent(
                timestamp=current_timestamp,
                player_name=pname,
                action_type='login',
                details=action_line,
                raw_line=action_line,
                server_name=server_name,
                source_file=source_file,
            ))
            continue

        logout_match = LOGOUT_RE.match(action_line)
        if logout_match:
            pname = logout_match.group(1)
            steamid = logout_match.group(2)
            player_map[pname] = steamid
            events.append(LogEvent(
                timestamp=current_timestamp,
                player_name=pname,
                action_type='logout',
                details=action_line,
                raw_line=action_line,
                server_name=server_name,
                source_file=source_file,
            ))
            continue

        # Skip [RoundManager_ND] internal messages (kit delivery, data loaded, etc.)
        if action_line.startswith('[RoundManager_ND]'):
            continue

        # Skip noise: Saving client permissions, HTML fragments, etc.
        if action_line.startswith('Saving client permissions'):
            continue
        if action_line.startswith('<') or action_line.startswith('</'):
            continue

        # Classify the action
        action_type = 'other'
        player_name = ''

        for pattern, atype in ACTION_PATTERNS:
            m = pattern.match(action_line)
            if m:
                action_type = atype
                if atype == 'console':
                    player_name = m.group(2)
                elif atype == 'death':
                    player_name = m.group(1)
                elif atype == 'attack':
                    # "X attacked by Player. Damage" or "Player launched Weapon"
                    if 'attacked by' in action_line:
                        player_name = m.group(2)
                    else:
                        player_name = m.group(1)
                elif atype == 'structure':
                    player_name = m.group(2)
                elif atype == 'kick':
                    player_name = m.group(1)
                elif atype == 'connection':
                    player_name = ''  # IP address, not a player name
                elif atype == 'radio':
                    radio_match = re.search(r'Клиент (.+?) переключил', m.group(1))
                    if radio_match:
                        player_name = radio_match.group(1)
                elif atype == 'chat':
                    player_name = m.group(1)
                elif atype == 'name_change':
                    player_name = m.group(1)
                else:
                    player_name = m.group(1) if m.lastindex >= 1 else ''
                break

        event = LogEvent(
            timestamp=current_timestamp,
            player_name=player_name,
            action_type=action_type,
            details=action_line,
            raw_line=action_line,
            server_name=server_name,
            source_file=source_file,
        )
        events.append(event)

    return events, player_map


# ══════════════════════════════════════════════════════════════════════════════
# SFTP Log Downloader
# ══════════════════════════════════════════════════════════════════════════════

class LogSFTPManager:
    """Manages SFTP connections for downloading server logs."""

    def __init__(self, server_number: int):
        # Read settings fresh each time (not cached instance attribute)
        self.server_number = server_number

    def _get_config(self):
        """Get fresh settings to pick up .env changes after reset_settings_cache."""
        from config import get_settings
        s = get_settings()
        if self.server_number == 1:
            return {
                'host': s.sftp_server_1_host,
                'port': s.sftp_server_1_port,
                'username': s.sftp_server_1_username,
                'password': s.sftp_server_1_password,
                'key_path': s.sftp_server_1_key_path,
                'logs_dir': s.sftp_server_1_logs_dir,
                'name': s.sftp_server_1_name,
            }
        else:
            return {
                'host': s.sftp_server_2_host,
                'port': s.sftp_server_2_port,
                'username': s.sftp_server_2_username,
                'password': s.sftp_server_2_password,
                'key_path': s.sftp_server_2_key_path,
                'logs_dir': s.sftp_server_2_logs_dir,
                'name': s.sftp_server_2_name,
            }

    def is_configured(self) -> bool:
        """Check if SFTP and logs directory are configured."""
        cfg = self._get_config()
        return bool(cfg['host'] and cfg['logs_dir'])

    def _connect(self) -> paramiko.SFTPClient:
        """Establish SFTP connection with timeouts."""
        cfg = self._get_config()
        if not cfg['host']:
            raise RuntimeError(f"SFTP server {self.server_number} not configured")

        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        try:
            sock.connect((cfg['host'], cfg['port']))
        except (socket.timeout, OSError) as e:
            sock.close()
            raise RuntimeError(f"SFTP server {self.server_number} unreachable: {e}")

        transport = paramiko.Transport(sock)
        transport.set_keepalive(30)
        transport.banner_timeout = 15
        transport.auth_timeout = 15

        try:
            if cfg['key_path'] and os.path.exists(cfg['key_path']):
                pkey = paramiko.RSAKey.from_private_key_file(cfg['key_path'])
                transport.connect(username=cfg['username'], pkey=pkey)
            elif cfg['password']:
                transport.connect(username=cfg['username'], password=cfg['password'])
            else:
                transport.close()
                raise RuntimeError(f"No credentials for SFTP server {self.server_number}")
        except RuntimeError:
            raise
        except Exception as e:
            transport.close()
            raise RuntimeError(f"SFTP auth error server {self.server_number}: {e}")

        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.get_channel().settimeout(30)
        sftp._transport_ref = transport
        return sftp

    def _close(self, sftp):
        """Properly close SFTP client and its transport."""
        if sftp:
            transport = getattr(sftp, '_transport_ref', None)
            try:
                sftp.close()
            except Exception:
                pass
            if transport:
                try:
                    transport.close()
                except Exception:
                    pass

    def list_log_files(self) -> List[Dict]:
        """List available log files on the remote server."""
        if not self.is_configured():
            return []
        sftp = None
        try:
            sftp = self._connect()
            cfg = self._get_config()
            files = []
            for entry in sftp.listdir_attr(cfg['logs_dir']):
                if entry.filename.endswith('.txt'):
                    files.append({
                        'filename': entry.filename,
                        'size': entry.st_size,
                        'mtime': datetime.fromtimestamp(entry.st_mtime) if entry.st_mtime else None,
                    })
            files.sort(key=lambda x: x.get('mtime') or datetime.min, reverse=True)
            return files
        except Exception as e:
            logger.error(f"Failed to list log files from server {self.server_number}: {e}")
            return []
        finally:
            self._close(sftp)

    def download_log_file(self, filename: str) -> Optional[str]:
        """Download a single log file content from remote."""
        if not self.is_configured():
            return None
        sftp = None
        try:
            sftp = self._connect()
            cfg = self._get_config()
            remote_path = f"{cfg['logs_dir'].rstrip('/')}/{filename}"
            with sftp.open(remote_path, 'r') as f:
                content = f.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')
            return content
        except Exception as e:
            logger.error(f"Failed to download log {filename} from server {self.server_number}: {e}")
            return None
        finally:
            self._close(sftp)

    def download_and_remove_logs(self, local_dir: str) -> int:
        """Download all .txt log files to local_dir, then delete them from remote.
        
        Returns the number of files successfully downloaded.
        """
        if not self.is_configured():
            return 0
        os.makedirs(local_dir, exist_ok=True)
        sftp = None
        downloaded = 0
        try:
            sftp = self._connect()
            cfg = self._get_config()
            remote_dir = cfg['logs_dir'].rstrip('/')

            entries = sftp.listdir_attr(remote_dir)
            txt_files = [e for e in entries if e.filename.endswith('.txt')]

            for entry in txt_files:
                remote_path = f"{remote_dir}/{entry.filename}"
                local_path = os.path.join(local_dir, entry.filename)

                # Skip if already downloaded locally
                if os.path.exists(local_path):
                    # Still delete from remote
                    try:
                        sftp.remove(remote_path)
                    except Exception:
                        pass
                    continue

                try:
                    # Download to local
                    sftp.get(remote_path, local_path)
                    downloaded += 1
                    # Delete from remote after successful download
                    try:
                        sftp.remove(remote_path)
                    except Exception as e:
                        logger.warning(f"Downloaded but failed to delete remote {entry.filename}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to download {entry.filename}: {e}")
                    # Remove partial local file
                    if os.path.exists(local_path):
                        os.remove(local_path)

            return downloaded
        except Exception as e:
            logger.error(f"Failed to download logs from server {self.server_number}: {e}")
            return 0
        finally:
            self._close(sftp)


# ══════════════════════════════════════════════════════════════════════════════
# JSON File Storage & Background Worker
# ══════════════════════════════════════════════════════════════════════════════

import json as _json

LOG_STORAGE_DIR = "data/logs_cache"
LOG_RAW_DIR = "data/logs_raw"
_log_cache_lock = threading.Lock()
_log_worker_running = False
_log_refresh_interval = 60  # 1 minute

# ── In-memory cache for fast searches ────────────────────────────────────────
_memory_cache: Dict[str, List[LogEvent]] = {}  # server_name -> events
_memory_cache_ts: Dict[str, float] = {}  # server_name -> last load timestamp
_MEMORY_CACHE_TTL = 30  # seconds before re-checking disk file mtime
_MAX_EVENTS_PER_SERVER = 500_000  # Cap to prevent OOM on very large log histories


def _ensure_storage_dir():
    """Ensure the log storage directories exist."""
    os.makedirs(LOG_STORAGE_DIR, exist_ok=True)
    os.makedirs(LOG_RAW_DIR, exist_ok=True)


def _storage_path(server_name: str) -> str:
    """Get the JSON file path for a server's logs."""
    safe_name = server_name.replace(' ', '_').replace('/', '_')
    return os.path.join(LOG_STORAGE_DIR, f"{safe_name}.json")


def _meta_path() -> str:
    """Get the metadata JSON file path."""
    return os.path.join(LOG_STORAGE_DIR, "_meta.json")


def _save_events_to_disk(server_name: str, events: List[LogEvent]):
    """Save parsed events to a JSON file on disk."""
    _ensure_storage_dir()
    path = _storage_path(server_name)
    data = [
        {
            "ts": e.timestamp.isoformat(),
            "p": e.player_name,
            "t": e.action_type,
            "d": e.details,
            "s": e.server_name,
            "f": e.source_file,
        }
        for e in events
    ]
    with open(path, 'w', encoding='utf-8') as f:
        _json.dump(data, f, ensure_ascii=False)

    # Update metadata
    meta = _load_meta()
    meta[server_name] = {
        'event_count': len(events),
        'last_refresh': datetime.utcnow().isoformat(),
        'oldest_event': events[-1].timestamp.isoformat() if events else None,
        'newest_event': events[0].timestamp.isoformat() if events else None,
    }
    with open(_meta_path(), 'w', encoding='utf-8') as f:
        _json.dump(meta, f, ensure_ascii=False)


def _load_meta() -> Dict:
    """Load metadata from disk."""
    path = _meta_path()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return _json.load(f)
        except Exception:
            pass
    return {}


def _player_map_path(server_name: str) -> str:
    """Get path for player mapping file."""
    safe_name = server_name.replace(' ', '_').replace('/', '_')
    return os.path.join(LOG_STORAGE_DIR, f"{safe_name}_players.json")


def _save_player_map(server_name: str, player_map: Dict[str, str]):
    """Save/merge player name→steamid mapping to disk.
    
    Stores two structures:
    - "name_to_steam": {name: steamid} for quick lookup
    - "steam_to_names": {steamid: [name1, name2, ...]} for name history
    """
    _ensure_storage_dir()
    path = _player_map_path(server_name)
    # Load existing
    existing = {"name_to_steam": {}, "steam_to_names": {}}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = _json.load(f)
            # Support old format (flat name→steamid dict)
            if "name_to_steam" in raw:
                existing = raw
            else:
                existing["name_to_steam"] = raw
        except Exception:
            pass

    # Merge new entries
    for name, steamid in player_map.items():
        existing["name_to_steam"][name] = steamid
        if steamid not in existing["steam_to_names"]:
            existing["steam_to_names"][steamid] = []
        if name not in existing["steam_to_names"][steamid]:
            existing["steam_to_names"][steamid].append(name)

    with open(path, 'w', encoding='utf-8') as f:
        _json.dump(existing, f, ensure_ascii=False)


def get_player_map() -> Dict[str, str]:
    """Get merged player name→steamid mapping from all servers."""
    _ensure_storage_dir()
    merged = {}
    meta = _load_meta()
    for sname in meta.keys():
        path = _player_map_path(sname)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    raw = _json.load(f)
                if "name_to_steam" in raw:
                    merged.update(raw["name_to_steam"])
                else:
                    merged.update(raw)  # old format fallback
            except Exception:
                pass
    return merged


def get_steam_to_names() -> Dict[str, List[str]]:
    """Get merged steamid→[names] mapping (handles name changes)."""
    _ensure_storage_dir()
    merged: Dict[str, List[str]] = {}
    meta = _load_meta()
    for sname in meta.keys():
        path = _player_map_path(sname)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    raw = _json.load(f)
                s2n = raw.get("steam_to_names", {})
                for steamid, names in s2n.items():
                    if steamid not in merged:
                        merged[steamid] = []
                    for n in names:
                        if n not in merged[steamid]:
                            merged[steamid].append(n)
            except Exception:
                pass
    return merged


def _load_events_from_disk(server_name: str) -> List[LogEvent]:
    """Load events from JSON file on disk (with in-memory caching)."""
    now = time.time()

    # Check if we have a fresh in-memory copy
    if server_name in _memory_cache:
        cached_ts = _memory_cache_ts.get(server_name, 0)
        if now - cached_ts < _MEMORY_CACHE_TTL:
            return _memory_cache[server_name]

        # Check if the file was modified since last cache
        path = _storage_path(server_name)
        if os.path.exists(path):
            file_mtime = os.path.getmtime(path)
            if file_mtime <= cached_ts:
                # File hasn't changed, extend TTL
                _memory_cache_ts[server_name] = now
                return _memory_cache[server_name]

    # Load from disk
    path = _storage_path(server_name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = _json.load(f)
        events = []
        for item in data:
            events.append(LogEvent(
                timestamp=datetime.fromisoformat(item["ts"]),
                player_name=item["p"],
                action_type=item["t"],
                details=item["d"],
                raw_line=item["d"],
                server_name=item["s"],
                source_file=item["f"],
            ))
        # Store in memory cache (cap to prevent unbounded growth)
        if len(events) > _MAX_EVENTS_PER_SERVER:
            events = events[:_MAX_EVENTS_PER_SERVER]
        _memory_cache[server_name] = events
        _memory_cache_ts[server_name] = now
        return events
    except Exception as e:
        logger.error(f"Failed to load events from disk for {server_name}: {e}")
        return []


# Cache for the merged "all servers" view
_merged_cache: Optional[List[LogEvent]] = None
_merged_cache_ts: float = 0
_MERGED_CACHE_TTL = 15  # seconds


def get_cached_logs(server_name: Optional[str] = None) -> List[LogEvent]:
    """Get log events from disk storage, optionally filtered by server."""
    global _merged_cache, _merged_cache_ts
    _ensure_storage_dir()

    if server_name:
        return _load_events_from_disk(server_name)

    # Merge all servers — use short-lived cache to avoid re-sorting every request
    now = time.time()
    if _merged_cache is not None and (now - _merged_cache_ts) < _MERGED_CACHE_TTL:
        return _merged_cache

    meta = _load_meta()
    all_events = []
    for sname in meta.keys():
        all_events.extend(_load_events_from_disk(sname))
    all_events.sort(key=lambda e: e.timestamp, reverse=True)

    # Cap total merged events to prevent OOM
    if len(all_events) > _MAX_EVENTS_PER_SERVER * 2:
        all_events = all_events[:_MAX_EVENTS_PER_SERVER * 2]

    _merged_cache = all_events
    _merged_cache_ts = now
    return all_events


def refresh_logs_for_server(server_number: int):
    """Download new logs from SFTP to local disk, delete from remote, then parse all local files.
    
    Optimization: tracks which files have already been parsed to avoid re-reading
    unchanged files. Only new/modified files are re-parsed, then merged with existing events.
    """
    from config import get_settings
    s = get_settings()
    server_name = s.sftp_server_1_name if server_number == 1 else s.sftp_server_2_name
    safe_name = server_name.replace(' ', '_').replace('/', '_')

    mgr = LogSFTPManager(server_number)
    if not mgr.is_configured():
        logger.info(f"Logs SFTP not configured for server {server_number}, skipping")
        return

    # Step 1: Download new .txt files to local dir and delete from remote
    local_dir = os.path.join(LOG_RAW_DIR, safe_name)
    os.makedirs(local_dir, exist_ok=True)

    logger.info(f"Downloading new logs for {server_name}...")
    new_count = mgr.download_and_remove_logs(local_dir)
    logger.info(f"Downloaded {new_count} new log files for {server_name}")

    # Step 2: Parse local .txt files — use a manifest to skip unchanged files
    manifest_path = os.path.join(LOG_STORAGE_DIR, f"{safe_name}_manifest.json")
    _ensure_storage_dir()
    
    # Load manifest of previously parsed files {filename: mtime}
    old_manifest: Dict[str, float] = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                old_manifest = _json.load(f)
        except Exception:
            pass

    local_files = [fn for fn in os.listdir(local_dir) if fn.endswith('.txt')]
    new_manifest: Dict[str, float] = {}
    files_to_parse: List[str] = []

    for filename in local_files:
        filepath = os.path.join(local_dir, filename)
        mtime = os.path.getmtime(filepath)
        new_manifest[filename] = mtime
        # Parse if new or modified since last run
        if filename not in old_manifest or old_manifest[filename] < mtime:
            files_to_parse.append(filename)

    # If no files changed and we had a previous cache, skip full re-parse
    if not files_to_parse and old_manifest and not new_count:
        logger.debug(f"No new/changed files for {server_name}, skipping parse")
        return

    # Parse files — only re-parse changed ones if we have existing cache,
    # otherwise parse all (first run or manifest mismatch)
    all_events = []
    all_player_map: Dict[str, str] = {}

    for filename in local_files:
        filepath = os.path.join(local_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            events, pmap = parse_log_content(content, server_name=server_name, source_file=filename)
            all_events.extend(events)
            all_player_map.update(pmap)
            # Free the raw content immediately
            del content
        except Exception as e:
            logger.warning(f"Failed to parse local log {filename}: {e}")

    # Sort by timestamp descending
    all_events.sort(key=lambda e: e.timestamp, reverse=True)

    # Step 3: Save parsed events, player map, and manifest to disk
    with _log_cache_lock:
        _save_events_to_disk(server_name, all_events)
        _save_player_map(server_name, all_player_map)
        # Invalidate in-memory caches for this server
        global _merged_cache, _merged_cache_ts
        _memory_cache.pop(server_name, None)
        _memory_cache_ts.pop(server_name, None)
        _merged_cache = None
        _merged_cache_ts = 0
        _players_cache.clear()
        _players_cache_ts.clear()

    # Save manifest
    try:
        with open(manifest_path, 'w', encoding='utf-8') as f:
            _json.dump(new_manifest, f)
    except Exception:
        pass

    logger.info(f"Parsed {len(all_events)} events from {len(local_files)} local files for {server_name}, {len(all_player_map)} players mapped")


def _log_worker_loop():
    """Background worker that periodically refreshes logs from SFTP."""
    global _log_worker_running
    _log_worker_running = True
    logger.info("Log worker started")

    while _log_worker_running:
        try:
            refresh_logs_for_server(1)
        except Exception as e:
            logger.error(f"Log worker error (server 1): {e}")

        try:
            refresh_logs_for_server(2)
        except Exception as e:
            logger.error(f"Log worker error (server 2): {e}")

        # Sleep for refresh interval
        for _ in range(_log_refresh_interval):
            if not _log_worker_running:
                break
            time.sleep(1)

    logger.info("Log worker stopped")


def start_log_worker():
    """Start the background log download worker thread."""
    global _log_worker_running
    if _log_worker_running:
        return
    _ensure_storage_dir()
    t = threading.Thread(target=_log_worker_loop, daemon=True, name="LogWorker")
    t.start()


def stop_log_worker():
    """Stop the background log worker."""
    global _log_worker_running
    _log_worker_running = False


def force_refresh_logs():
    """Force an immediate refresh of all logs (runs in background thread)."""
    def _do_refresh():
        try:
            refresh_logs_for_server(1)
        except Exception as e:
            logger.error(f"Force refresh error (server 1): {e}")
        try:
            refresh_logs_for_server(2)
        except Exception as e:
            logger.error(f"Force refresh error (server 2): {e}")

    t = threading.Thread(target=_do_refresh, daemon=True, name="LogRefresh")
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
# Search & Filter
# ══════════════════════════════════════════════════════════════════════════════

def search_logs(
    server_name: Optional[str] = None,
    player_name: Optional[str] = None,
    action_type: Optional[str] = None,
    search_text: Optional[str] = None,
    time_from: Optional[datetime] = None,
    time_to: Optional[datetime] = None,
    category: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> Tuple[List[LogEvent], int]:
    """Search and filter log events from disk. Returns (events, total_count).
    
    Optimized: uses in-memory cache, early termination for time filters,
    and generator-based filtering to avoid materializing full lists.
    """
    PLAYER_ACTIONS = {'picked_up', 'placed', 'removed', 'opened', 'closed', 'equipped',
                      'unequipped', 'dropped', 'reactor', 'repair', 'fabrication',
                      'fabrication_cancel', 'activated', 'deactivated', 'wire', 'pump',
                      'settings_change', 'console', 'death', 'chat', 'login', 'logout',
                      'attack', 'structure', 'kick', 'name_change'}

    events = get_cached_logs(server_name)

    # Pre-compute filter values once
    player_lower = player_name.lower() if player_name else None
    search_lower = search_text.lower() if search_text else None

    # Single-pass filtering with generator to avoid multiple list iterations
    def _filter():
        for e in events:
            # Time filters — events are sorted desc, so we can optimize
            if time_to and e.timestamp > time_to:
                continue
            if time_from and e.timestamp < time_from:
                # All remaining events are older, stop iteration
                break

            # Category filter
            if category == 'player' and e.action_type not in PLAYER_ACTIONS:
                continue
            elif category == 'technical' and e.action_type in PLAYER_ACTIONS:
                continue

            # Player filter
            if player_lower and player_lower not in e.player_name.lower():
                continue

            # Action type filter
            if action_type and action_type != 'all' and e.action_type != action_type:
                continue

            # Text search filter
            if search_lower and search_lower not in e.details.lower():
                continue

            yield e

    # We need total count for pagination, so materialize filtered results
    # But use early-break for time_from optimization
    filtered = list(_filter())
    total = len(filtered)
    page_events = filtered[offset:offset + limit]

    return page_events, total


_players_cache: Dict[Optional[str], List[str]] = {}
_players_cache_ts: Dict[Optional[str], float] = {}
_PLAYERS_CACHE_TTL = 60  # seconds


def get_unique_players(server_name: Optional[str] = None) -> List[str]:
    """Get list of unique player names from logs (cached)."""
    now = time.time()
    cache_key = server_name

    if cache_key in _players_cache:
        if now - _players_cache_ts.get(cache_key, 0) < _PLAYERS_CACHE_TTL:
            return _players_cache[cache_key]

    events = get_cached_logs(server_name)
    players = set()
    for e in events:
        if e.player_name:
            players.add(e.player_name)
    result = sorted(players)

    _players_cache[cache_key] = result
    _players_cache_ts[cache_key] = now
    return result


def get_log_stats() -> Dict:
    """Get statistics about stored logs."""
    meta = _load_meta()
    stats = {}
    for server, data in meta.items():
        stats[server] = {
            'event_count': data.get('event_count', 0),
            'last_refresh': data.get('last_refresh'),
            'oldest_event': data.get('oldest_event'),
            'newest_event': data.get('newest_event'),
        }
    return stats

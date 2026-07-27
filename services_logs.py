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
    action_type: str  # picked_up, placed, opened, closed, equipped, unequipped, reactor, chat, connection, fabrication, repair, console, death, wire, pump, radio, dropped, used, activated, deactivated, login, logout, other
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
    (re.compile(r'^(.+?) used (.+?) on (.+)$'), 'used'),
    (re.compile(r'^(.+?) used (.+)$'), 'used'),
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

        # System/technical lines must NOT be treated as player chat.
        # Anything starting with a [Tag] (e.g. [DemoUploader], [QueueGuard],
        # [DemoRecorder]) or a numbered spawn-manifest line ("2. Инопланетное
        # орудие (ID: ...)") is technical noise — classify as 'other' so the
        # chat-catching regex below doesn't mis-file it into player actions.
        _is_system = (
            action_line.startswith('[')
            or re.match(r'^\d+\.\s', action_line) is not None
        )

        if _is_system:
            events.append(LogEvent(
                timestamp=current_timestamp,
                player_name='',
                action_type='other',
                details=action_line,
                raw_line=action_line,
                server_name=server_name,
                source_file=source_file,
            ))
            continue

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
# SQLite Storage & Background Worker
# ══════════════════════════════════════════════════════════════════════════════
#
# Logs are stored in a dedicated SQLite database with indexes, NOT in giant JSON
# files loaded into RAM. Search/filter runs as indexed SQL queries with
# LIMIT/OFFSET — constant memory regardless of total log volume.
#
import json as _json
import sqlite3

LOG_STORAGE_DIR = "data/logs_cache"
LOG_RAW_DIR = "data/logs_raw"
LOG_DB_PATH = os.path.join(LOG_STORAGE_DIR, "logs.db")
_log_cache_lock = threading.Lock()
_log_worker_running = False
_log_refresh_interval = 60  # 1 minute

# Upper bound for search result counts (see search_logs). Text filters use a
# leading-wildcard LIKE that no index can serve, so an exact COUNT(*) means a
# full-table scan on every search; counting inside a LIMIT-ed subquery keeps
# that bounded no matter how large the log history grows. Anything past this
# is reported as "N+" — nobody pages to result 5000 of a log search anyway.
COUNT_CAP = 5000

# Thread-local SQLite connections (sqlite3 objects are not thread-safe to share)
_tls = threading.local()


def _ensure_storage_dir():
    os.makedirs(LOG_STORAGE_DIR, exist_ok=True)
    os.makedirs(LOG_RAW_DIR, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection with sane pragmas."""
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        return conn
    _ensure_storage_dir()
    conn = sqlite3.connect(LOG_DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")       # concurrent reads while writing
    conn.execute("PRAGMA synchronous=NORMAL")     # fast, still crash-safe with WAL
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-8000")       # ~8MB page cache (negative = KB)
    conn.execute("PRAGMA wal_autocheckpoint=1000")  # auto-checkpoint every ~1000 pages (~4MB)
    conn.execute("PRAGMA busy_timeout=30000")     # wait up to 30s on lock instead of erroring
    _init_schema(conn)
    _tls.conn = conn
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS log_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          INTEGER NOT NULL,          -- unix ms
            server      TEXT NOT NULL,
            player      TEXT,
            action      TEXT NOT NULL,
            details     TEXT,
            source_file TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_log_ts      ON log_events(ts);
        CREATE INDEX IF NOT EXISTS ix_log_server  ON log_events(server, ts);
        CREATE INDEX IF NOT EXISTS ix_log_player  ON log_events(player);
        CREATE INDEX IF NOT EXISTS ix_log_action  ON log_events(action);
        -- Every listing is ORDER BY ts DESC, and the action/category filter
        -- is the most common non-text filter — a composite lets SQLite walk
        -- straight down this index for "тип события + свежие сверху" instead
        -- of scanning and sorting. (Text filters stay unindexable by design:
        -- a leading-wildcard LIKE can't use any B-tree, which is exactly why
        -- the COUNT above is capped.)
        CREATE INDEX IF NOT EXISTS ix_log_action_ts ON log_events(action, ts);

        CREATE TABLE IF NOT EXISTS log_files (
            server   TEXT NOT NULL,
            filename TEXT NOT NULL,
            mtime    REAL NOT NULL,
            PRIMARY KEY (server, filename)
        );

        CREATE TABLE IF NOT EXISTS player_map (
            name    TEXT PRIMARY KEY,
            steamid TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS player_names (
            steamid TEXT NOT NULL,
            name    TEXT NOT NULL,
            PRIMARY KEY (steamid, name)
        );

        -- Прогресс/состояние полнотекстового индекса (см. _init_fts).
        CREATE TABLE IF NOT EXISTS fts_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS log_meta (
            server        TEXT PRIMARY KEY,
            event_count   INTEGER,
            last_refresh  TEXT,
            oldest_event  TEXT,
            newest_event  TEXT
        );
    """)
    conn.commit()
    # Полнотекстовый индекс + триггеры синхронизации. Ставится здесь, чтобы
    # все новые события индексировались сразу; уже накопленная история
    # доиндексируется в фоне (см. _init_fts / fts_backfill_step).
    _init_fts(conn)


def _ts_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0)


# Action categories (module-level, shared)
PLAYER_ACTIONS = {'picked_up', 'placed', 'removed', 'opened', 'closed', 'equipped',
                  'unequipped', 'dropped', 'used', 'reactor', 'repair', 'fabrication',
                  'fabrication_cancel', 'activated', 'deactivated', 'wire', 'pump',
                  'settings_change', 'console', 'death', 'chat', 'login', 'logout',
                  'attack', 'structure', 'kick', 'name_change'}


def get_player_map() -> Dict[str, str]:
    """Get merged player name→steamid mapping from SQLite."""
    try:
        conn = _get_conn()
        rows = conn.execute("SELECT name, steamid FROM player_map").fetchall()
        return {name: sid for name, sid in rows}
    except Exception as e:
        logger.error(f"get_player_map failed: {e}")
        return {}


def get_steam_to_names() -> Dict[str, List[str]]:
    """Get merged steamid→[names] mapping (handles name changes)."""
    try:
        conn = _get_conn()
        rows = conn.execute("SELECT steamid, name FROM player_names").fetchall()
        merged: Dict[str, List[str]] = {}
        for sid, name in rows:
            merged.setdefault(sid, []).append(name)
        return merged
    except Exception as e:
        logger.error(f"get_steam_to_names failed: {e}")
        return {}



def refresh_logs_for_server(server_number: int):
    """Download new logs from SFTP, then incrementally parse & insert into SQLite.

    Only NEW or MODIFIED files are parsed. Events are inserted via executemany
    in a single transaction — constant memory, no full-history reload.
    """
    from config import get_settings
    s = get_settings()
    server_name = s.sftp_server_1_name if server_number == 1 else s.sftp_server_2_name

    mgr = LogSFTPManager(server_number)
    if not mgr.is_configured():
        logger.info(f"Logs SFTP not configured for server {server_number}, skipping")
        return

    safe_name = server_name.replace(' ', '_').replace('/', '_')
    local_dir = os.path.join(LOG_RAW_DIR, safe_name)
    os.makedirs(local_dir, exist_ok=True)

    new_count = mgr.download_and_remove_logs(local_dir)
    if new_count:
        logger.info(f"Downloaded {new_count} new log files for {server_name}")

    conn = _get_conn()

    # Determine which files changed since last parse (tracked in log_files table)
    known = {row[0]: row[1] for row in conn.execute(
        "SELECT filename, mtime FROM log_files WHERE server=?", (server_name,)
    ).fetchall()}

    local_files = [fn for fn in os.listdir(local_dir) if fn.endswith('.txt')]
    files_to_parse = []
    for filename in local_files:
        filepath = os.path.join(local_dir, filename)
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            continue
        if filename not in known or known[filename] < mtime:
            files_to_parse.append((filename, mtime))

    if not files_to_parse:
        return  # nothing new to parse

    total_events = 0
    with _log_cache_lock:
        for filename, mtime in files_to_parse:
            filepath = os.path.join(local_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                events, pmap = parse_log_content(content, server_name=server_name, source_file=filename)
                del content  # free raw text immediately

                # Remove any previous rows for this file (re-parse case), then insert
                conn.execute("DELETE FROM log_events WHERE server=? AND source_file=?",
                             (server_name, filename))
                if events:
                    conn.executemany(
                        "INSERT INTO log_events (ts, server, player, action, details, source_file) "
                        "VALUES (?,?,?,?,?,?)",
                        [(_ts_to_ms(e.timestamp), server_name, e.player_name,
                          e.action_type, e.details, filename) for e in events]
                    )
                    total_events += len(events)

                # Merge player map
                for name, sid in pmap.items():
                    conn.execute("INSERT OR REPLACE INTO player_map (name, steamid) VALUES (?,?)", (name, sid))
                    conn.execute("INSERT OR IGNORE INTO player_names (steamid, name) VALUES (?,?)", (sid, name))

                conn.execute(
                    "INSERT OR REPLACE INTO log_files (server, filename, mtime) VALUES (?,?,?)",
                    (server_name, filename, mtime)
                )
                # free events list reference
                events = None
                # Commit per-file so the transaction & WAL stay small during a
                # large first-run import of historical logs.
                conn.commit()
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    pass
            except Exception as e:
                conn.rollback()
                logger.warning(f"Failed to parse local log {filename}: {e}")

        # Update meta for this server
        row = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM log_events WHERE server=?",
            (server_name,)
        ).fetchone()
        cnt = row[0] or 0
        oldest = _ms_to_dt(row[1]).isoformat() if row[1] else None
        newest = _ms_to_dt(row[2]).isoformat() if row[2] else None
        conn.execute(
            "INSERT OR REPLACE INTO log_meta (server, event_count, last_refresh, oldest_event, newest_event) "
            "VALUES (?,?,?,?,?)",
            (server_name, cnt, datetime.utcnow().isoformat(), oldest, newest)
        )
        conn.commit()
        # Truncate WAL so it doesn't grow unbounded across refresh cycles
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        _players_cache.clear()
        _players_cache_ts.clear()

    logger.info(f"Indexed {total_events} new events from {len(files_to_parse)} file(s) for {server_name}")


def _log_worker_loop():
    """Background worker that periodically refreshes logs from SFTP."""
    global _log_worker_running
    _log_worker_running = True
    logger.info("Log worker started")

    _cleanup_counter = 0
    _first_pass = True
    while _log_worker_running:
        # Ретенция идёт ПЕРЕД индексацией (и на первом же проходе, а не через
        # час): при коротком сроке хранения незачем тратить время на
        # индексацию строк, которые тут же будут удалены — после очистки
        # объём работы для бэкфилла кратно меньше.
        if _first_pass or _cleanup_counter >= 72:
            _cleanup_counter = 0
            try:
                cleanup_old_logs()
            except Exception as e:
                logger.error(f"Log retention cleanup error: {e}")

        # Доиндексация накопленной истории в FTS — порциями, по одной за
        # цикл, пока не будет пройдена вся таблица. Так 5+ млн строк
        # индексируются постепенно, не блокируя ни старт приложения, ни
        # обычную запись логов. Пока не достроено — поиск работает через
        # LIKE (медленно, но корректно), см. search_logs.
        try:
            while fts_backfill_step():
                if not _log_worker_running:
                    break
                time.sleep(1)  # уступить БД записи логов между порциями
        except Exception as e:
            logger.error(f"FTS backfill error: {e}")

        _first_pass = False

        try:
            refresh_logs_for_server(1)
        except Exception as e:
            logger.error(f"Log worker error (server 1): {e}")

        try:
            refresh_logs_for_server(2)
        except Exception as e:
            logger.error(f"Log worker error (server 2): {e}")

        # Отсчёт до следующей ретенции: цикл длится _log_refresh_interval
        # секунд (по умолчанию 60), т.е. 72 цикла ≈ раз в ~1.2 часа.
        # Сама очистка выполняется в начале цикла (см. выше).
        _cleanup_counter += 1

        # Sleep for refresh interval
        for _ in range(_log_refresh_interval):
            if not _log_worker_running:
                break
            time.sleep(1)

    logger.info("Log worker stopped")


LOG_RETENTION_DAYS = 15  # события старше этого срока удаляются
_CLEANUP_BATCH = 50000   # строк за одну транзакцию удаления


def cleanup_old_logs(retention_days: int = LOG_RETENTION_DAYS, vacuum: bool = True):
    """Удаляет события старше срока хранения и возвращает место на диске.

    Удаление идёт ПОРЦИЯМИ, а не одним DELETE: при переходе на более короткий
    срок хранения (или на большой накопленной истории) единая транзакция на
    миллионы строк надолго забирает блокировку записи, а с появлением FTS
    каждая удаляемая строка ещё и дёргает триггер синхронизации индекса.
    Порции с коммитом и освобождением _log_cache_lock между ними позволяют
    фоновой закачке логов работать параллельно.

    VACUUM выполняется один раз в конце и только если реально что-то удалено:
    он переписывает файл БД целиком (нужен запас места примерно в размер базы)
    и на время блокирует её, поэтому гонять его на каждую порцию нельзя.
    """
    cutoff_ms = _ts_to_ms(datetime.utcnow() - timedelta(days=retention_days))
    deleted = 0

    while True:
        with _log_cache_lock:
            conn = _get_conn()
            cur = conn.execute(
                "DELETE FROM log_events WHERE id IN ("
                "  SELECT id FROM log_events WHERE ts < ? LIMIT ?"
                ")",
                (cutoff_ms, _CLEANUP_BATCH),
            )
            n = cur.rowcount or 0
            conn.commit()
        deleted += n
        if n < _CLEANUP_BATCH:
            break
        # Уступить БД фоновой закачке/поиску между порциями.
        time.sleep(0.5)

    if deleted <= 0:
        return

    with _log_cache_lock:
        conn = _get_conn()
        if vacuum:
            try:
                conn.execute("VACUUM")
            except Exception as e:
                logger.warning(f"VACUUM после очистки логов не выполнен: {e}")
        for srv in [r[0] for r in conn.execute("SELECT DISTINCT server FROM log_events").fetchall()]:
            row = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM log_events WHERE server=?", (srv,)).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO log_meta (server, event_count, last_refresh, oldest_event, newest_event) VALUES (?,?,?,?,?)",
                (srv, row[0] or 0, datetime.utcnow().isoformat(),
                 _ms_to_dt(row[1]).isoformat() if row[1] else None,
                 _ms_to_dt(row[2]).isoformat() if row[2] else None)
            )
        conn.commit()
        _players_cache.clear()
        _players_cache_ts.clear()
    logger.info(f"Ретенция логов: удалено {deleted} событий старше {retention_days} дн.")


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
# Search & Filter (SQL-backed — constant memory)
# ══════════════════════════════════════════════════════════════════════════════

_players_cache: Dict[Optional[str], List[str]] = {}
_players_cache_ts: Dict[Optional[str], float] = {}
_PLAYERS_CACHE_TTL = 60  # seconds


# ══════════════════════════════════════════════════════════════════════════
# ПОЛНОТЕКСТОВЫЙ ПОИСК (FTS5)
#
# Текстовые фильтры раньше делали LIKE '%...%' — ведущий wildcard, который
# не может использовать ни один B-tree-индекс, то есть полный скан таблицы
# на каждый запрос. При 5+ млн событий и БД >2 ГБ это укладывало бэкенд.
#
# log_fts — это FTS5-таблица с внешним содержимым (content='log_events'):
# сам текст в ней НЕ дублируется, хранится только инвертированный индекс,
# что критично при уже двухгигабайтной базе. Синхронизацию с log_events
# держат триггеры ниже.
#
# ВАЖНО о семантике: FTS ищет по НАЧАЛУ слова (префиксный поиск), а не по
# произвольной подстроке. "влад" найдёт "Владимир", но "адим" — уже нет,
# в отличие от прежнего LIKE. Это осознанный размен: подстрочный поиск в
# принципе неиндексируем и именно он был причиной нагрузки.
# ══════════════════════════════════════════════════════════════════════════

_FTS_BACKFILL_BATCH = 20000   # строк за один шаг фонового бэкфилла
_fts_available_cache: Optional[bool] = None


def _fts_available(conn: sqlite3.Connection) -> bool:
    """Собран ли SQLite с поддержкой FTS5 (кэшируется на процесс)."""
    global _fts_available_cache
    if _fts_available_cache is None:
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
            conn.execute("DROP TABLE IF EXISTS _fts_probe")
            _fts_available_cache = True
        except Exception as e:
            logger.warning(f"FTS5 недоступен, поиск останется на LIKE: {e}")
            _fts_available_cache = False
    return _fts_available_cache


def _fts_meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM fts_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _fts_meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO fts_meta (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


def _init_fts(conn: sqlite3.Connection) -> bool:
    """Создаёт FTS5-таблицу и триггеры синхронизации. Идемпотентно.

    Триггеры ставятся сразу, поэтому все НОВЫЕ события индексируются с этого
    момента, а уже накопленные 5+ млн строк доиндексируются в фоне
    (fts_backfill_step) — старт приложения при этом не блокируется.
    """
    if not _fts_available(conn):
        return False
    try:
        conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS log_fts USING fts5(
                player, details,
                content='log_events', content_rowid='id',
                tokenize="unicode61 remove_diacritics 2",
                prefix='2 3'
            );

            CREATE TRIGGER IF NOT EXISTS log_fts_ai AFTER INSERT ON log_events BEGIN
                INSERT INTO log_fts(rowid, player, details)
                VALUES (new.id, new.player, new.details);
            END;
            CREATE TRIGGER IF NOT EXISTS log_fts_ad AFTER DELETE ON log_events BEGIN
                INSERT INTO log_fts(log_fts, rowid, player, details)
                VALUES ('delete', old.id, old.player, old.details);
            END;
            CREATE TRIGGER IF NOT EXISTS log_fts_au AFTER UPDATE ON log_events BEGIN
                INSERT INTO log_fts(log_fts, rowid, player, details)
                VALUES ('delete', old.id, old.player, old.details);
                INSERT INTO log_fts(rowid, player, details)
                VALUES (new.id, new.player, new.details);
            END;
        """)
        if _fts_meta_get(conn, "backfill_cursor") is None:
            # Курсор идёт СВЕРХУ ВНИЗ (от новых id к старым): свежие логи
            # ищут чаще всего, поэтому они индексируются первыми, и старые
            # файлы — те, что реже всего перезаписываются при refresh —
            # остаются неиндексированными дольше всех.
            max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM log_events").fetchone()[0]
            _fts_meta_set(conn, "backfill_cursor", max_id + 1)
            _fts_meta_set(conn, "backfill_done", "1" if max_id == 0 else "0")
        return True
    except Exception as e:
        logger.error(f"Не удалось инициализировать FTS5: {e}")
        return False


def fts_is_ready(conn: Optional[sqlite3.Connection] = None) -> bool:
    """True, когда индекс покрывает всю историю и им можно искать."""
    try:
        conn = conn or _get_conn()
        return _fts_available(conn) and _fts_meta_get(conn, "backfill_done") == "1"
    except Exception:
        return False


def fts_backfill_step(batch: int = _FTS_BACKFILL_BATCH) -> bool:
    """Индексирует одну порцию ранее накопленных строк.

    Возвращает True, если работа ещё осталась. Порционно и с сохранением
    курсора — чтобы не держать блокировку записи на 5 млн строк разом и
    чтобы прогресс не терялся при перезапуске.
    """
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error(f"fts_backfill_step: БД недоступна: {e}")
        return False
    if not _fts_available(conn) or _fts_meta_get(conn, "backfill_done") == "1":
        return False

    try:
        cursor = int(_fts_meta_get(conn, "backfill_cursor") or 0)
        if cursor <= 1:
            _fts_meta_set(conn, "backfill_done", "1")
            logger.info("FTS-бэкфилл логов завершён")
            return False

        rows = conn.execute(
            "SELECT id, player, details FROM log_events WHERE id < ? ORDER BY id DESC LIMIT ?",
            (cursor, batch),
        ).fetchall()
        if not rows:
            _fts_meta_set(conn, "backfill_done", "1")
            logger.info("FTS-бэкфилл логов завершён")
            return False

        conn.executemany(
            "INSERT INTO log_fts(rowid, player, details) VALUES (?,?,?)", rows
        )
        conn.commit()

        new_cursor = rows[-1][0]  # наименьший id в порции (порядок DESC)
        _fts_meta_set(conn, "backfill_cursor", new_cursor)
        if len(rows) < batch or new_cursor <= 1:
            _fts_meta_set(conn, "backfill_done", "1")
            logger.info("FTS-бэкфилл логов завершён")
            return False

        logger.info(f"FTS-бэкфилл: проиндексировано до id={new_cursor}, осталось ниже него")
        return True
    except Exception as e:
        logger.error(f"Ошибка FTS-бэкфилла: {e}")
        return False


def get_fts_status() -> dict:
    """Состояние индекса для админки/диагностики."""
    try:
        conn = _get_conn()
        if not _fts_available(conn):
            return {"available": False, "ready": False, "progress_pct": 0}
        done = _fts_meta_get(conn, "backfill_done") == "1"
        cursor = int(_fts_meta_get(conn, "backfill_cursor") or 0)
        max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM log_events").fetchone()[0]
        # cursor идёт вниз к 1, поэтому доля готового = сколько уже пройдено сверху
        pct = 100 if done else (int((max_id - cursor) / max_id * 100) if max_id else 0)
        return {"available": True, "ready": done, "progress_pct": max(0, min(100, pct))}
    except Exception:
        return {"available": False, "ready": False, "progress_pct": 0}


def _fts_query(text: str) -> str:
    """Пользовательский ввод → безопасный префиксный FTS5-запрос.

    Кавычки удваиваются (экранирование внутри фразы), спецсимволы синтаксиса
    FTS5 при этом теряют своё значение — пользователь ищет текст, а не пишет
    поисковые выражения. Последний токен становится префиксным, поэтому
    "влад" находит "Владимир".
    """
    cleaned = text.replace('"', '""').strip()
    return f'"{cleaned}"*' if cleaned else '""'


def _build_where(server_name, player_name, action_type, search_text,
                 time_from, time_to, category, names_list=None, use_fts=False):
    """Собирает FROM/WHERE + параметры для запросов к логам.

    use_fts=True — текстовые фильтры уходят в FTS5 (MATCH по индексу),
    иначе используется прежний LIKE '%...%' (полный скан) как запасной путь,
    пока фоновый бэкфилл не достроил индекс или если FTS5 недоступен.
    Возвращает (from_sql, where, params).
    """
    clauses, params = [], []
    if server_name:
        clauses.append("server = ?"); params.append(server_name)
    if time_from:
        clauses.append("ts >= ?"); params.append(_ts_to_ms(time_from))
    if time_to:
        clauses.append("ts <= ?"); params.append(_ts_to_ms(time_to))
    if action_type and action_type != 'all':
        clauses.append("action = ?"); params.append(action_type)
    if category == 'player':
        ph = ",".join("?" * len(PLAYER_ACTIONS))
        clauses.append(f"action IN ({ph})"); params.extend(PLAYER_ACTIONS)
    elif category == 'technical':
        ph = ",".join("?" * len(PLAYER_ACTIONS))
        clauses.append(f"action NOT IN ({ph})"); params.extend(PLAYER_ACTIONS)

    from_sql = "log_events"
    if use_fts:
        # Все текстовые условия объединяются в ОДНО выражение MATCH — FTS5
        # допускает лишь один MATCH на таблицу, но его синтаксис сам
        # поддерживает AND/OR и фильтр по колонке.
        fts_terms = []
        if names_list:
            ors = " OR ".join(f"player:{_fts_query(n)}" for n in names_list)
            fts_terms.append(f"({ors})")
        elif player_name:
            fts_terms.append(f"player:{_fts_query(player_name)}")
        if search_text:
            fts_terms.append(f"details:{_fts_query(search_text)}")
        if fts_terms:
            from_sql = "log_events JOIN log_fts ON log_fts.rowid = log_events.id"
            clauses.append("log_fts MATCH ?")
            params.append(" AND ".join(fts_terms))
    else:
        if names_list:
            ors = " OR ".join("player LIKE ?" for _ in names_list)
            clauses.append(f"({ors})")
            params.extend([f"%{n}%" for n in names_list])
        elif player_name:
            clauses.append("player LIKE ?"); params.append(f"%{player_name}%")
        if search_text:
            clauses.append("details LIKE ?"); params.append(f"%{search_text}%")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return from_sql, where, params


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
    names_list: Optional[List[str]] = None,
) -> Tuple[List[LogEvent], int, bool]:
    """Search log events via indexed SQL.

    Returns (events, total_count, total_is_capped). Constant memory: only
    `limit` rows are materialized. `total_count` is capped at COUNT_CAP —
    when total_is_capped is True there are more matches than reported and
    the UI should render it as "COUNT_CAP+" (see the bounded-COUNT comment
    below for why an exact total isn't worth its cost here).
    """
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error(f"search_logs: DB unavailable: {e}")
        return [], 0

    # Текстовые фильтры идут через FTS5, как только индекс достроен; пока
    # фоновый бэкфилл не закончил (или если FTS5 нет в сборке SQLite) —
    # прежний LIKE-путь, чтобы поиск оставался корректным, пусть и медленным.
    has_text_filter = bool(player_name or search_text or names_list)
    use_fts = has_text_filter and fts_is_ready(conn)

    from_sql, where, params = _build_where(server_name, player_name, action_type,
                                           search_text, time_from, time_to, category,
                                           names_list, use_fts=use_fts)

    # Ограниченный COUNT. Через FTS он дёшев, но на LIKE-пути (пока индекс
    # строится) COUNT(*) обязан просканировать всю таблицу — счёт внутри
    # LIMIT-подзапроса ограничивает работу COUNT_CAP строками независимо от
    # размера истории. Точный итог того не стоит: вызывающий получает флаг
    # и показывает "5000+".
    capped = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {from_sql}{where} LIMIT {COUNT_CAP + 1})",
        params,
    ).fetchone()[0]
    total = min(capped, COUNT_CAP)
    total_is_capped = capped > COUNT_CAP

    rows = conn.execute(
        f"SELECT log_events.ts, log_events.player, log_events.action, log_events.details, "
        f"log_events.server, log_events.source_file FROM {from_sql}{where} "
        f"ORDER BY log_events.ts DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()

    events = [
        LogEvent(
            timestamp=_ms_to_dt(r[0]),
            player_name=r[1] or "",
            action_type=r[2],
            details=r[3] or "",
            raw_line=r[3] or "",
            server_name=r[4] or "",
            source_file=r[5] or "",
        )
        for r in rows
    ]
    return events, total, total_is_capped


def get_cached_logs(server_name: Optional[str] = None) -> List[LogEvent]:
    """DEPRECATED compatibility shim. Returns a bounded recent slice via SQL.

    Old callers expected the full list; we cap to 5000 most-recent events to
    avoid loading the entire history into RAM. Prefer search_logs() instead.
    """
    events, _total, _capped = search_logs(server_name=server_name, limit=5000, offset=0)
    return events


def get_unique_players(server_name: Optional[str] = None) -> List[str]:
    """Get list of unique player names from logs (SQL DISTINCT, cached)."""
    now = time.time()
    cache_key = server_name
    if cache_key in _players_cache and now - _players_cache_ts.get(cache_key, 0) < _PLAYERS_CACHE_TTL:
        return _players_cache[cache_key]
    try:
        conn = _get_conn()
        if server_name:
            rows = conn.execute(
                "SELECT DISTINCT player FROM log_events WHERE server=? AND player IS NOT NULL AND player<>'' ORDER BY player",
                (server_name,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT player FROM log_events WHERE player IS NOT NULL AND player<>'' ORDER BY player"
            ).fetchall()
        result = [r[0] for r in rows]
    except Exception as e:
        logger.error(f"get_unique_players failed: {e}")
        result = []
    _players_cache[cache_key] = result
    _players_cache_ts[cache_key] = now
    return result


def get_log_stats() -> Dict:
    """Get statistics about stored logs (from log_meta table)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT server, event_count, last_refresh, oldest_event, newest_event FROM log_meta"
        ).fetchall()
        return {
            r[0]: {
                'event_count': r[1] or 0,
                'last_refresh': r[2],
                'oldest_event': r[3],
                'newest_event': r[4],
            }
            for r in rows
        }
    except Exception as e:
        logger.error(f"get_log_stats failed: {e}")
        return {}

"""
Server Logs Service: SFTP download, parsing, and search for Barotrauma server logs.
Logs are downloaded in a background worker thread and parsed into structured events.
"""

import base64
import binascii
import os
import re
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple, NamedTuple
from dataclasses import dataclass, field
from contextlib import contextmanager

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
    row_id: int = 0


# RoundManager login/logout patterns for extracting name→steamid
LOGIN_RE = re.compile(r'^\[RoundManager_ND\] Вход: (.+?) \| SteamID: (\d+) \| IP: (.+)$')
LOGOUT_RE = re.compile(r'^\[RoundManager_ND\] Выход: (.+?) \| SteamID: (\d+) \|')

# [ShardLink] ban/kick propagation notices — matched BEFORE the generic
# "any [Tag]-prefixed line is technical noise" rule (see _is_system in
# parse_log_content), which would otherwise swallow these into 'other' with
# no player attached. Found by inspecting real production logs — these were
# some of the most common events in the whole file and were entirely
# unsearchable/uncategorized before this.
SHARD_BAN_RE = re.compile(r'^\[ShardLink\] (.+?) \(STEAM_\S+\) забанен на этом шарде')
SHARD_KICK_RE = re.compile(r'^\[ShardLink\] (.+?) кикнут на этом шарде')

# Теги, у которых в ACTION_PATTERNS есть собственный разбор с игроком —
# они не должны попадать под общее правило «строка с [Тегом] = технический
# шум» (см. _is_system в parse_log_content).
TAGGED_PLAYER_RE = re.compile(r'^\[CustomRadioThirdFork\]')

# Action type classification patterns
ATTACKED_BY_RE = re.compile(
    r'^(.+?) attacked by (.+?)(?:\.\s+(?=[^:.\n]+:\s*-?\d)(.+))?\.?$'
)

ACTION_PATTERNS = [
    (re.compile(r'^(.+?) picked up (.+)$'), 'picked_up'),
    (re.compile(r'^(.+?) placed (.+) in the inventory of (.+)$'), 'placed'),
    (re.compile(r'^(.+?) removed (.+) from the inventory of (.+)$'), 'removed'),
    (re.compile(r'^(.+?) opened (.+)$'), 'opened'),
    (re.compile(r'^(.+?) closed (.+)$'), 'closed'),
    (re.compile(r'^(.+?) equipped (.+)$'), 'equipped'),
    (re.compile(r'^(.+?) unequipped (.+)$'), 'unequipped'),
    (re.compile(r'^(.+?) dropped (.+)$'), 'dropped'),
    (re.compile(r'^(.+?) threw (.+)$'), 'threw'),
    (re.compile(r'^(.+?) used (.+?) on (.+)$'), 'used'),
    (re.compile(r'^(.+?) used (.+)$'), 'used'),
    (re.compile(r'^(.+?) adjusted reactor settings: (.+)$'), 'reactor'),
    (re.compile(r'^(.+?) started repairing (.+)$'), 'repair'),
    (re.compile(r'^(.+?) failed to repair (.+)$'), 'repair_failed'),
    (re.compile(r'^(.+?) started fabricating (.+)$'), 'fabrication'),
    (re.compile(r'^(.+?) cancelled the fabrication of (.+)$'), 'fabrication_cancel'),
    (re.compile(r'^(.+?) activated (.+)$'), 'activated'),
    (re.compile(r'^(.+?) deactivated (.+)$'), 'deactivated'),
    (re.compile(r'^(.+?) attached (.+) to a wall$'), 'attached'),
    (re.compile(r'^(.+?) connected a wire (.+)$'), 'wire'),
    (re.compile(r'^(.+?) disconnected a wire (.+)$'), 'wire'),
    (re.compile(r'^(.+?) set the pumping speed of (.+)$'), 'pump'),
    (re.compile(r'^(.+?) turned (on|off) (.+)$'), 'pump'),
    # "Set the recharge speed of" (supercapacitors) and "changed supply ratio
    # of power group" (reactor power management) are both reactor/power
    # settings tweaks distinct from "set the value" — same action_type
    # bucket, player_name falls out of the default group(1) handling below.
    (re.compile(r'^(.+?) set the recharge speed of (.+)$'), 'settings_change'),
    (re.compile(r'^(.+?) changed supply ratio of power group (.+)$'), 'settings_change'),
    (re.compile(r'^(.+?) set the value (.+)$'), 'settings_change'),
    (re.compile(r"^(.+?) has gained the talent '(.+?)'$"), 'talent_gained'),
    (re.compile(r'^(.+?) grabbed (.+?)\'s body and received (.+)\.$'), 'looted_body'),
    (re.compile(r'^Respawning (.+?) \(.+?\) as (.+)\.$'), 'respawn'),
    # Combat & death
    # Character names on ASPER often contain an abbreviated rank with a dot
    # ("Ст. Матрос ...").  The old non-greedy ``(.+?)\.`` stopped at that
    # rank dot and indexed every such attack under the fake player "Ст".
    # The optional damage section is recognisable by Barotrauma's
    # ``Affliction name: numeric strength`` shape, so dots inside names remain
    # part of the attacker while attacks without a non-zero affliction still
    # parse correctly.
    (ATTACKED_BY_RE, 'attack'),
    (re.compile(r'^(.+?) launched (.+)$'), 'attack'),
    (re.compile(r'^(.+) has died \(Cause of death: (.+)\)$'), 'death'),
    (re.compile(r'^(Inner|Outer) wall (breached|repaired) by (.+)$'), 'structure'),
    # Console
    (re.compile(r'^Console command "(.+)" executed by (.+)\.$'), 'console'),
    (re.compile(r'^Client "(.+?)" sent a server command "(.+?)"\. Permission denied\.$'), 'command_denied'),
    # Kick/ban messages — three independent wordings share the 'kick' type
    # (Russian moderator-initiated, English admin-console "Client X kicked
    # Y", English network-desync auto-kick); which capture group holds the
    # target is disambiguated by content inside the dispatch loop, same
    # trick already used for 'attack'/'radio' below.
    (re.compile(r'^Игрок (.+?) исключен с сервера (.*)$'), 'kick'),
    (re.compile(r'^Client "(.+?)" kicked "(.+?)"\.$'), 'kick'),
    (re.compile(r'^(.+?) was kicked because (.+)$'), 'kick'),
    (re.compile(r'^Client "(.+?)" banned "(.+?)"\.$'), 'ban'),
    (re.compile(r'^(.+?) покидает сервер\.$'), 'logout'),
    (re.compile(r'^Игрок (.+?) отключился от игры\. (.*)$'), 'logout'),
    (re.compile(r'^Игрок (.+?) присоединился к серверу\.$'), 'joined'),
    (re.compile(r'^(.+?) is (?:not )?ready to start the game\.$'), 'ready'),
    (re.compile(r'^Игрок (.+?) сменил имя на (.+)\.$'), 'name_change'),
    (re.compile(r'^Пользователь (.+?) ранее использовал имя "(.+?)" на сервере\.$'), 'name_history'),
    # Client-side Lua/.NET errors are reported as a sentence beginning with
    # the player name.  Without an explicit pattern the final ``Name: text``
    # fallback can treat everything before the first colon as a nickname,
    # which polluted autocomplete with values such as
    # ``Player has reported an error``.  Keep the event searchable under the
    # real actor while retaining the complete diagnostic text in details.
    (re.compile(r'^(.+?) has reported an error(?:[:.]?\s*(.*))?$'), 'client_error'),
    # Terminal/computer text entry — functionally chat, just through a
    # device instead of the chat box; kept as its own type so it's still
    # distinguishable, but counted as a player action (see PLAYER_ACTIONS).
    (re.compile(r'^(.+?) entered "(.+)" on (.+)$'), 'terminal'),
    # Network
    (re.compile(r'^Incoming connection from (.+)\.$'), 'connection'),
    (re.compile(r'^\[CustomRadioThirdFork\] (.+)$'), 'radio'),
    # Chat (must be last - catches Name: message)
    (re.compile(r'^(.+?): (.+)$'), 'chat'),
]

# Timestamp pattern(s). Barotrauma's server log has shipped with at least two
# header styles across versions/locales — 24-hour ("[03/15/2024 14:23:01]")
# and 12-hour with an AM/PM suffix ("[3/15/2024 2:23:01 PM]", note the
# non-padded day/hour too) — and BOTH can appear either as a standalone
# header line (content follows on subsequent indented lines) OR inline with
# the event text on the very same line ("[timestamp] SomePlayer: hi").
# The old code only recognised the 24h zero-padded standalone form: a log
# written in the other style never set current_timestamp at all, and since
# every non-timestamp line is skipped while current_timestamp is None (see
# below), that meant the ENTIRE file silently produced zero events — this is
# the root cause of "не все логи вообще парсятся".
_TIMESTAMP_PATTERNS = [
    (re.compile(r'^\[(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [APap][Mm])\]\s*(.*)$'), '%m/%d/%Y %I:%M:%S %p'),
    (re.compile(r'^\[(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2})\]\s*(.*)$'), '%m/%d/%Y %H:%M:%S'),
]


def _match_timestamp(line: str) -> Optional[Tuple[datetime, str]]:
    """Returns (timestamp, rest_of_line) if `line` starts with a recognised
    timestamp header, else None. rest_of_line is '' for a standalone header
    line, or the inline event text that followed the timestamp on the same line."""
    for pattern, fmt in _TIMESTAMP_PATTERNS:
        m = pattern.match(line)
        if not m:
            continue
        try:
            return datetime.strptime(m.group(1), fmt), m.group(2)
        except ValueError:
            continue
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Log Parser
# ══════════════════════════════════════════════════════════════════════════════

def parse_log_content(content: str, server_name: str = "", source_file: str = "") -> Tuple[List[LogEvent], Dict[str, str]]:
    """Parse raw log file content into structured LogEvent objects.

    Returns: (events, player_map) where player_map is {player_name: steamid}
    """
    # Files are UTF-8 WITH a BOM (confirmed against real server exports —
    # raw bytes start EF BB BF), but they're read with plain 'utf-8', which
    # leaves the BOM as a literal U+FEFF character on the first line. That
    # breaks the very first timestamp match (_match_timestamp anchors on
    # ^\[, and ﻿[ doesn't match), silently losing every line up to the
    # SECOND timestamp header in the file. Stripping it here covers both
    # read paths (SFTP download and local re-read) without touching them.
    content = content.lstrip('﻿')

    events: List[LogEvent] = []
    player_map: Dict[str, str] = {}  # name -> steamid

    def process_action_line(action_line: str, ts: datetime) -> None:
        """Classify one already-timestamped line of content and append the
        resulting event(s). Shared between the "standalone timestamp header,
        content on following lines" format and the "[timestamp] content"
        inline format — see _match_timestamp's docstring for why both need
        to be supported."""
        action_line = action_line.strip()
        if not action_line:
            return

        # Check for RoundManager login/logout (extract name→steamid)
        login_match = LOGIN_RE.match(action_line)
        if login_match:
            pname, steamid = login_match.group(1), login_match.group(2)
            player_map[pname] = steamid
            events.append(LogEvent(
                timestamp=ts, player_name=pname, action_type='login',
                details=action_line, raw_line=action_line,
                server_name=server_name, source_file=source_file,
            ))
            return

        logout_match = LOGOUT_RE.match(action_line)
        if logout_match:
            pname, steamid = logout_match.group(1), logout_match.group(2)
            player_map[pname] = steamid
            events.append(LogEvent(
                timestamp=ts, player_name=pname, action_type='logout',
                details=action_line, raw_line=action_line,
                server_name=server_name, source_file=source_file,
            ))
            return

        # Skip [RoundManager_ND] internal messages (kit delivery, data loaded, etc.)
        if action_line.startswith('[RoundManager_ND]'):
            return

        # Skip noise: Saving client permissions, HTML fragments, etc.
        if action_line.startswith('Saving client permissions'):
            return
        if action_line.startswith('<') or action_line.startswith('</'):
            return

        # [ShardLink] ban/kick propagation notices — MUST be checked before
        # the generic "[Tag]-prefixed line is technical noise" rule right
        # below, which would otherwise swallow these with no player
        # attached (see SHARD_BAN_RE/SHARD_KICK_RE definitions for why).
        shard_ban = SHARD_BAN_RE.match(action_line)
        if shard_ban:
            events.append(LogEvent(
                timestamp=ts, player_name=shard_ban.group(1), action_type='ban',
                details=action_line, raw_line=action_line,
                server_name=server_name, source_file=source_file,
            ))
            return
        shard_kick = SHARD_KICK_RE.match(action_line)
        if shard_kick:
            events.append(LogEvent(
                timestamp=ts, player_name=shard_kick.group(1), action_type='kick',
                details=action_line, raw_line=action_line,
                server_name=server_name, source_file=source_file,
            ))
            return

        # Classify the action
        action_type = 'other'
        player_name = ''

        # System/technical lines must NOT be treated as player chat.
        # Anything starting with a [Tag] (e.g. [DemoUploader], [QueueGuard],
        # [DemoRecorder]) or a numbered spawn-manifest line ("2. Инопланетное
        # орудие (ID: ...)") is technical noise — classify as 'other' so the
        # chat-catching regex below doesn't mis-file it into player actions.
        #
        # Исключение — теги, для которых НИЖЕ есть собственный паттерн с
        # разбором игрока (сейчас это [CustomRadioThirdFork], переключение
        # канала рации). Без этой оговорки правило «строка с [Тегом] —
        # технический шум» съедало их раньше, чем до них доходил разбор:
        # в реальных логах это тысячи событий рации, которые оседали в
        # 'other' без игрока и потому не находились ни фильтром по типу
        # события, ни поиском по игроку.
        _is_system = (
            (action_line.startswith('[') and not TAGGED_PLAYER_RE.match(action_line))
            or re.match(r'^\d+\.\s', action_line) is not None
        )

        if _is_system:
            events.append(LogEvent(
                timestamp=ts, player_name='', action_type='other',
                details=action_line, raw_line=action_line,
                server_name=server_name, source_file=source_file,
            ))
            return

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
                    # "(Inner|Outer) wall (breached|repaired) by NAME" — name is group 3.
                    player_name = m.group(3)
                elif atype == 'kick':
                    # Three independent wordings share this type (see
                    # ACTION_PATTERNS) — only "Client "Admin" kicked
                    # "Target"." has the player we care about in group 2,
                    # the target; the other two have it in group 1.
                    if action_line.startswith('Client "') and ' kicked "' in action_line:
                        player_name = m.group(2)
                    else:
                        player_name = m.group(1)
                elif atype == 'ban':
                    # "Client "Admin" banned "Target"." — target is group 2.
                    player_name = m.group(2)
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

        events.append(LogEvent(
            timestamp=ts, player_name=player_name, action_type=action_type,
            details=action_line, raw_line=action_line,
            server_name=server_name, source_file=source_file,
        ))

    current_timestamp: Optional[datetime] = None
    for raw_line in content.split('\n'):
        line = raw_line.rstrip()
        if not line:
            continue

        ts_hit = _match_timestamp(line)
        if ts_hit is not None:
            current_timestamp, rest = ts_hit
            if rest:
                # Inline format: "[timestamp] content" on one line.
                process_action_line(rest, current_timestamp)
            # else: standalone header — content follows on subsequent lines.
            continue

        # Plain content line under the last-seen standalone header.
        if current_timestamp is None:
            # No timestamp has been seen yet at all — this would previously
            # have silently dropped every such line for the whole file if
            # the header format didn't match TIMESTAMP_RE exactly. Now that
            # _match_timestamp covers both known formats this should be rare,
            # but skipping (rather than guessing a timestamp) is still
            # correct: a line with no established time is genuinely unusable
            # for a time-ordered log.
            continue
        process_action_line(line, current_timestamp)

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

# До 17.08.2026 англоязычный автокик по рассинхронизации не имел своего
# ACTION_PATTERN. Если причина содержала двоеточие (обычно ``Events queued:
# N``), последний общий chat-паттерн принимал ВСЮ часть до двоеточия за имя
# игрока. В результате один матрос появлялся в автодополнении десятками
# длинных вариантов с текстом ``was kicked because ...``.
_AUTO_KICK_PLAYER_SEPARATOR = " was kicked because "
_AUTO_KICK_PLAYER_REPAIR_KEY = "repair_auto_kick_player_v1"
_CLIENT_ERROR_PLAYER_SEPARATOR = " has reported an error"
_ATTACK_PLAYER_REPAIR_KEY = "repair_attack_player_v1"

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
    # SQLite's built-in LOWER/LIKE only folds ASCII. Register Unicode
    # casefolding for the correctness fallback used while the FTS backfill is
    # still running: a Russian query such as ``физ`` must match
    # ``Физраствор`` exactly as it does in the browser.
    conn.create_function(
        "nd_casefold",
        1,
        lambda value: str(value or "").casefold(),
        deterministic=True,
    )
    conn.execute("PRAGMA journal_mode=WAL")       # concurrent reads while writing
    conn.execute("PRAGMA synchronous=NORMAL")     # fast, still crash-safe with WAL
    # ВРЕМЕННЫЕ структуры — НА ДИСК, не в RAM. Раньше здесь стояло
    # temp_store=MEMORY, и в оперативку уходило всё временное: сортировки,
    # временные B-деревья для DISTINCT по миллионам строк, рабочий набор
    # VACUUM, слияния сегментов FTS5. На машине с 2 ГБ RAM и базой в
    # несколько ГБ это и давало «зверское» потребление памяти — сами логи
    # при этом никогда не грузились целиком, все выборки ограничены LIMIT.
    conn.execute("PRAGMA temp_store=FILE")
    # Кэш страниц выделяется НА КАЖДОЕ соединение, а соединения здесь
    # thread-local: при пуле в несколько десятков потоков прежние 8 МБ
    # превращались в сотни мегабайт. 2 МБ на поток — разумный потолок.
    conn.execute("PRAGMA cache_size=-2000")       # ~2MB page cache (negative = KB)
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
        -- Сервер + тип события + время: комбинация фильтров «сервер + тип
        -- действия/категория», которую панель шлёт чаще всего. Без неё
        -- SQLite может взять только один из двух индексов и досеивать
        -- остальное перебором — чем больше фильтров выбрано разом, тем
        -- дольше отвечал запрос (ровно то, из-за чего вторая и третья
        -- галочки фильтра выглядели «неработающими»: запрос не возвращался).
        CREATE INDEX IF NOT EXISTS ix_log_server_action_ts ON log_events(server, action, ts);

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
    _repair_legacy_auto_kick_players(conn)
    _repair_legacy_attack_players(conn)


def _normalise_stored_player_name(name: str) -> str:
    """Возвращает настоящее имя из известных legacy-значений парсера.

    Это дешёвая защита для autocomplete на случай, если одноразовый ремонт
    БД временно не смог взять write-lock. Новые строки парсер уже сохраняет
    правильно, а отдельная миграция ниже чинит саму историю и тип события.
    """
    if _AUTO_KICK_PLAYER_SEPARATOR in name:
        return name.split(_AUTO_KICK_PLAYER_SEPARATOR, 1)[0].strip()
    if _CLIENT_ERROR_PLAYER_SEPARATOR in name:
        return name.split(_CLIENT_ERROR_PLAYER_SEPARATOR, 1)[0].strip()
    return name.strip()


def _repair_legacy_auto_kick_players(conn: sqlite3.Connection) -> int:
    """Один раз исправляет загрязнённые player/action в накопленной SQLite.

    Ограничение ``action='chat'`` осознанно ведёт UPDATE через существующий
    индекс ix_log_action вместо полного перебора 1.6+ млн событий. Именно
    chat выставлял прежний fallback-парсер, когда встречал двоеточие внутри
    причины автокика. UPDATE-триггер FTS синхронизирует player/details.
    """
    try:
        if _fts_meta_get(conn, _AUTO_KICK_PLAYER_REPAIR_KEY) == "1":
            return 0
        # Старые строки могли ещё не попасть в незавершённый FTS-бэкфилл.
        # UPDATE-триггер добавил бы исправленную строку сейчас, а бэкфилл
        # позже вставил бы тот же rowid второй раз. Поэтому меняем историю
        # только после полного индекса; autocomplete всё равно сразу очищен
        # через _normalise_stored_player_name(). Без FTS такого риска нет.
        if _fts_available(conn) and _fts_meta_get(conn, "backfill_done") != "1":
            return 0

        # На старте одновременно могут открыться соединения log-worker и
        # HTTP threadpool. IMMEDIATE + повторная проверка под блокировкой не
        # дают им выполнять один и тот же массовый UPDATE параллельно.
        conn.execute("BEGIN IMMEDIATE")
        if _fts_meta_get(conn, _AUTO_KICK_PLAYER_REPAIR_KEY) == "1":
            conn.commit()
            return 0
        if _fts_available(conn) and _fts_meta_get(conn, "backfill_done") != "1":
            conn.commit()
            return 0

        cursor = conn.execute(
            "UPDATE log_events INDEXED BY ix_log_action "
            "SET player = trim(substr(player, 1, instr(player, ?) - 1)), "
            "    action = 'kick' "
            "WHERE action = 'chat' AND instr(player, ?) > 1",
            (_AUTO_KICK_PLAYER_SEPARATOR, _AUTO_KICK_PLAYER_SEPARATOR),
        )
        repaired = max(cursor.rowcount or 0, 0)
        conn.execute(
            "INSERT OR REPLACE INTO fts_meta(key, value) VALUES (?, '1')",
            (_AUTO_KICK_PLAYER_REPAIR_KEY,),
        )
        conn.commit()
        if repaired:
            logger.info("Логи: исправлено legacy-записей автокика: %d", repaired)
        return repaired
    except Exception:
        conn.rollback()
        logger.exception("Логи: не удалось исправить legacy-записи автокика")
        return 0


def _repair_legacy_attack_players(conn: sqlite3.Connection) -> int:
    """Restore attackers truncated at a dot in already indexed PvP rows.

    Raw server log files are intentionally removed after a durable SQLite
    import, but the complete original action line remains in ``details``.
    Reparse only rows behind the indexed ``action='attack'`` predicate and
    update the actor when the old parser stored a truncated rank/name.
    """
    try:
        if _fts_meta_get(conn, _ATTACK_PLAYER_REPAIR_KEY) == "1":
            return 0
        if _fts_available(conn) and _fts_meta_get(conn, "backfill_done") != "1":
            return 0

        conn.execute("BEGIN IMMEDIATE")
        if _fts_meta_get(conn, _ATTACK_PLAYER_REPAIR_KEY) == "1":
            conn.commit()
            return 0
        if _fts_available(conn) and _fts_meta_get(conn, "backfill_done") != "1":
            conn.commit()
            return 0

        rows = conn.execute(
            "SELECT id, player, details FROM log_events INDEXED BY ix_log_action "
            "WHERE action = 'attack' AND instr(details, ' attacked by ') > 0"
        ).fetchall()
        updates = []
        for row_id, stored_player, details in rows:
            match = ATTACKED_BY_RE.match(details or "")
            if not match:
                continue
            attacker = match.group(2).strip()
            if attacker and attacker != (stored_player or "").strip():
                updates.append((attacker, row_id))

        if updates:
            conn.executemany("UPDATE log_events SET player = ? WHERE id = ?", updates)
        conn.execute(
            "INSERT OR REPLACE INTO fts_meta(key, value) VALUES (?, '1')",
            (_ATTACK_PLAYER_REPAIR_KEY,),
        )
        conn.commit()
        if updates:
            logger.info("Логи: восстановлены исполнители PvP-атак: %d", len(updates))
        return len(updates)
    except Exception:
        conn.rollback()
        logger.exception("Логи: не удалось восстановить исполнителей PvP-атак")
        return 0


def _ts_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0)


# Action categories (module-level, shared)
# 'joined' и 'ready' сознательно ИСКЛЮЧЕНЫ (не в этом множестве, т.е.
# считаются технической категорией) — по решению админа: "Игрок X
# присоединился к серверу" дублирует login от RoundManager_ND, а "готов/не
# готов к началу раунда" — служебное состояние лобби, не игровое действие.
PLAYER_ACTIONS = {'picked_up', 'placed', 'removed', 'opened', 'closed', 'equipped',
                  'unequipped', 'dropped', 'threw', 'used', 'reactor', 'repair',
                  'repair_failed', 'fabrication', 'fabrication_cancel', 'activated',
                  'deactivated', 'attached', 'wire', 'pump', 'settings_change', 'console',
                  'command_denied', 'death', 'chat', 'terminal', 'login', 'logout',
                  'respawn', 'attack', 'structure', 'kick', 'ban',
                  'name_change', 'name_history', 'talent_gained', 'looted_body'}


def get_player_map() -> Dict[str, str]:
    """Get merged player name→steamid mapping from SQLite."""
    try:
        conn = _get_conn()
        rows = conn.execute("SELECT name, steamid FROM player_map").fetchall()
        return {name: sid for name, sid in rows}
    except Exception as e:
        logger.error(f"get_player_map failed: {e}")
        return {}


def get_steamids_for_names(names: List[str]) -> Dict[str, str]:
    """Targeted name→steamid lookup for just the given names (e.g. the
    player names actually present on the current page of search results) —
    used to make the "Игрок" column clickable (see routes/logs.py's
    api_logs_events): resolving via a handful of names is cheap, unlike
    loading the entire player_map for every request."""
    if not names:
        return {}
    try:
        conn = _get_conn()
        placeholders = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT name, steamid FROM player_map WHERE name IN ({placeholders})", names,
        ).fetchall()
        return {name: sid for name, sid in rows}
    except Exception as e:
        logger.error(f"get_steamids_for_names failed: {e}")
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

                # The raw .txt has now been fully parsed into log_events (this
                # commit is what makes it durable) — the source file itself
                # served its purpose and just sat here forever otherwise
                # (confirmed on the real server: hundreds of files accumulating
                # with no cleanup at all). Delete AFTER commit, not before, so
                # a crash mid-parse leaves the file in place for a retry next
                # cycle instead of silently losing the round's log.
                try:
                    os.remove(filepath)
                except OSError as e:
                    logger.warning(f"Failed to remove parsed raw log {filename}: {e}")
            except Exception as e:
                conn.rollback()
                logger.warning(f"Failed to parse local log {filename}: {e}")

        # One-off sweep for files parsed in an EARLIER run (before raw-file
        # cleanup existed, or if a previous delete above failed) — anything
        # already recorded in log_files but still sitting on disk is a leak.
        try:
            already_parsed = {row[0] for row in conn.execute(
                "SELECT filename FROM log_files WHERE server=?", (server_name,)
            ).fetchall()}
            for filename in local_files:
                if filename in already_parsed and filename not in {f for f, _ in files_to_parse}:
                    try:
                        os.remove(os.path.join(local_dir, filename))
                    except OSError:
                        pass
        except Exception as e:
            logger.warning(f"Raw log backlog cleanup failed for {server_name}: {e}")

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
                time.sleep(0.2)  # уступить БД записи логов между порциями
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
            # VACUUM переписывает базу целиком во временную копию, поэтому
            # требует свободного места примерно в размер самой базы. Без
            # проверки на почти полном диске он падает уже в процессе,
            # оставляя мусор. Если места мало — просто пропускаем: файл не
            # уменьшится, но освободившиеся страницы всё равно переиспользуются
            # под новые события, то есть база не растёт дальше.
            try:
                import shutil
                db_size = os.path.getsize(LOG_DB_PATH)
                free = shutil.disk_usage(LOG_STORAGE_DIR).free
                if free < db_size * 1.2:
                    logger.warning(
                        f"VACUUM пропущен: на диске {free // (1024*1024)} МБ свободно, "
                        f"для базы в {db_size // (1024*1024)} МБ этого мало"
                    )
                else:
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

# Размер порции бэкфилла. Держится небольшим осознанно: строки порции
# материализуются в память Python (id + имя + текст события), и на машине с
# 2 ГБ RAM крупные порции дают заметные всплески потребления. 2000 строк —
# это единицы мегабайт на шаг.
_FTS_BACKFILL_BATCH = 2000
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
            _repair_legacy_auto_kick_players(conn)
            logger.info("FTS-бэкфилл логов завершён")
            return False

        rows = conn.execute(
            "SELECT id, player, details FROM log_events WHERE id < ? ORDER BY id DESC LIMIT ?",
            (cursor, batch),
        ).fetchall()
        if not rows:
            _fts_meta_set(conn, "backfill_done", "1")
            _repair_legacy_auto_kick_players(conn)
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
            _repair_legacy_auto_kick_players(conn)
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
                 time_from, time_to, category, names_list=None, use_fts=False,
                 player_scope="related"):
    """Собирает FROM/WHERE + параметры для запросов к логам.

    use_fts=True — текстовые фильтры уходят в FTS5 (MATCH по индексу),
    иначе используется прежний LIKE '%...%' (полный скан) как запасной путь,
    пока фоновый бэкфилл не достроил индекс или если FTS5 недоступен.
    Возвращает (from_sql, where, params).
    """
    if player_scope not in {"actor", "related"}:
        raise ValueError("player_scope must be 'actor' or 'related'")

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

    # Обычный режим фильтра игрока — точное совпадение по структурированной
    # колонке player (кто СОВЕРШИЛ действие). Оно обслуживается существующим
    # B-tree индексом ix_log_player и не сканирует миллионы строк details.
    # Поиск упоминаний в details оставлен отдельным, осознанно более тяжёлым
    # режимом related.
    exact_player_filter = player_scope == "actor" and bool(names_list or player_name)
    if exact_player_filter:
        if names_list:
            unique_names = list(dict.fromkeys(names_list))
            placeholders = ",".join("?" * len(unique_names))
            clauses.append(f"log_events.player IN ({placeholders})")
            params.extend(unique_names)
        else:
            clauses.append("log_events.player = ?")
            params.append(player_name)

    # INDEXED BY не даёт планировщику выбрать широкий индекс action для
    # запроса вида «редкий игрок + частое действие». На проде именно такой
    # план превращал фильтр player + wire в 30-секундный скан и 503.
    if exact_player_filter and not use_fts:
        from_sql = "log_events INDEXED BY ix_log_player"
    elif not use_fts and server_name:
        # LIKE cannot use a text B-tree, but a replay search always knows its
        # server and time range. Force SQLite to scan only that round instead
        # of choosing a broad action/details plan over the entire log index.
        if action_type and action_type != "all":
            from_sql = "log_events INDEXED BY ix_log_server_action_ts"
        else:
            from_sql = "log_events INDEXED BY ix_log_server"
    else:
        from_sql = "log_events"
    if use_fts:
        # Все текстовые условия объединяются в ОДНО выражение MATCH — FTS5
        # допускает лишь один MATCH на таблицу, но его синтаксис сам
        # поддерживает AND/OR и фильтр по колонке.
        fts_terms = []
        if player_scope == "related" and names_list:
            ors = " OR ".join(
                f"(player:{_fts_query(n)} OR details:{_fts_query(n)})" for n in names_list
            )
            fts_terms.append(f"({ors})")
        elif player_scope == "related" and player_name:
            fts_terms.append(f"(player:{_fts_query(player_name)} OR details:{_fts_query(player_name)})")
        if search_text:
            fts_terms.append(f"details:{_fts_query(search_text)}")
        if fts_terms:
            from_sql = "log_events JOIN log_fts ON log_fts.rowid = log_events.id"
            clauses.append("log_fts MATCH ?")
            params.append(" AND ".join(fts_terms))
    else:
        if player_scope == "related" and names_list:
            ors = " OR ".join("(nd_casefold(player) LIKE ? OR nd_casefold(details) LIKE ?)" for _ in names_list)
            clauses.append(f"({ors})")
            for n in names_list:
                folded = n.casefold()
                params.extend([f"%{folded}%", f"%{folded}%"])
        elif player_scope == "related" and player_name:
            clauses.append("(nd_casefold(player) LIKE ? OR nd_casefold(details) LIKE ?)")
            folded = player_name.casefold()
            params.extend([f"%{folded}%", f"%{folded}%"])
        if search_text:
            clauses.append("nd_casefold(details) LIKE ?"); params.append(f"%{search_text.casefold()}%")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return from_sql, where, params


class LogSearchResult(NamedTuple):
    """Результат поиска по логам.

    Кортеж (а не просто список) потому, что подвал таблицы должен знать не
    только строки текущей страницы, но и есть ли следующая.

    total          — сколько всего совпало; None, если посчитать не удалось
                     за отведённый бюджет времени (см. _bounded_count).
    total_is_capped — совпадений больше, чем показывает total ("5000+").
    has_more       — есть ли ещё строки ПОСЛЕ текущей страницы. Считается
                     отдельно от total (запросом limit+1) именно для того,
                     чтобы перелистывание работало даже когда total неизвестен.
    next_cursor    — непрозрачная позиция последней строки страницы для
                     следующего keyset-запроса без медленного OFFSET.
    """
    events: List[LogEvent]
    total: Optional[int]
    total_is_capped: bool
    has_more: bool
    next_cursor: Optional[str]


class LogTimelineOverview(NamedTuple):
    """Compact, truthful overview for an editor timeline.

    ``buckets`` groups by time/action and never materialises the underlying
    log rows. ``players`` is separately capped because a server can accumulate
    hundreds of one-off character names during a long round.
    """
    buckets: List[dict]
    players: List[dict]


class InvalidLogCursor(ValueError):
    """Курсор логов повреждён или имеет неизвестный формат."""


_LOG_CURSOR_VERSION = "1"
_LOG_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")


def _encode_log_cursor(timestamp_ms: int, row_id: int) -> str:
    payload = f"{_LOG_CURSOR_VERSION}:{timestamp_ms}:{row_id}".encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_log_cursor(cursor: str) -> tuple[int, int]:
    if not _LOG_CURSOR_RE.fullmatch(cursor):
        raise InvalidLogCursor("Некорректный курсор страницы логов.")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        version, timestamp_raw, row_id_raw = decoded.split(":", 2)
        timestamp_ms = int(timestamp_raw)
        row_id = int(row_id_raw)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidLogCursor("Некорректный курсор страницы логов.") from exc
    if version != _LOG_CURSOR_VERSION or timestamp_ms < 0 or row_id <= 0:
        raise InvalidLogCursor("Некорректный курсор страницы логов.")
    return timestamp_ms, row_id


# Сколько секунд максимум разрешено считать общее число совпадений. Счёт —
# сугубо справочная цифра в подвале таблицы; листание, наоборот, работать
# обязано. Раньше COUNT по редкой комбинации фильтров сканировал всю историю,
# запрос не укладывался в таймаут прокси, страница молча оставалась со
# старыми строками — и это выглядело как «фильтры и перелистывание не
# работают». Теперь счёт просто прерывается, а строки отдаются всегда.
_COUNT_TIME_BUDGET_SEC = 2.0

# Сколько максимум разрешено выбирать СТРОКИ страницы. Раньше бюджет стоял
# только на COUNT, а сам SELECT не был ограничен ничем — и на редкой
# комбинации фильтров (LIKE-путь, глубокий OFFSET, category='technical' с
# несargable NOT IN) один запрос к /api/v1/admin/logs/events молотил по
# 50+ минут. Хуже того, клиент к тому времени давно ушёл: read-timeout
# прокси 180 с, после него пользователь видел ошибку и жал «обновить», а
# ПРЕЖНИЙ скан продолжал жечь ядро в threadpool ещё почти час, и такие
# сканы копились друг на друга. Ограничение делает тяжёлый запрос честно
# падающим за 10 с вместо того, чтобы уносить сервер.
_ROW_TIME_BUDGET_SEC = 10.0

# Полный поиск внутри одного реплея принципиально не зависит от FTS. В нём
# уже известны сервер и короткий (обычно 20-120 минут) временной диапазон,
# поэтому надёжнее последовательно прочитать только этот диапазон по
# составному B-tree индексу и проверить Unicode-текст в Python. Так запрос не
# меняет поведение в зависимости от состояния фонового FTS-бэкфилла и не
# требует Redis/отдельного поискового сервиса.
_ROUND_SEARCH_TIME_BUDGET_SEC = 12.0
_ROUND_SEARCH_FETCH_SIZE = 2_000

# Через сколько «виртуальных инструкций» SQLite дёргает progress-handler.
# Это ПИТОНОВСКИЙ колбэк, и каждый его вызов забирает GIL. При прежних
# 10 000 он срабатывал десятки тысяч раз в секунду, и поток из threadpool
# на долгом скане непрерывно выдёргивал GIL у event loop'а — отсюда
# «зависания сайта» по 0.5-0.8 с в перф-мониторе при живом, ничем не
# занятом бэкенде. Миллион инструкций проверяет дедлайн всё ещё сотни раз
# в секунду (точность прерывания — единицы миллисекунд), но обходится на
# два порядка дешевле.
_PROGRESS_GRANULARITY = 1_000_000


class LogSearchTooSlow(Exception):
    """Выборка не уложилась в _ROW_TIME_BUDGET_SEC и была прервана.

    Отдельный тип, а не пустой результат: пустая таблица читается как
    «событий нет» и уводит расследование не туда, тогда как здесь надо
    сказать «уточните фильтры»."""


class LogDataUnavailable(RuntimeError):
    """Хранилище или индекс логов недоступны для достоверной выборки.

    Это не означает, что совпадений нет.  Обычные страницы логов исторически
    показывают пустой результат при сбое SQLite, поэтому ``search_logs``
    поднимает исключение только по явному запросу вызывающего. Timeline
    реплея включает строгий режим: иначе поломанный индекс выглядел бы как
    спокойный раунд без единого события.
    """


class _Budget:
    """Progress-handler SQLite: ненулевой ответ прерывает идущий запрос.

    Помнит факт срабатывания, чтобы вызывающий мог отличить прерывание по
    таймауту от настоящей ошибки БД, не разбирая текст исключения."""

    __slots__ = ("deadline", "expired")

    def __init__(self, seconds: float):
        self.deadline = time.monotonic() + seconds
        self.expired = False

    def __call__(self) -> int:
        if time.monotonic() > self.deadline:
            self.expired = True
            return 1
        return 0


@contextmanager
def _apply_budget(conn: sqlite3.Connection, budget: "_Budget"):
    """Вешает бюджет на соединение на время блока и обязательно снимает.

    Соединения тут thread-local (см. _get_conn), поэтому handler никогда не
    заденет запрос соседнего потока."""
    conn.set_progress_handler(budget, _PROGRESS_GRANULARITY)
    try:
        yield
    finally:
        conn.set_progress_handler(None, 0)


def _bounded_count(conn: sqlite3.Connection, sql: str, params: list) -> Optional[int]:
    """COUNT с ограничением по времени: None, если не уложились в бюджет.

    Прерывание делается штатным progress-handler'ом SQLite — это
    единственный способ остановить уже идущий скан, timeout соединения тут не
    поможет: он про ожидание блокировки, а не про длительность запроса.
    """
    try:
        with _apply_budget(conn, _Budget(_COUNT_TIME_BUDGET_SEC)):
            return conn.execute(sql, params).fetchone()[0]
    except sqlite3.OperationalError:
        return None


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
    player_scope: str = "related",
    cursor: Optional[str] = None,
    count_total: bool = True,
    raise_on_unavailable: bool = False,
) -> LogSearchResult:
    """Search log events via indexed SQL.

    Все переданные фильтры применяются ОДНОВРЕМЕННО (AND) — см. _build_where.

    Returns a LogSearchResult; constant memory: only `limit` rows are
    materialized. `total` is capped at COUNT_CAP and may be None when counting
    didn't fit into its time budget — `has_more` is authoritative for
    pagination in both cases.
    """
    try:
        conn = _get_conn()
    except Exception as e:
        logger.error(f"search_logs: DB unavailable: {e}")
        if raise_on_unavailable:
            raise LogDataUnavailable("Хранилище серверных логов недоступно.") from e
        # Именно кортеж нужной формы: вызывающие распаковывают результат, и
        # короткий кортеж на этом пути раньше валил весь запрос страницы
        # логов пятисоткой вместо пустой таблицы.
        return LogSearchResult([], 0, False, False, None)

    search_started = time.monotonic()

    # Текстовые фильтры идут через FTS5, как только индекс достроен; пока
    # фоновый бэкфилл не закончил (или если FTS5 нет в сборке SQLite) —
    # прежний LIKE-путь, чтобы поиск оставался корректным, пусть и медленным.
    has_related_player_filter = player_scope == "related" and bool(player_name or names_list)
    has_text_filter = bool(search_text or has_related_player_filter)
    use_fts = has_text_filter and fts_is_ready(conn)

    from_sql, base_where, base_params = _build_where(
        server_name,
        player_name,
        action_type,
        search_text,
        time_from,
        time_to,
        category,
        names_list,
        use_fts=use_fts,
        player_scope=player_scope,
    )
    row_where = base_where
    row_params = list(base_params)
    effective_offset = offset
    if cursor:
        cursor_ts, cursor_id = _decode_log_cursor(cursor)
        # Row-value comparison exactly matches ORDER BY ts DESC, id DESC and,
        # unlike the logically equivalent OR, lets SQLite SEEK into ix_log_ts
        # (and composite action/server indexes) instead of scanning from the
        # newest row to the cursor on every deep page.
        cursor_clause = "(log_events.ts, log_events.id) < (?, ?)"
        row_where += (" AND " if row_where else " WHERE ") + cursor_clause
        row_params.extend([cursor_ts, cursor_id])
        effective_offset = 0
    strategy = (
        "fts" if use_fts
        else "actor_index" if player_scope == "actor" and bool(player_name or names_list)
        else "like" if has_text_filter
        else "indexed"
    )

    # Строки берутся ПЕРВЫМИ и с запасом в одну: наличие (limit+1)-й строки и
    # есть ответ на вопрос «есть ли следующая страница», без всякого COUNT.
    #
    # Сортировка — по ts И по id: в реальных логах десятки строк делят одну и
    # ту же секунду, а порядок одинаковых ts SQLite не гарантирует и вправе
    # выбрать разный для разных OFFSET (планы запроса у страниц отличаются).
    # Из-за этого соседние страницы могли показывать одни и те же события, а
    # другие — терять. id монотонно растёт со временем вставки, поэтому он и
    # доупорядочивает, и совпадает с ожидаемым «новые сверху».
    budget = _Budget(_ROW_TIME_BUDGET_SEC)
    try:
        with _apply_budget(conn, budget):
            rows = conn.execute(
                f"SELECT log_events.ts, log_events.id, log_events.player, log_events.action, log_events.details, "
                f"log_events.server, log_events.source_file FROM {from_sql}{row_where} "
                f"ORDER BY log_events.ts DESC, log_events.id DESC LIMIT ? OFFSET ?",
                row_params + [limit + 1, effective_offset]
            ).fetchall()
    except sqlite3.Error as e:
        if budget.expired:
            logger.warning(
                f"search_logs: прерван по бюджету {_ROW_TIME_BUDGET_SEC}s "
                f"(strategy={strategy}, offset={effective_offset}, cursor={bool(cursor)}, category={category!r}, "
                f"text={bool(search_text)}, names={bool(names_list or player_name)})"
            )
            raise LogSearchTooSlow() from e
        logger.error(f"search_logs: query failed: {e}")
        if raise_on_unavailable:
            raise LogDataUnavailable("Индекс серверных логов недоступен.") from e
        return LogSearchResult([], 0, False, False, None)

    has_more = len(rows) > limit
    rows = rows[:limit]

    # Ограниченный COUNT. Через FTS он дёшев, но на LIKE-пути (пока индекс
    # строится) COUNT(*) обязан просканировать всю таблицу — счёт внутри
    # LIMIT-подзапроса ограничивает работу COUNT_CAP строками независимо от
    # размера истории, а бюджет времени сверху — ещё и по времени. Точный
    # итог того не стоит: вызывающий получает флаг и показывает "5000+".
    capped = _bounded_count(
        conn,
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {from_sql}{base_where} LIMIT {COUNT_CAP + 1})",
        base_params,
    ) if count_total else None
    if not count_total:
        total = None
        total_is_capped = False
    elif capped is None:
        total: Optional[int] = None
        total_is_capped = True
    else:
        total = min(capped, COUNT_CAP)
        total_is_capped = capped > COUNT_CAP

    events = [
        LogEvent(
            timestamp=_ms_to_dt(r[0]),
            row_id=r[1],
            player_name=r[2] or "",
            action_type=r[3],
            details=r[4] or "",
            raw_line=r[4] or "",
            server_name=r[5] or "",
            source_file=r[6] or "",
        )
        for r in rows
    ]
    next_cursor = _encode_log_cursor(rows[-1][0], rows[-1][1]) if has_more and rows else None
    elapsed_ms = int((time.monotonic() - search_started) * 1000)
    logger.info(
        "logs.search.complete elapsed_ms=%d strategy=%s rows=%d offset=%d cursor=%s "
        "counted=%s server=%s action=%s category=%s text=%s players=%d",
        elapsed_ms,
        strategy,
        len(events),
        effective_offset,
        bool(cursor),
        count_total,
        server_name or "all",
        action_type or "all",
        category or "all",
        bool(search_text),
        len(names_list) if names_list else int(bool(player_name)),
    )
    return LogSearchResult(events, total, total_is_capped, has_more, next_cursor)


def search_logs_in_round(
    *,
    server_name: str,
    time_from: datetime,
    time_to: datetime,
    search_text: str,
    action_type: Optional[str] = None,
    category: Optional[str] = None,
    player_name: Optional[str] = None,
    names_list: Optional[List[str]] = None,
    player_scope: str = "related",
    limit: int = 5_000,
    raise_on_unavailable: bool = False,
) -> LogSearchResult:
    """Надёжный literal-поиск по серверным логам одного раунда.

    SQL здесь отвечает только за ограничение ``server + ts`` (и, при
    наличии, ``action``). Эти поля обслуживаются составными индексами и
    позволяют стримить даже плотный раунд без широкого скана всей истории.
    Сам текст сравнивается через :py:meth:`str.casefold` в Python, поэтому
    запросы вроде ``физ`` стабильно находят ``Физраствор`` независимо от
    SQLite NOCASE, локали и готовности FTS5.

    В память попадает не весь раунд: курсор читает фиксированные порции, а
    совпадения сохраняются только до ``limit + 1``. ``has_more`` означает,
    что совпадений больше лимита; точный COUNT намеренно не запускается.
    """
    if not server_name:
        raise ValueError("server_name обязателен для поиска по раунду")
    if time_to < time_from:
        raise ValueError("time_to должен быть не раньше time_from")
    if player_scope not in {"actor", "related"}:
        raise ValueError("player_scope must be 'actor' or 'related'")
    if limit <= 0:
        raise ValueError("limit должен быть положительным")

    try:
        conn = _get_conn()
    except Exception as exc:
        logger.error("search_logs_in_round: DB unavailable: %s", exc)
        if raise_on_unavailable:
            raise LogDataUnavailable("Хранилище серверных логов недоступно.") from exc
        return LogSearchResult([], 0, False, False, None)

    clauses = ["server = ?", "ts >= ?", "ts <= ?"]
    params: list = [server_name, _ts_to_ms(time_from), _ts_to_ms(time_to)]
    selected_action = action_type if action_type and action_type != "all" else None
    if selected_action:
        clauses.append("action = ?")
        params.append(selected_action)
        from_sql = "log_events INDEXED BY ix_log_server_action_ts"
    else:
        from_sql = "log_events INDEXED BY ix_log_server"

    if category == "player":
        placeholders = ",".join("?" * len(PLAYER_ACTIONS))
        clauses.append(f"action IN ({placeholders})")
        params.extend(PLAYER_ACTIONS)
    elif category == "technical":
        placeholders = ",".join("?" * len(PLAYER_ACTIONS))
        clauses.append(f"action NOT IN ({placeholders})")
        params.extend(PLAYER_ACTIONS)

    requested_names = list(dict.fromkeys(names_list or ([player_name] if player_name else [])))
    exact_players = set(requested_names) if player_scope == "actor" else set()
    related_players = [name.casefold() for name in requested_names] if player_scope == "related" else []
    folded_search = (search_text or "").strip().casefold()

    sql = (
        "SELECT ts, id, player, action, details, server, source_file "
        f"FROM {from_sql} WHERE {' AND '.join(clauses)} "
        "ORDER BY ts DESC, id DESC"
    )
    started = time.monotonic()
    budget = _Budget(_ROUND_SEARCH_TIME_BUDGET_SEC)
    matched_rows: list[tuple] = []
    scanned = 0
    manually_expired = False
    try:
        with _apply_budget(conn, budget):
            cursor = conn.execute(sql, params)
            while len(matched_rows) <= limit:
                batch = cursor.fetchmany(_ROUND_SEARCH_FETCH_SIZE)
                if not batch:
                    break
                scanned += len(batch)
                for row in batch:
                    actor = row[2] or ""
                    details = row[4] or ""
                    if exact_players and actor not in exact_players:
                        continue
                    folded_haystack = f"{actor}\n{row[3] or ''}\n{details}\n{row[6] or ''}".casefold()
                    if related_players and not any(name in folded_haystack for name in related_players):
                        continue
                    if folded_search and folded_search not in folded_haystack:
                        continue
                    matched_rows.append(row)
                    if len(matched_rows) > limit:
                        break
                if time.monotonic() > budget.deadline:
                    manually_expired = True
                    break
    except sqlite3.Error as exc:
        if budget.expired:
            logger.warning(
                "logs.round_search.timeout server=%s action=%s scanned=%d",
                server_name,
                selected_action or "all",
                scanned,
            )
            raise LogSearchTooSlow() from exc
        logger.error("search_logs_in_round: query failed: %s", exc)
        if raise_on_unavailable:
            raise LogDataUnavailable("Индекс серверных логов недоступен.") from exc
        return LogSearchResult([], 0, False, False, None)

    if manually_expired:
        logger.warning(
            "logs.round_search.timeout server=%s action=%s scanned=%d",
            server_name,
            selected_action or "all",
            scanned,
        )
        raise LogSearchTooSlow()

    has_more = len(matched_rows) > limit
    matched_rows = matched_rows[:limit]
    events = [
        LogEvent(
            timestamp=_ms_to_dt(row[0]),
            row_id=row[1],
            player_name=row[2] or "",
            action_type=row[3],
            details=row[4] or "",
            raw_line=row[4] or "",
            server_name=row[5] or "",
            source_file=row[6] or "",
        )
        for row in matched_rows
    ]
    elapsed_ms = int((time.monotonic() - started) * 1_000)
    logger.info(
        "logs.round_search.complete elapsed_ms=%d scanned=%d matches=%d capped=%s "
        "server=%s action=%s category=%s text_chars=%d",
        elapsed_ms,
        scanned,
        len(events),
        has_more,
        server_name,
        selected_action or "all",
        category or "all",
        len(search_text or ""),
    )
    # Если диапазон исчерпан, число найденных строк уже точное. При ранней
    # остановке UI честно покажет его как ограниченное лимитом.
    return LogSearchResult(
        events,
        len(events),
        has_more,
        has_more,
        None,
    )


def get_log_timeline_overview(
    *,
    server_name: str,
    time_from: datetime,
    time_to: datetime,
    bucket_ms: int,
    player_limit: int = 300,
    player_name: Optional[str] = None,
) -> LogTimelineOverview:
    """Aggregate one replay range without loading thousands of log rows.

    The compound ``(server, ts)`` index constrains both queries to the round.
    This is the overview used while the browser shows the whole replay; exact
    rows are fetched only for the zoomed visible window.
    """
    start_ms = _ts_to_ms(time_from)
    end_ms = _ts_to_ms(time_to)
    bucket_ms = max(250, int(bucket_ms))
    try:
        conn = _get_conn()
    except Exception as exc:
        logger.error("timeline overview DB unavailable: %s", exc)
        raise LogDataUnavailable("Хранилище серверных логов недоступно.") from exc

    budget = _Budget(_ROW_TIME_BUDGET_SEC)
    actor_clause = " AND player=?" if player_name else ""
    actor_params: List[object] = [player_name] if player_name else []
    try:
        with _apply_budget(conn, budget):
            bucket_rows = conn.execute(
                "SELECT CAST((ts - ?) / ? AS INTEGER) AS bucket, action, COUNT(*), "
                "COUNT(DISTINCT NULLIF(player, '')) AS player_count, "
                "MIN(NULLIF(player, '')) AS representative_player, "
                "COUNT(NULLIF(player, '')) AS attributed_count "
                "FROM log_events INDEXED BY ix_log_server "
                f"WHERE server=? AND ts>=? AND ts<=?{actor_clause} "
                "GROUP BY bucket, action ORDER BY bucket ASC, action ASC",
                (start_ms, bucket_ms, server_name, start_ms, end_ms, *actor_params),
            ).fetchall()
            player_rows = conn.execute(
                "SELECT player, COUNT(*) AS amount "
                "FROM log_events INDEXED BY ix_log_server "
                f"WHERE server=? AND ts>=? AND ts<=? AND player IS NOT NULL AND player<>''{actor_clause} "
                "GROUP BY player ORDER BY amount DESC, player ASC LIMIT ?",
                (server_name, start_ms, end_ms, *actor_params, max(1, min(int(player_limit), 1000))),
            ).fetchall()
    except sqlite3.Error as exc:
        if budget.expired:
            raise LogSearchTooSlow() from exc
        logger.error("timeline overview query failed: %s", exc)
        raise LogDataUnavailable("Индекс серверных логов недоступен.") from exc

    return LogTimelineOverview(
        [
            {
                "offset_ms": max(0, int(row[0]) * bucket_ms + bucket_ms // 2),
                "action_type": row[1] or "other",
                "count": int(row[2]),
                "player_count": int(row[3] or 0),
                # Naming one actor is truthful only when every attributed row
                # in this bucket/action belongs to that same player.
                "player_name": (
                    row[4]
                    if int(row[3] or 0) == 1 and int(row[5] or 0) == int(row[2])
                    else None
                ),
            }
            for row in bucket_rows
        ],
        [
            {"name": row[0], "event_count": int(row[1])}
            for row in player_rows
            if row[0]
        ],
    )


def get_cached_logs(server_name: Optional[str] = None) -> List[LogEvent]:
    """DEPRECATED compatibility shim. Returns a bounded recent slice via SQL.

    Old callers expected the full list; we cap to 5000 most-recent events to
    avoid loading the entire history into RAM. Prefer search_logs() instead.
    """
    return search_logs(server_name=server_name, limit=5000, offset=0).events


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
        # Нормализация плюс set схлопывают десятки legacy-вариантов одного
        # матроса в одну подсказку даже если ремонт БД не успел выполниться.
        result = sorted({
            clean
            for row in rows
            if (clean := _normalise_stored_player_name(row[0] or ""))
        })
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

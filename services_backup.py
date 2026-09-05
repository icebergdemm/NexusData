"""Encrypted SFTP backups for NexusData.

Security invariants:
  * credentials are never written to application logs or subprocess arguments;
  * the SSH host key is pinned after the first successful connection (TOFU),
    or checked strictly immediately when a known_hosts file already exists;
  * the complete archive is encrypted with AES-256-GCM before upload;
  * remote upload is atomic (``.part`` -> final name) and mode 0600;
  * plaintext database snapshots exist only in a mode-0700 temporary directory
    and are removed on both success and failure;
  * operational logs contain identifiers and sizes, never credentials, archive
    contents, database URLs, or encryption keys.

The active application databases/log files are intentionally not rewritten or
encrypted in place.  This module protects the off-site backup copy; replacing
the live log SQLite database with SQLCipher would be a separate migration.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable
from urllib.parse import urlsplit

import paramiko
import pymysql
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy.engine import make_url

from config import Settings, get_settings


logger = logging.getLogger(__name__)

ARCHIVE_MAGIC = b"NEXUSBK1"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
ARCHIVE_RE = re.compile(
    r"^nexusdata-\d{8}T\d{6}Z-[0-9a-f]{8}\.tar\.gz\.aes$"
)
PROJECT_ROOT = Path(__file__).resolve().parent
LOG_INDEX_PATH = PROJECT_ROOT / "data" / "logs_cache" / "logs.db"
BACKUP_LOCK_PATH = PROJECT_ROOT / "data" / "backup.lock"
DEFAULT_KNOWN_HOSTS_PATH = PROJECT_ROOT / "data" / "backup_known_hosts"

_backup_lock = threading.Lock()


class BackupConfigurationError(RuntimeError):
    """The backup is enabled but its safe configuration is incomplete."""


class BackupExecutionError(RuntimeError):
    """A backup failed without exposing credentials in its message."""


def decode_encryption_key(value: str) -> bytes:
    """Decode an exact 256-bit key from regular or URL-safe base64."""

    raw = (value or "").strip().encode("ascii", errors="strict")
    if not raw:
        raise BackupConfigurationError("BACKUP_ENCRYPTION_KEY is required")
    raw += b"=" * (-len(raw) % 4)
    try:
        key = base64.b64decode(raw, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise BackupConfigurationError(
            "BACKUP_ENCRYPTION_KEY must be valid base64"
        ) from exc
    if len(key) != 32:
        raise BackupConfigurationError(
            "BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )
    return key


def validate_backup_settings(settings: Settings) -> dict:
    """Validate fail-closed backup settings and return normalized values."""

    # Select password aliases as one coherent profile. This prevents a stale
    # BACKUP_SFTP_USERNAME/key path from being combined with SFTP_PASSWORD.
    use_compact_password = bool(settings.sftp_password) and not bool(
        settings.backup_sftp_password
    )
    if use_compact_password:
        host_value = settings.sftp_ip or settings.backup_sftp_host
        username_value = settings.sftp_user or settings.backup_sftp_username
        password = settings.sftp_password
        private_key_value = ""
        port = settings.sftp_port
    else:
        host_value = settings.backup_sftp_host or settings.sftp_ip
        username_value = settings.backup_sftp_username or settings.sftp_user
        password = settings.backup_sftp_password
        private_key_value = settings.backup_sftp_private_key_path
        port = settings.backup_sftp_port

    host = (host_value or "").strip()
    if not host:
        raise BackupConfigurationError("BACKUP_SFTP_HOST or SFTP_IP is required")
    if "://" in host:
        parsed_host = urlsplit(host).hostname
        if not parsed_host:
            raise BackupConfigurationError("SFTP host URL does not contain a hostname/IP")
        host = parsed_host
    if "/" in host or any(char.isspace() for char in host):
        raise BackupConfigurationError(
            "SFTP host must be a hostname/IP without a path"
        )

    username = (username_value or "").strip()
    if not username:
        raise BackupConfigurationError("BACKUP_SFTP_USERNAME or SFTP_USER is required")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", username):
        raise BackupConfigurationError("BACKUP_SFTP_USERNAME contains unsafe characters")

    private_key: Path | None = None
    if private_key_value:
        configured_key = Path(private_key_value).expanduser()
        if configured_key.is_file():
            private_key = configured_key.resolve()
            key_mode = stat.S_IMODE(private_key.stat().st_mode)
            if key_mode & 0o077:
                raise BackupConfigurationError(
                    "BACKUP_SFTP_PRIVATE_KEY_PATH must not be readable by group/others (use chmod 600)"
                )
        elif not password:
            raise BackupConfigurationError(
                "BACKUP_SFTP_PRIVATE_KEY_PATH must point to an existing file"
            )
    if private_key is None and not password:
        raise BackupConfigurationError(
            "BACKUP_SFTP_PASSWORD/SFTP_PASSWORD or an existing private key is required"
        )

    known_hosts = Path(
        settings.backup_sftp_known_hosts_path or DEFAULT_KNOWN_HOSTS_PATH
    ).expanduser().resolve()
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    if not known_hosts.exists():
        descriptor = os.open(known_hosts, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    elif not known_hosts.is_file():
        raise BackupConfigurationError("BACKUP_SFTP_KNOWN_HOSTS_PATH must be a file")
    host_key_is_pinned = known_hosts.stat().st_size > 0

    remote_dir = posixpath.normpath((settings.backup_sftp_remote_dir or "").strip())
    if not remote_dir.startswith("/") or remote_dir in ("/", "/."):
        raise BackupConfigurationError(
            "BACKUP_SFTP_REMOTE_DIR must be an absolute non-root SFTP path"
        )

    encryption_key = decode_encryption_key(settings.backup_encryption_key)
    return {
        "host": host,
        "username": username,
        "password": password or None,
        "private_key": private_key,
        "known_hosts": known_hosts,
        "host_key_is_pinned": host_key_is_pinned,
        "remote_dir": remote_dir,
        "encryption_key": encryption_key,
        "port": port,
    }


class _GCMEncryptingWriter:
    """Minimal sequential file object used by tarfile's streaming mode."""

    def __init__(self, target: BinaryIO, key: bytes):
        self._target = target
        self._nonce = os.urandom(NONCE_SIZE)
        self._encryptor = Cipher(algorithms.AES(key), modes.GCM(self._nonce)).encryptor()
        self._encryptor.authenticate_additional_data(ARCHIVE_MAGIC)
        self._position = 0
        self._finalized = False
        target.write(ARCHIVE_MAGIC)
        target.write(self._nonce)

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def write(self, data: bytes) -> int:
        if self._finalized:
            raise ValueError("encrypted writer is finalized")
        if not data:
            return 0
        self._target.write(self._encryptor.update(data))
        self._position += len(data)
        return len(data)

    def flush(self) -> None:
        self._target.flush()

    def finalize(self) -> None:
        if self._finalized:
            return
        self._target.write(self._encryptor.finalize())
        self._target.write(self._encryptor.tag)
        self._target.flush()
        os.fsync(self._target.fileno())
        self._finalized = True


def decrypt_archive(encrypted_path: Path, output_tar_path: Path, key_value: str | bytes) -> None:
    """Decrypt one archive for disaster recovery, authenticating it first."""

    key = key_value if isinstance(key_value, bytes) else decode_encryption_key(key_value)
    encrypted_path = Path(encrypted_path)
    output_tar_path = Path(output_tar_path)
    minimum = len(ARCHIVE_MAGIC) + NONCE_SIZE + TAG_SIZE
    if encrypted_path.stat().st_size <= minimum:
        raise BackupExecutionError("backup archive is truncated")

    with encrypted_path.open("rb") as source:
        if source.read(len(ARCHIVE_MAGIC)) != ARCHIVE_MAGIC:
            raise BackupExecutionError("unsupported backup archive format")
        nonce = source.read(NONCE_SIZE)
        source.seek(-TAG_SIZE, os.SEEK_END)
        tag = source.read(TAG_SIZE)
        ciphertext_end = source.tell() - TAG_SIZE
        source.seek(len(ARCHIVE_MAGIC) + NONCE_SIZE)

        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(ARCHIVE_MAGIC)
        try:
            with output_tar_path.open("xb") as target:
                os.chmod(output_tar_path, 0o600)
                remaining = ciphertext_end - source.tell()
                while remaining:
                    chunk = source.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupExecutionError("backup archive is truncated")
                    target.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                target.write(decryptor.finalize())
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            with suppress(FileNotFoundError):
                output_tar_path.unlink()
            raise


def _mysql_identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise BackupExecutionError("database contains an unsafe SQL identifier")
    return f"`{value.replace('`', '``')}`"


def _dump_mysql_with_pymysql(url, destination: Path) -> dict:
    """Portable schema/data dump used when the system client is unavailable.

    Rows are read with an unbuffered cursor and emitted in bounded INSERT
    batches, so the fallback does not load a large production table into RAM.
    """

    connection = None
    table_count = 0
    row_count = 0
    try:
        connection = pymysql.connect(
            host=url.host or "localhost",
            port=url.port or 3306,
            user=url.username,
            password=url.password or "",
            database=url.database,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=10,
            read_timeout=300,
            write_timeout=30,
        )

        tables: list[tuple[str, str, list[str]]] = []
        views: list[tuple[str, str]] = []
        skipped_objects: list[str] = []
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute("SHOW FULL TABLES")
            objects = cursor.fetchall()
            for object_name, object_type in objects:
                name = str(object_name)
                quoted_name = _mysql_identifier(name)
                cursor.execute(f"SHOW CREATE TABLE {quoted_name}")
                create_row = cursor.fetchone()
                if not create_row or len(create_row) < 2:
                    raise BackupExecutionError("database object schema could not be read")
                create_sql = str(create_row[1]).rstrip(";\n")
                if str(object_type).upper() == "BASE TABLE":
                    cursor.execute(f"SHOW COLUMNS FROM {quoted_name}")
                    # Generated columns are recomputed by MySQL and must not be
                    # included in INSERT statements during restore.
                    columns = [
                        str(column[0])
                        for column in cursor.fetchall()
                        if len(column) < 6 or "GENERATED" not in str(column[5] or "").upper()
                    ]
                    tables.append((name, create_sql, columns))
                elif str(object_type).upper() == "VIEW":
                    views.append((name, create_sql))
                else:
                    skipped_objects.append(name)

        with destination.open("x", encoding="utf-8", newline="\n") as output:
            os.chmod(destination, 0o600)
            output.write("-- NexusData built-in MySQL/MariaDB backup\n")
            output.write("SET NAMES utf8mb4;\n")
            output.write("SET FOREIGN_KEY_CHECKS=0;\n")
            output.write("SET UNIQUE_CHECKS=0;\n\n")

            for table_name, create_sql, columns in tables:
                quoted_table = _mysql_identifier(table_name)
                output.write(f"DROP TABLE IF EXISTS {quoted_table};\n")
                output.write(f"{create_sql};\n")
                table_count += 1
                if not columns:
                    output.write("\n")
                    continue

                quoted_columns = ",".join(_mysql_identifier(column) for column in columns)
                query = f"SELECT {quoted_columns} FROM {quoted_table}"
                data_cursor = connection.cursor(pymysql.cursors.SSCursor)
                try:
                    data_cursor.execute(query)
                    batch: list[str] = []
                    batch_bytes = 0
                    for row in data_cursor:
                        values = ",".join(connection.escape(value) for value in row)
                        encoded_row = f"({values})"
                        batch.append(encoded_row)
                        batch_bytes += len(encoded_row)
                        row_count += 1
                        if len(batch) >= 250 or batch_bytes >= CHUNK_SIZE:
                            output.write(
                                f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES\n"
                                + ",\n".join(batch)
                                + ";\n"
                            )
                            batch.clear()
                            batch_bytes = 0
                    if batch:
                        output.write(
                            f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES\n"
                            + ",\n".join(batch)
                            + ";\n"
                        )
                finally:
                    data_cursor.close()
                output.write("\n")

            for view_name, create_sql in views:
                quoted_view = _mysql_identifier(view_name)
                output.write(f"DROP VIEW IF EXISTS {quoted_view};\n")
                output.write(f"{create_sql};\n\n")

            output.write("SET UNIQUE_CHECKS=1;\n")
            output.write("SET FOREIGN_KEY_CHECKS=1;\n")
            output.flush()
            os.fsync(output.fileno())

        connection.rollback()
        return {
            "included": True,
            "bytes": destination.stat().st_size,
            "method": "pymysql",
            "tables": table_count,
            "rows": row_count,
            "views": len(views),
            "skipped_objects": len(skipped_objects),
        }
    except (BackupConfigurationError, BackupExecutionError):
        with suppress(FileNotFoundError):
            destination.unlink()
        raise
    except Exception as exc:
        with suppress(FileNotFoundError):
            destination.unlink()
        raise BackupExecutionError(
            f"built-in database export failed ({type(exc).__name__})"
        ) from exc
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()


def _dump_mysql(settings: Settings, destination: Path) -> dict:
    url = make_url(settings.backup_database_url or settings.database_url)
    if not url.drivername.startswith(("mysql", "mariadb")):
        raise BackupConfigurationError("BACKUP_INCLUDE_DATABASE currently requires MySQL/MariaDB")
    if not url.database or not url.username:
        raise BackupConfigurationError("DATABASE_URL is missing database name or username")
    if url.database.startswith("-") or any(char in url.database for char in ("\x00", "\n", "\r")):
        raise BackupConfigurationError("DATABASE_URL contains an unsafe database name")

    dump_binary = shutil.which(settings.backup_mysqldump_binary)
    if dump_binary is None:
        logger.warning(
            "[Backup] MySQL client is unavailable; using built-in streaming database export"
        )
        return _dump_mysql_with_pymysql(url, destination)

    def option_value(value: str) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )

    # MySQL recommends an option file over a password on the command line.
    # It lives inside the mode-0700 per-run directory, has mode 0600, is never
    # added to the archive, and is unlinked immediately after mysqldump exits.
    option_file = destination.parent / ".mysql-backup.cnf"
    descriptor = os.open(option_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
        config_file.write("[client]\n")
        config_file.write(f'user="{option_value(url.username)}"\n')
        config_file.write(f'password="{option_value(url.password or "")}"\n')
        config_file.write(f'host="{option_value(url.host or "localhost")}"\n')
        config_file.write(f"port={url.port or 3306}\n")
        config_file.write("protocol=tcp\n")

    command = [
        dump_binary,
        f"--defaults-extra-file={option_file}",
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--events",
        "--hex-blob",
        "--default-character-set=utf8mb4",
        url.database,
    ]
    try:
        with destination.open("xb") as output:
            os.chmod(destination, 0o600)
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
                check=False,
                timeout=settings.backup_command_timeout_seconds,
            )
    except FileNotFoundError:
        # The executable may disappear between shutil.which() and Popen (for
        # example during a container image update). Preserve availability by
        # using the same dependency-free fallback.
        with suppress(FileNotFoundError):
            destination.unlink()
        return _dump_mysql_with_pymysql(url, destination)
    except subprocess.TimeoutExpired as exc:
        raise BackupExecutionError("mysqldump exceeded the configured timeout") from exc
    finally:
        with suppress(FileNotFoundError):
            option_file.unlink()

    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-500:]
        if url.password:
            detail = detail.replace(url.password, "[REDACTED]")
        with suppress(FileNotFoundError):
            destination.unlink()
        raise BackupExecutionError(
            f"mysqldump failed with exit code {completed.returncode}: {detail or 'no diagnostic'}"
        )
    return {
        "included": True,
        "bytes": destination.stat().st_size,
        "method": "mysqldump",
    }


def _snapshot_sqlite(source_path: Path, destination: Path) -> dict:
    if not source_path.is_file():
        return {"included": False, "reason": "not_found"}
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True, timeout=30)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise BackupExecutionError("log-index SQLite snapshot failed integrity_check")
    finally:
        target.close()
        source.close()
    os.chmod(destination, 0o600)
    return {"included": True, "bytes": destination.stat().st_size}


_EXCLUDED_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__"}
_EXCLUDED_FILE_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
}


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = Path(info.name).parts
    basename = parts[-1] if parts else ""
    if any(part in _EXCLUDED_DIR_NAMES for part in parts):
        return None
    if (
        basename in _EXCLUDED_FILE_NAMES
        or basename.startswith(".env.")
        or basename.endswith((".pem", ".key", ".p12", ".pfx"))
    ):
        return None
    # Never follow/store links: a symlink inside an otherwise safe source can
    # point at .env, private keys or arbitrary host files.
    if info.issym() or info.islnk() or info.isdev():
        return None
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def _configured_sources(settings: Settings) -> tuple[list[tuple[Path, str]], list[str]]:
    sources: list[tuple[Path, str]] = []
    missing: list[str] = []
    seen: set[Path] = set()
    for index, raw_value in enumerate(settings.backup_include_paths_list):
        raw_value = str(raw_value).strip()
        if not raw_value:
            continue
        configured = Path(raw_value).expanduser()
        path = configured if configured.is_absolute() else PROJECT_ROOT / configured
        path = path.resolve()
        if path == Path(path.anchor):
            raise BackupConfigurationError("filesystem root cannot be a backup source")
        if path.name == ".env" or path.name.startswith(".env."):
            raise BackupConfigurationError(".env files cannot be backup sources")
        if path in seen:
            continue
        seen.add(path)
        if not path.exists():
            missing.append(raw_value)
            continue
        if configured.is_absolute():
            suffix = hashlib.sha256(str(path).encode()).hexdigest()[:8]
            arcname = f"files/external-{index}-{suffix}-{path.name}"
        else:
            clean = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_value.strip("/\\")) or f"source-{index}"
            arcname = f"files/{clean}"
        sources.append((path, arcname))
    return sources, missing


def _build_encrypted_archive(settings: Settings, work_dir: Path, key: bytes, backup_id: str) -> tuple[Path, dict]:
    staging = work_dir / "staging"
    staging.mkdir(mode=0o700, parents=True)
    manifest: dict = {
        "format": "nexusdata-backup-v1",
        "backup_id": backup_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {"included": False},
        "log_index": {"included": False},
        "sources": [],
        "missing_sources": [],
    }

    if settings.backup_include_database:
        manifest["database"] = _dump_mysql(settings, staging / "database.sql")
    if settings.backup_include_log_index:
        manifest["log_index"] = _snapshot_sqlite(
            LOG_INDEX_PATH,
            staging / "logs-index.sqlite",
        )

    sources, missing = _configured_sources(settings)
    manifest["sources"] = [arcname for _, arcname in sources]
    manifest["missing_sources"] = missing
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)

    archive_path = work_dir / f"{backup_id}.tar.gz.aes"
    with archive_path.open("xb") as encrypted_file:
        os.chmod(archive_path, 0o600)
        writer = _GCMEncryptingWriter(encrypted_file, key)
        try:
            with tarfile.open(fileobj=writer, mode="w|gz", format=tarfile.PAX_FORMAT) as archive:
                archive.add(manifest_path, arcname="manifest.json", recursive=False, filter=_tar_filter)
                database_dump = staging / "database.sql"
                if database_dump.exists():
                    archive.add(database_dump, arcname="database/mysql.sql", recursive=False, filter=_tar_filter)
                log_snapshot = staging / "logs-index.sqlite"
                if log_snapshot.exists():
                    archive.add(log_snapshot, arcname="logs/log-index.sqlite", recursive=False, filter=_tar_filter)
                for source_path, arcname in sources:
                    archive.add(source_path, arcname=arcname, recursive=True, filter=_tar_filter)
        finally:
            writer.finalize()
    return archive_path, manifest


def _mkdir_remote(sftp: paramiko.SFTPClient, path: str) -> None:
    current = "/"
    for part in [part for part in path.split("/") if part]:
        current = posixpath.join(current, part)
        try:
            attributes = sftp.stat(current)
            if not stat.S_ISDIR(attributes.st_mode):
                raise BackupExecutionError("configured remote backup path is not a directory")
        except FileNotFoundError:
            sftp.mkdir(current, mode=0o700)


def _connect_sftp(settings: Settings, normalized: dict) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    client = paramiko.SSHClient()
    client.load_host_keys(str(normalized["known_hosts"]))
    if normalized["host_key_is_pinned"]:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        # Trust on first use: the first successful server key is persisted to
        # the private backup known_hosts file; every later connection rejects
        # a missing/changed key. This keeps simple password-only setup usable
        # while still detecting changes after enrollment.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=normalized["host"],
            port=normalized["port"],
            username=normalized["username"],
            password=normalized["password"],
            key_filename=(
                str(normalized["private_key"])
                if normalized["private_key"] is not None
                else None
            ),
            passphrase=settings.backup_sftp_private_key_passphrase or None,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        return client, client.open_sftp()
    except paramiko.AuthenticationException:
        client.close()
        raise BackupExecutionError(
            "SFTP authentication rejected. Check credentials and server login policy; "
            "BACKUP_SFTP_PASSWORD takes precedence over the SFTP_* password profile."
        ) from None
    except TimeoutError as exc:
        client.close()
        raise BackupExecutionError(
            f"SFTP connection timed out: {normalized['host']}:{normalized['port']} is unreachable"
        ) from exc
    except paramiko.ssh_exception.NoValidConnectionsError as exc:
        client.close()
        raise BackupExecutionError(
            f"SFTP connection failed: cannot reach {normalized['host']}:{normalized['port']}"
        ) from exc
    except Exception:
        client.close()
        raise


def _prune_remote_backups(sftp: paramiko.SFTPClient, remote_dir: str, keep: int) -> list[str]:
    candidates = [
        item
        for item in sftp.listdir_attr(remote_dir)
        if ARCHIVE_RE.fullmatch(item.filename) and stat.S_ISREG(item.st_mode)
    ]
    candidates.sort(key=lambda item: (item.st_mtime, item.filename), reverse=True)
    removed: list[str] = []
    for item in candidates[keep:]:
        sftp.remove(posixpath.join(remote_dir, item.filename))
        removed.append(item.filename)
    return removed


def _upload_archive(settings: Settings, normalized: dict, archive_path: Path) -> dict:
    client, sftp = _connect_sftp(settings, normalized)
    remote_dir = normalized["remote_dir"]
    final_name = archive_path.name
    remote_final = posixpath.join(remote_dir, final_name)
    remote_partial = posixpath.join(remote_dir, f".{final_name}.part-{secrets.token_hex(4)}")
    try:
        _mkdir_remote(sftp, remote_dir)
        sftp.put(str(archive_path), remote_partial, confirm=True)
        sftp.chmod(remote_partial, 0o600)
        remote_size = sftp.stat(remote_partial).st_size
        local_size = archive_path.stat().st_size
        if remote_size != local_size:
            raise BackupExecutionError("uploaded backup size verification failed")
        sftp.rename(remote_partial, remote_final)
        removed = _prune_remote_backups(
            sftp,
            remote_dir,
            settings.backup_retention_count,
        )
        return {
            "remote_file": final_name,
            "encrypted_bytes": local_size,
            "retention_removed": len(removed),
        }
    except Exception:
        with suppress(Exception):
            sftp.remove(remote_partial)
        raise
    finally:
        sftp.close()
        client.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight_backup_connection(settings: Settings, normalized: dict) -> None:
    """Fail before an expensive dump/archive if SFTP authentication is broken.

    Do not create files or prune backups here. Upload reconnects after building
    the archive so an idle SSH session is not held open during a long dump.
    """
    client, sftp = _connect_sftp(settings, normalized)
    try:
        normalized["host_key_is_pinned"] = True
    finally:
        try:
            sftp.close()
        finally:
            client.close()


def test_backup_connection(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    normalized = validate_backup_settings(settings)
    client, sftp = _connect_sftp(settings, normalized)
    try:
        _mkdir_remote(sftp, normalized["remote_dir"])
        sftp.listdir_attr(normalized["remote_dir"])
        return {
            "status": "ok",
            "host_key_pinned": True,
            "host_key_enrolled_now": not normalized["host_key_is_pinned"],
            "auth": "password" if normalized["password"] else "private_key",
            "username": normalized["username"],
        }
    finally:
        sftp.close()
        client.close()


def create_and_upload_backup(
    settings: Settings | None = None,
    *,
    allow_when_disabled: bool = False,
) -> dict:
    """Create one encrypted archive, upload it, then remove every local temp.

    ``allow_when_disabled`` is reserved for the explicit CLI ``run`` command:
    it lets an operator test one backup before enabling the periodic schedule.
    Scheduler calls keep the fail-closed default.
    """

    settings = settings or get_settings()
    if not settings.backup_enabled and not allow_when_disabled:
        return {"status": "disabled"}
    if not _backup_lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "already_running"}

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"nexusdata-{timestamp}-{secrets.token_hex(4)}"
    lock_descriptor: int | None = None
    try:
        # The scheduler is intentionally single-process, but an operator may
        # invoke `python -m services_backup run` at the same time.  A process
        # lock complements the in-process mutex and prevents those runs from
        # dumping/uploading concurrently.
        BACKUP_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_descriptor = os.open(BACKUP_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_descriptor)
            lock_descriptor = None
            return {"status": "skipped", "reason": "already_running"}

        normalized = validate_backup_settings(settings)
        _preflight_backup_connection(settings, normalized)
        temp_parent = Path(settings.backup_temp_dir).expanduser() if settings.backup_temp_dir else None
        if temp_parent is not None:
            temp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="nexus-backup-",
            dir=str(temp_parent) if temp_parent else None,
        ) as temporary:
            work_dir = Path(temporary)
            os.chmod(work_dir, 0o700)
            archive_path, manifest = _build_encrypted_archive(
                settings,
                work_dir,
                normalized["encryption_key"],
                backup_id,
            )
            sha256 = _sha256_file(archive_path)
            upload = _upload_archive(settings, normalized, archive_path)
            result = {
                "status": "ok",
                "backup_id": backup_id,
                "sha256": sha256,
                "missing_sources": manifest["missing_sources"],
                **upload,
            }
            logger.info(
                "[Backup] completed id=%s encrypted_bytes=%s missing_sources=%s retention_removed=%s",
                backup_id,
                upload["encrypted_bytes"],
                len(manifest["missing_sources"]),
                upload["retention_removed"],
            )
            return result
    except (BackupConfigurationError, BackupExecutionError):
        logger.error("[Backup] failed id=%s (safe configuration/execution error)", backup_id)
        raise
    except Exception as exc:
        # Do not interpolate arbitrary third-party exception messages here:
        # authentication libraries may include paths or connection metadata.
        logger.error("[Backup] failed id=%s error_type=%s", backup_id, type(exc).__name__)
        raise BackupExecutionError(f"backup failed ({type(exc).__name__})") from exc
    finally:
        if lock_descriptor is not None:
            with suppress(OSError):
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        _backup_lock.release()


def _safe_extract(archive_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    destination = output_dir.resolve()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            member_path = (destination / member.name).resolve()
            if destination not in member_path.parents and member_path != destination:
                raise BackupExecutionError("unsafe path in backup archive")
            if member.issym() or member.islnk() or member.isdev():
                raise BackupExecutionError("unsafe special file in backup archive")
        archive.extractall(destination, filter="data")


def restore_archive(encrypted_path: Path, output_dir: Path, key_value: str) -> None:
    """Decrypt and safely extract a downloaded archive into a new directory."""

    output_dir = Path(output_dir)
    if output_dir.exists():
        raise BackupExecutionError("restore output directory must not already exist")
    parent = output_dir.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nexus-restore-", dir=str(parent)) as temporary:
        plain_tar = Path(temporary) / "backup.tar.gz"
        decrypt_archive(Path(encrypted_path), plain_tar, key_value)
        _safe_extract(plain_tar, output_dir)


def _main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NexusData encrypted SFTP backup utility")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="verify configuration, host key and SFTP access")
    subparsers.add_parser("run", help="create and upload one backup now")
    restore = subparsers.add_parser("restore", help="decrypt and safely extract a downloaded backup")
    restore.add_argument("archive", type=Path)
    restore.add_argument("output_dir", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "check":
            result = test_backup_connection()
        elif args.command == "run":
            result = create_and_upload_backup(allow_when_disabled=True)
        else:
            settings = get_settings()
            restore_archive(args.archive, args.output_dir, settings.backup_encryption_key)
            result = {"status": "ok", "output_dir": str(args.output_dir)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (BackupConfigurationError, BackupExecutionError) as exc:
        print(
            json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        # Keep CLI output useful without leaking arbitrary third-party error
        # messages which may contain connection metadata.
        print(
            json.dumps(
                {"status": "error", "error": f"backup failed ({type(exc).__name__})"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())

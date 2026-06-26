"""
SFTP File Manager Service for Sub-projects page.
Provides file browsing, upload, download, delete, and edit capabilities.
"""

import os
import io
import stat
import logging
from datetime import datetime
from typing import List, Optional, Dict
from dataclasses import dataclass

import paramiko
from config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class RemoteFile:
    """Represents a file or directory on the remote SFTP server."""
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: Optional[datetime]
    permissions: str


class SFTPFileManager:
    """File manager for browsing and manipulating files on SFTP servers."""

    def __init__(self, server_number: int):
        s = get_settings()
        if server_number == 1:
            self.host = s.sftp_server_1_host
            self.port = s.sftp_server_1_port
            self.username = s.sftp_server_1_username
            self.password = s.sftp_server_1_password
            self.key_path = s.sftp_server_1_key_path
            self.base_dir = s.sftp_server_1_remote_dir
            self.name = s.sftp_server_1_name
        elif server_number == 2:
            self.host = s.sftp_server_2_host
            self.port = s.sftp_server_2_port
            self.username = s.sftp_server_2_username
            self.password = s.sftp_server_2_password
            self.key_path = s.sftp_server_2_key_path
            self.base_dir = s.sftp_server_2_remote_dir
            self.name = s.sftp_server_2_name
        else:
            raise ValueError(f"Invalid server number: {server_number}")
        self.server_number = server_number

    def is_configured(self) -> bool:
        """Check if SFTP is properly configured."""
        return bool(self.host and self.base_dir)

    def _connect(self) -> paramiko.SFTPClient:
        """Establish SFTP connection with timeouts."""
        if not self.host:
            raise RuntimeError(f"SFTP server {self.server_number} not configured")

        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        try:
            sock.connect((self.host, self.port))
        except (socket.timeout, OSError) as e:
            sock.close()
            raise RuntimeError(f"SFTP сервер {self.name} недоступен: {e}")

        transport = paramiko.Transport(sock)
        transport.set_keepalive(30)
        transport.banner_timeout = 15
        transport.auth_timeout = 15

        try:
            if self.key_path and os.path.exists(self.key_path):
                pkey = paramiko.RSAKey.from_private_key_file(self.key_path)
                transport.connect(username=self.username, pkey=pkey)
            elif self.password:
                transport.connect(username=self.username, password=self.password)
            else:
                transport.close()
                raise RuntimeError(f"No credentials for SFTP server {self.server_number}")
        except RuntimeError:
            raise
        except Exception as e:
            transport.close()
            raise RuntimeError(f"SFTP auth error: {e}")

        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.get_channel().settimeout(30)
        sftp._nd_transport = transport  # keep ref so we can close the transport too
        return sftp

    @staticmethod
    def _close(sftp):
        """Close SFTP client AND its underlying transport (prevents fd/thread leak)."""
        if not sftp:
            return
        transport = getattr(sftp, '_nd_transport', None)
        try:
            sftp.close()
        except Exception:
            pass
        if transport:
            try:
                transport.close()
            except Exception:
                pass

    def _safe_path(self, relative_path: str) -> str:
        """Ensure path stays within base_dir (prevent directory traversal)."""
        # Normalize and remove any .. components
        clean = os.path.normpath(relative_path).replace('\\', '/')
        if clean.startswith('/'):
            clean = clean.lstrip('/')
        # Prevent traversal
        parts = clean.split('/')
        safe_parts = [p for p in parts if p and p != '..']
        result = '/'.join(safe_parts)
        return f"{self.base_dir.rstrip('/')}/{result}" if result else self.base_dir.rstrip('/')

    def list_directory(self, relative_path: str = "") -> List[RemoteFile]:
        """List files in a directory."""
        sftp = None
        try:
            sftp = self._connect()
            full_path = self._safe_path(relative_path)
            files = []

            for entry in sftp.listdir_attr(full_path):
                is_dir = stat.S_ISDIR(entry.st_mode) if entry.st_mode else False
                mtime = datetime.fromtimestamp(entry.st_mtime) if entry.st_mtime else None
                perms = stat.filemode(entry.st_mode) if entry.st_mode else '----------'

                rel = f"{relative_path}/{entry.filename}".lstrip('/')
                files.append(RemoteFile(
                    name=entry.filename,
                    path=rel,
                    is_dir=is_dir,
                    size=entry.st_size or 0,
                    mtime=mtime,
                    permissions=perms,
                ))

            # Sort: directories first, then by name
            files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
            return files
        except Exception as e:
            logger.error(f"Failed to list directory on server {self.server_number}: {e}")
            raise RuntimeError(f"Ошибка чтения директории: {e}")
        finally:
            self._close(sftp)

    def read_file(self, relative_path: str) -> str:
        """Read file content as text."""
        sftp = None
        try:
            sftp = self._connect()
            full_path = self._safe_path(relative_path)
            with sftp.open(full_path, 'r') as f:
                content = f.read()
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')
            return content
        except Exception as e:
            logger.error(f"Failed to read file {relative_path} on server {self.server_number}: {e}")
            raise RuntimeError(f"Ошибка чтения файла: {e}")
        finally:
            self._close(sftp)

    def write_file(self, relative_path: str, content: str) -> bool:
        """Write/overwrite file content."""
        sftp = None
        try:
            sftp = self._connect()
            full_path = self._safe_path(relative_path)
            with sftp.open(full_path, 'w') as f:
                f.write(content)
            logger.info(f"Written file {relative_path} on server {self.server_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to write file {relative_path} on server {self.server_number}: {e}")
            raise RuntimeError(f"Ошибка записи файла: {e}")
        finally:
            self._close(sftp)

    def upload_file(self, relative_path: str, file_data: bytes, filename: str) -> bool:
        """Upload a file to the remote server."""
        sftp = None
        try:
            sftp = self._connect()
            dir_path = self._safe_path(relative_path)
            remote_path = f"{dir_path.rstrip('/')}/{filename}"
            with sftp.open(remote_path, 'wb') as f:
                f.write(file_data)
            logger.info(f"Uploaded {filename} to {relative_path} on server {self.server_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload {filename} on server {self.server_number}: {e}")
            raise RuntimeError(f"Ошибка загрузки файла: {e}")
        finally:
            self._close(sftp)

    def download_file(self, relative_path: str) -> Optional[bytes]:
        """Download file content as bytes."""
        sftp = None
        try:
            sftp = self._connect()
            full_path = self._safe_path(relative_path)
            with sftp.open(full_path, 'rb') as f:
                data = f.read()
            return data
        except Exception as e:
            logger.error(f"Failed to download {relative_path} on server {self.server_number}: {e}")
            raise RuntimeError(f"Ошибка скачивания файла: {e}")
        finally:
            self._close(sftp)

    def delete_file(self, relative_path: str) -> bool:
        """Delete a file from the remote server."""
        sftp = None
        try:
            sftp = self._connect()
            full_path = self._safe_path(relative_path)
            sftp.remove(full_path)
            logger.info(f"Deleted {relative_path} on server {self.server_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {relative_path} on server {self.server_number}: {e}")
            raise RuntimeError(f"Ошибка удаления файла: {e}")
        finally:
            self._close(sftp)

    def create_directory(self, relative_path: str) -> bool:
        """Create a directory on the remote server."""
        sftp = None
        try:
            sftp = self._connect()
            full_path = self._safe_path(relative_path)
            sftp.mkdir(full_path)
            logger.info(f"Created directory {relative_path} on server {self.server_number}")
            return True
        except Exception as e:
            logger.error(f"Failed to create directory {relative_path} on server {self.server_number}: {e}")
            raise RuntimeError(f"Ошибка создания директории: {e}")
        finally:
            self._close(sftp)

    def get_file_info(self, relative_path: str) -> Optional[RemoteFile]:
        """Get info about a specific file."""
        sftp = None
        try:
            sftp = self._connect()
            full_path = self._safe_path(relative_path)
            entry = sftp.stat(full_path)
            is_dir = stat.S_ISDIR(entry.st_mode) if entry.st_mode else False
            mtime = datetime.fromtimestamp(entry.st_mtime) if entry.st_mtime else None
            perms = stat.filemode(entry.st_mode) if entry.st_mode else '----------'
            name = relative_path.split('/')[-1] if '/' in relative_path else relative_path
            return RemoteFile(
                name=name,
                path=relative_path,
                is_dir=is_dir,
                size=entry.st_size or 0,
                mtime=mtime,
                permissions=perms,
            )
        except Exception as e:
            logger.error(f"Failed to get file info {relative_path}: {e}")
            return None
        finally:
            self._close(sftp)

import base64
import os
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import paramiko
from cryptography.exceptions import InvalidTag

from config import Settings
import services_backup


def _key_value() -> str:
    return base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")


def _settings(tmp_path: Path, **overrides) -> Settings:
    private_key = tmp_path / "backup.key"
    private_key.write_text("test-only-private-key", encoding="utf-8")
    private_key.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("backup.invalid ssh-ed25519 AAAATEST\n", encoding="utf-8")
    values = {
        "backup_enabled": True,
        "backup_sftp_host": "backup.invalid",
        "backup_sftp_username": "nexusbackup",
        "backup_sftp_private_key_path": str(private_key),
        "backup_sftp_known_hosts_path": str(known_hosts),
        "backup_sftp_remote_dir": "/data",
        "backup_encryption_key": _key_value(),
        "backup_include_database": False,
        "backup_include_log_index": False,
        "backup_include_paths": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_backup_configuration_accepts_compact_password_credentials(tmp_path):
    settings = _settings(
        tmp_path,
        backup_sftp_host="stale-backup.invalid",
        backup_sftp_username="stale-backup-user",
        backup_sftp_private_key_path=str(tmp_path / "missing-stale-key"),
        sftp_ip="https://213.108.170.48",
        sftp_user="root",
        sftp_password="test-password",
        sftp_port=2222,
    )
    normalized = services_backup.validate_backup_settings(settings)

    assert normalized["host"] == "213.108.170.48"
    assert normalized["username"] == "root"
    assert normalized["password"] == "test-password"
    assert normalized["private_key"] is None
    assert normalized["port"] == 2222


def test_backup_configuration_requires_private_key_permissions(tmp_path):
    settings = _settings(tmp_path)
    Path(settings.backup_sftp_private_key_path).chmod(0o644)
    if os.name == "posix":
        with pytest.raises(services_backup.BackupConfigurationError, match="chmod 600"):
            services_backup.validate_backup_settings(settings)
    else:
        services_backup.validate_backup_settings(settings)


def test_sftp_connection_is_host_pinned_and_key_only(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    normalized = services_backup.validate_backup_settings(settings)
    captured = {}

    class FakeTransport:
        def set_keepalive(self, seconds):
            captured["keepalive"] = seconds

    class FakeClient:
        def load_host_keys(self, path):
            captured["known_hosts"] = path

        def set_missing_host_key_policy(self, policy):
            captured["policy"] = policy

        def connect(self, **kwargs):
            captured["connect"] = kwargs

        def get_transport(self):
            return FakeTransport()

        def open_sftp(self):
            return object()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(services_backup.paramiko, "SSHClient", FakeClient)
    client, _sftp = services_backup._connect_sftp(settings, normalized)

    assert isinstance(captured["policy"], paramiko.RejectPolicy)
    assert captured["known_hosts"] == str(normalized["known_hosts"])
    assert captured["connect"]["password"] is None
    assert captured["connect"]["allow_agent"] is False
    assert captured["connect"]["look_for_keys"] is False
    assert captured["connect"]["key_filename"] == str(normalized["private_key"])
    assert captured["keepalive"] == 30
    client.close()


def test_sftp_timeout_has_actionable_safe_error(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    normalized = services_backup.validate_backup_settings(settings)

    class FakeClient:
        def load_host_keys(self, _path):
            pass

        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, **_kwargs):
            raise TimeoutError()

        def close(self):
            pass

    monkeypatch.setattr(services_backup.paramiko, "SSHClient", FakeClient)
    with pytest.raises(
        services_backup.BackupExecutionError,
        match=r"backup\.invalid:22 is unreachable",
    ):
        services_backup._connect_sftp(settings, normalized)


def test_password_connection_enrolls_first_host_key_then_pins_it(tmp_path, monkeypatch):
    known_hosts = tmp_path / "new-known-hosts"
    settings = _settings(
        tmp_path,
        backup_sftp_private_key_path="",
        backup_sftp_password="password-only",
        backup_sftp_known_hosts_path=str(known_hosts),
    )
    normalized = services_backup.validate_backup_settings(settings)
    captured = {}

    class FakeTransport:
        def set_keepalive(self, _seconds):
            pass

    class FakeClient:
        def load_host_keys(self, path):
            captured["known_hosts"] = path

        def set_missing_host_key_policy(self, policy):
            captured["policy"] = policy

        def connect(self, **kwargs):
            captured["connect"] = kwargs

        def get_transport(self):
            return FakeTransport()

        def open_sftp(self):
            return object()

        def close(self):
            pass

    monkeypatch.setattr(services_backup.paramiko, "SSHClient", FakeClient)
    services_backup._connect_sftp(settings, normalized)

    assert known_hosts.exists()
    if os.name == "posix":
        assert known_hosts.stat().st_mode & 0o777 == 0o600
    assert isinstance(captured["policy"], paramiko.AutoAddPolicy)
    assert captured["connect"]["password"] == "password-only"
    assert captured["connect"]["key_filename"] is None
    assert captured["connect"]["allow_agent"] is False
    assert captured["connect"]["look_for_keys"] is False


def test_archive_is_encrypted_authenticated_and_restorable(tmp_path):
    source = tmp_path / "site-logs"
    source.mkdir()
    secret_text = b"private game log: player 76561198000000000"
    (source / "server.log").write_bytes(secret_text)
    settings = _settings(tmp_path, backup_include_paths=str(source))
    key = services_backup.decode_encryption_key(settings.backup_encryption_key)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    archive, _manifest = services_backup._build_encrypted_archive(
        settings,
        work_dir,
        key,
        "nexusdata-20260902T000000Z-12345678",
    )

    encrypted = archive.read_bytes()
    assert encrypted.startswith(services_backup.ARCHIVE_MAGIC)
    assert secret_text not in encrypted

    plain_tar = tmp_path / "restored.tar.gz"
    services_backup.decrypt_archive(archive, plain_tar, key)
    with tarfile.open(plain_tar, "r:gz") as restored:
        member = next(item for item in restored.getmembers() if item.name.endswith("server.log"))
        assert restored.extractfile(member).read() == secret_text

    tampered = tmp_path / "tampered.aes"
    payload = bytearray(encrypted)
    payload[len(services_backup.ARCHIVE_MAGIC) + services_backup.NONCE_SIZE + 5] ^= 1
    tampered.write_bytes(payload)
    with pytest.raises(InvalidTag):
        services_backup.decrypt_archive(tampered, tmp_path / "must-not-exist.tar.gz", key)
    assert not (tmp_path / "must-not-exist.tar.gz").exists()


def test_env_secret_files_and_symlinks_are_not_archived(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "normal.txt").write_text("included", encoding="utf-8")
    (source / ".env").write_text("PASSWORD=must-not-leak", encoding="utf-8")
    outside = tmp_path / "outside.key"
    outside.write_text("private-key", encoding="utf-8")
    os.symlink(outside, source / "linked-secret")
    settings = _settings(tmp_path, backup_include_paths=str(source))

    archive, _ = services_backup._build_encrypted_archive(
        settings,
        tmp_path / "archive-work",
        services_backup.decode_encryption_key(settings.backup_encryption_key),
        "nexusdata-20260902T000000Z-12345678",
    )
    plain_tar = tmp_path / "archive.tar.gz"
    services_backup.decrypt_archive(archive, plain_tar, settings.backup_encryption_key)
    with tarfile.open(plain_tar, "r:gz") as restored:
        names = restored.getnames()
    assert any(name.endswith("normal.txt") for name in names)
    assert not any(name.endswith(".env") for name in names)
    assert not any(name.endswith("linked-secret") for name in names)


def test_mysql_password_is_in_mode_600_option_file_not_command_or_environment(tmp_path, monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stderr = b""

    def fake_run(command, **kwargs):
        captured["command"] = command
        option_argument = command[1]
        option_path = Path(option_argument.split("=", 1)[1])
        captured["option_mode"] = option_path.stat().st_mode & 0o777
        captured["option_contents"] = option_path.read_text(encoding="utf-8")
        captured["password_env"] = kwargs["env"].get("MYSQL_PWD")
        kwargs["stdout"].write(b"-- consistent dump\n")
        return Completed()

    monkeypatch.setattr(services_backup.shutil, "which", lambda _binary: "/usr/bin/mysqldump")
    monkeypatch.setattr(services_backup.subprocess, "run", fake_run)
    settings = _settings(
        tmp_path,
        database_url="mysql+pymysql://backup_user:very-secret@db.invalid:3306/nexus",
    )
    destination = tmp_path / "database.sql"
    result = services_backup._dump_mysql(settings, destination)

    assert result["included"] is True
    if os.name == "posix":
        assert captured["option_mode"] == 0o600
    assert 'password="very-secret"' in captured["option_contents"]
    assert captured["password_env"] is None
    assert all("very-secret" not in part for part in captured["command"])
    assert not (tmp_path / ".mysql-backup.cnf").exists()


def test_builtin_mysql_dump_streams_schema_and_rows_without_system_client(tmp_path, monkeypatch):
    captured = {}

    class FakeCursor:
        def __init__(self, streaming=False):
            self.streaming = streaming
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def execute(self, query):
            if query == "SHOW FULL TABLES":
                self.rows = [("players", "BASE TABLE")]
            elif query == "SHOW CREATE TABLE `players`":
                self.rows = [("players", "CREATE TABLE `players` (`id` INT, `name` TEXT, `label` TEXT GENERATED ALWAYS AS (`name`) VIRTUAL)")]
            elif query == "SHOW COLUMNS FROM `players`":
                self.rows = [
                    ("id", "int", "NO", "", None, ""),
                    ("name", "text", "YES", "", None, ""),
                    ("label", "text", "YES", "", None, "VIRTUAL GENERATED"),
                ]
            elif query == "SELECT `id`,`name` FROM `players`":
                assert self.streaming is True
                self.rows = [(1, "Alice"), (2, "O'Reilly")]
            else:
                self.rows = []
            return len(self.rows)

        def fetchall(self):
            return list(self.rows)

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def __iter__(self):
            return iter(self.rows)

        def close(self):
            pass

    class FakeConnection:
        def cursor(self, cursor_type=None):
            return FakeCursor(streaming=cursor_type is services_backup.pymysql.cursors.SSCursor)

        def escape(self, value):
            if value is None:
                return "NULL"
            if isinstance(value, str):
                return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
            return str(value)

        def rollback(self):
            captured["rolled_back"] = True

        def close(self):
            captured["closed"] = True

    def fake_connect(**kwargs):
        captured["connect"] = kwargs
        return FakeConnection()

    monkeypatch.setattr(services_backup.shutil, "which", lambda _binary: None)
    monkeypatch.setattr(services_backup.pymysql, "connect", fake_connect)
    settings = _settings(
        tmp_path,
        database_url="mysql+pymysql://backup_user:very-secret@db.invalid:3306/nexus",
    )
    destination = tmp_path / "database.sql"

    result = services_backup._dump_mysql(settings, destination)
    dump = destination.read_text(encoding="utf-8")

    assert result == {
        "included": True,
        "bytes": destination.stat().st_size,
        "method": "pymysql",
        "tables": 1,
        "rows": 2,
        "views": 0,
        "skipped_objects": 0,
    }
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600
    assert captured["connect"]["password"] == "very-secret"
    assert captured["rolled_back"] is True
    assert captured["closed"] is True
    assert "very-secret" not in dump
    assert "CREATE TABLE `players`" in dump
    assert "INSERT INTO `players` (`id`,`name`)" in dump
    assert "O\\'Reilly" in dump
    assert "INSERT INTO `players` (`id`,`name`,`label`)" not in dump


def test_create_backup_removes_local_temporary_copy(tmp_path, monkeypatch):
    source = tmp_path / "logs"
    source.mkdir()
    (source / "app.log").write_text("safe only after encryption", encoding="utf-8")
    temp_parent = tmp_path / "temp"
    settings = _settings(
        tmp_path,
        backup_include_paths=str(source),
        backup_temp_dir=str(temp_parent),
    )

    def fake_upload(_settings, _normalized, archive_path):
        assert archive_path.is_file()
        return {
            "remote_file": archive_path.name,
            "encrypted_bytes": archive_path.stat().st_size,
            "retention_removed": 0,
        }

    monkeypatch.setattr(services_backup, "_preflight_backup_connection", Mock())
    monkeypatch.setattr(services_backup, "_upload_archive", fake_upload)
    result = services_backup.create_and_upload_backup(settings)

    assert result["status"] == "ok"
    assert temp_parent.exists()
    assert list(temp_parent.iterdir()) == []


def test_manual_backup_can_run_before_schedule_is_enabled(tmp_path, monkeypatch):
    source = tmp_path / "logs"
    source.mkdir()
    (source / "app.log").write_text("manual preflight", encoding="utf-8")
    settings = _settings(
        tmp_path,
        backup_enabled=False,
        backup_include_paths=str(source),
        backup_temp_dir=str(tmp_path / "temp"),
    )

    monkeypatch.setattr(services_backup, "_preflight_backup_connection", Mock())
    monkeypatch.setattr(
        services_backup,
        "_upload_archive",
        lambda _settings, _normalized, archive_path: {
            "remote_file": archive_path.name,
            "encrypted_bytes": archive_path.stat().st_size,
            "retention_removed": 0,
        },
    )

    assert services_backup.create_and_upload_backup(settings)["status"] == "disabled"
    assert services_backup.create_and_upload_backup(
        settings,
        allow_when_disabled=True,
    )["status"] == "ok"


def test_authentication_failure_is_actionable_and_does_not_expose_password(tmp_path, monkeypatch):
    settings = _settings(tmp_path, backup_sftp_password="test-secret")
    normalized = services_backup.validate_backup_settings(settings)
    client = Mock()
    client.connect.side_effect = paramiko.AuthenticationException("test-secret")
    monkeypatch.setattr(services_backup.paramiko, "SSHClient", lambda: client)

    with pytest.raises(services_backup.BackupExecutionError, match="SFTP authentication rejected") as error:
        services_backup._connect_sftp(settings, normalized)

    assert "test-secret" not in str(error.value)
    assert error.value.__suppress_context__
    client.close.assert_called_once()


def test_preflight_closes_connections_and_pins_followup_connection(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    normalized = services_backup.validate_backup_settings(settings)
    normalized["host_key_is_pinned"] = False
    client, sftp = Mock(), Mock()
    monkeypatch.setattr(services_backup, "_connect_sftp", lambda *_: (client, sftp))

    services_backup._preflight_backup_connection(settings, normalized)

    assert normalized["host_key_is_pinned"] is True
    sftp.close.assert_called_once()
    client.close.assert_called_once()
    sftp.put.assert_not_called()
    sftp.remove.assert_not_called()


def test_preflight_failure_never_builds_archive_and_releases_lock(tmp_path, monkeypatch):
    settings = _settings(tmp_path, backup_temp_dir=str(tmp_path / "temp"))
    monkeypatch.setattr(services_backup, "BACKUP_LOCK_PATH", tmp_path / "backup.lock")
    preflight = Mock(side_effect=services_backup.BackupExecutionError("SFTP authentication rejected"))
    build = Mock()
    upload = Mock()
    monkeypatch.setattr(services_backup, "_preflight_backup_connection", preflight)
    monkeypatch.setattr(services_backup, "_build_encrypted_archive", build)
    monkeypatch.setattr(services_backup, "_upload_archive", upload)

    for _ in range(2):
        with pytest.raises(services_backup.BackupExecutionError, match="authentication"):
            services_backup.create_and_upload_backup(settings)
    assert preflight.call_count == 2
    build.assert_not_called()
    upload.assert_not_called()
    assert not (tmp_path / "temp").exists()


def test_cli_configuration_error_is_json_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        services_backup,
        "test_backup_connection",
        lambda: (_ for _ in ()).throw(
            services_backup.BackupConfigurationError("BACKUP_SFTP_HOST is required")
        ),
    )

    assert services_backup._main(["check"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert '"status": "error"' in captured.err
    assert "BACKUP_SFTP_HOST is required" in captured.err
    assert "Traceback" not in captured.err

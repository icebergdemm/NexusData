"""
Звуки для ивентов: управление .wav/.mp3 на игровых серверах Barotrauma.

Файлы живут на игровых серверах и раздаются по SFTP — оттуда их читает плагин.
Параллельно каждый залитый звук сохраняется ЛОКАЛЬНО в сжатом виде (gzip),
поэтому удаление с удалённого сервера обратимо: запись в БД остаётся с
is_deleted_remotely=True, а по кнопке «Восстановить» файл заливается обратно
из локальной копии.

SFTP-доступ переиспользует уже настроенные игровые серверы (SFTP_SERVER_1/2_*
в .env — те же, что используются для логов и sub-проектов): это буквально те
же машины. Отдельно настраивается только КАТАЛОГ со звуками на каждом сервере
— он и правится в модальном окне настроек (хранится в app_settings, не в .env,
чтобы менять без перезапуска). Пароли в БД не дублируются.
"""
import gzip
import logging
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from models import AppSetting, SoundFile
from services_sftp_filemanager import SFTPFileManager

logger = logging.getLogger(__name__)

# Плагин умеет проигрывать только эти форматы.
ALLOWED_EXTENSIONS = {".wav", ".mp3"}
MAX_SOUND_BYTES = 25 * 1024 * 1024  # 25 МБ на файл

SOUNDS_BACKUP_DIR = os.path.join("uploads", "sounds_backup")

# Ключи в app_settings. Каталог по умолчанию пуст: пока суперпользователь не
# укажет его в настройках, страница честно сообщает, что не настроена, вместо
# того чтобы гадать и лезть в случайное место на игровом сервере.
_DIR_SETTING_KEYS = {1: "sounds_sftp_dir_server1", 2: "sounds_sftp_dir_server2"}

SERVERS: List[Tuple[int, str]] = [(1, "ASPER1"), (2, "ASPER2")]


# ── Настройки ─────────────────────────────────────────────────────────────

def get_setting(db: Session, key: str) -> Optional[str]:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def set_setting(db: Session, key: str, value: Optional[str]) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def get_sounds_dir(db: Session, server_number: int) -> Optional[str]:
    return get_setting(db, _DIR_SETTING_KEYS[server_number])


def set_sounds_dir(db: Session, server_number: int, path: Optional[str]) -> None:
    set_setting(db, _DIR_SETTING_KEYS[server_number], (path or "").strip() or None)


def get_settings_overview(db: Session) -> List[dict]:
    """Состояние настройки по каждому серверу — для модального окна."""
    out = []
    for num, default_name in SERVERS:
        mgr = SFTPFileManager(num)
        out.append({
            "server_number": num,
            "server_name": mgr.name or default_name,
            "sftp_host": mgr.host or None,
            "sftp_configured": mgr.is_configured(),
            "sounds_dir": get_sounds_dir(db, num),
        })
    return out


# ── Вспомогательное ───────────────────────────────────────────────────────

def _ensure_backup_dir() -> str:
    os.makedirs(SOUNDS_BACKUP_DIR, exist_ok=True)
    return SOUNDS_BACKUP_DIR


def sanitize_filename(name: str) -> str:
    """Имя файла, безопасное и для SFTP-пути, и для локального хранилища.

    Отбрасываются каталоги и всё, кроме букв/цифр/пробела/._- : имя приходит
    от пользователя и попадает в путь на удалённом сервере, поэтому «..» или
    разделитель каталогов здесь недопустимы.
    """
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^\w\s.\-()]+", "", base, flags=re.UNICODE).strip()
    return base[:200]


def validate_sound_name(name: str) -> str:
    """Проверяет имя и расширение, возвращает очищенное имя или бросает ValueError."""
    clean = sanitize_filename(name)
    if not clean:
        raise ValueError("Некорректное имя файла")
    ext = os.path.splitext(clean)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Допустимы только файлы .wav и .mp3")
    return clean


def _server_number_by_name(server_name: str) -> int:
    for num, default_name in SERVERS:
        mgr = SFTPFileManager(num)
        if (mgr.name or default_name).upper() == (server_name or "").upper():
            return num
    raise ValueError(f"Неизвестный сервер: {server_name}")


def _manager(db: Session, server_number: int) -> SFTPFileManager:
    """SFTP-менеджер, у которого base_dir подменён на каталог звуков.

    Настройки соединения берутся из уже сконфигурированного игрового сервера,
    а каталог — из app_settings (модальное окно настроек).
    """
    mgr = SFTPFileManager(server_number)
    sounds_dir = get_sounds_dir(db, server_number)
    if not sounds_dir:
        raise ValueError(
            f"Для сервера {mgr.name or server_number} не указан каталог со звуками — "
            f"задайте его в настройках страницы"
        )
    if not mgr.is_configured():
        raise ValueError(f"SFTP для сервера {mgr.name or server_number} не настроен")
    mgr.base_dir = sounds_dir
    return mgr


# ── Локальные резервные копии ─────────────────────────────────────────────

def _backup_path(backup_filename: str) -> str:
    return os.path.join(_ensure_backup_dir(), backup_filename)


def _write_backup(server_name: str, filename: str, data: bytes) -> str:
    """Сохраняет сжатую копию, возвращает имя файла копии."""
    safe_server = re.sub(r"[^\w\-]+", "_", server_name)
    backup_filename = f"{safe_server}__{sanitize_filename(filename)}.gz"
    with gzip.open(_backup_path(backup_filename), "wb", compresslevel=6) as f:
        f.write(data)
    return backup_filename


def read_backup(backup_filename: str) -> Optional[bytes]:
    path = _backup_path(backup_filename)
    if not os.path.isfile(path):
        return None
    with gzip.open(path, "rb") as f:
        return f.read()


def delete_backup(backup_filename: Optional[str]) -> None:
    if not backup_filename:
        return
    try:
        path = _backup_path(backup_filename)
        if os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"Не удалось удалить локальную копию {backup_filename}: {e}")


# ── Операции ──────────────────────────────────────────────────────────────

def list_sounds(db: Session, server_name: str) -> dict:
    """Список звуков сервера: файлы на сервере + удалённые, но восстановимые.

    Источник истины по существующим файлам — сам удалённый сервер (звук могли
    положить туда и мимо панели), а БД добавляет к ним метаданные: кто и когда
    залил, есть ли локальная копия.
    """
    server_number = _server_number_by_name(server_name)
    mgr = _manager(db, server_number)

    remote_files = mgr.list_directory("")
    remote_by_name = {
        f.name: f for f in remote_files
        if not f.is_dir and os.path.splitext(f.name)[1].lower() in ALLOWED_EXTENSIONS
    }

    records = {
        r.filename: r
        for r in db.query(SoundFile).filter(SoundFile.server_name == server_name).all()
    }

    items = []
    for name, rf in sorted(remote_by_name.items()):
        rec = records.get(name)
        items.append({
            "filename": name,
            "size_bytes": rf.size,
            "modified_at": rf.mtime.isoformat() if rf.mtime else None,
            "has_backup": bool(rec and rec.backup_filename),
            "uploaded_by": rec.uploaded_by.discord_username if (rec and rec.uploaded_by) else None,
            "uploaded_at": rec.uploaded_at.isoformat() if (rec and rec.uploaded_at) else None,
            "is_deleted_remotely": False,
        })

    # Удалённые с сервера, но с сохранённой локальной копией
    deleted = []
    for name, rec in sorted(records.items()):
        if name in remote_by_name or not rec.is_deleted_remotely or not rec.backup_filename:
            continue
        deleted.append({
            "filename": name,
            "size_bytes": rec.size_bytes,
            "modified_at": None,
            "has_backup": True,
            "uploaded_by": rec.uploaded_by.discord_username if rec.uploaded_by else None,
            "uploaded_at": rec.uploaded_at.isoformat() if rec.uploaded_at else None,
            "deleted_at": rec.deleted_at.isoformat() if rec.deleted_at else None,
            "deleted_by": rec.deleted_by.discord_username if rec.deleted_by else None,
            "is_deleted_remotely": True,
        })

    return {"server_name": server_name, "files": items, "deleted": deleted}


def upload_sound(db: Session, server_name: str, filename: str, data: bytes, admin_id: int) -> dict:
    """Заливает звук на сервер и сохраняет локальную сжатую копию."""
    clean_name = validate_sound_name(filename)
    if not data:
        raise ValueError("Пустой файл")
    if len(data) > MAX_SOUND_BYTES:
        raise ValueError(f"Файл больше {MAX_SOUND_BYTES // (1024 * 1024)} МБ")

    server_number = _server_number_by_name(server_name)
    mgr = _manager(db, server_number)

    # Сначала удалённый сервер: если заливка не удалась, локальная копия и
    # запись в БД не появятся и не будут вводить в заблуждение.
    mgr.upload_file("", data, clean_name)

    backup_filename = _write_backup(server_name, clean_name, data)

    rec = db.query(SoundFile).filter(
        SoundFile.server_name == server_name, SoundFile.filename == clean_name
    ).first()
    if rec:
        delete_backup(rec.backup_filename if rec.backup_filename != backup_filename else None)
        rec.size_bytes = len(data)
        rec.backup_filename = backup_filename
        rec.uploaded_by_admin_id = admin_id
        rec.uploaded_at = datetime.utcnow()
        rec.is_deleted_remotely = False
        rec.deleted_at = None
        rec.deleted_by_admin_id = None
    else:
        db.add(SoundFile(
            server_name=server_name,
            filename=clean_name,
            size_bytes=len(data),
            backup_filename=backup_filename,
            uploaded_by_admin_id=admin_id,
        ))
    db.commit()
    return {"filename": clean_name, "size_bytes": len(data)}


def rename_sound(db: Session, server_name: str, filename: str, new_name: str) -> dict:
    """Переименовывает звук на сервере; локальная копия следует за именем."""
    old_clean = validate_sound_name(filename)
    new_clean = validate_sound_name(new_name)
    if old_clean == new_clean:
        return {"filename": new_clean}

    server_number = _server_number_by_name(server_name)
    mgr = _manager(db, server_number)
    mgr.rename_file(old_clean, new_clean)

    rec = db.query(SoundFile).filter(
        SoundFile.server_name == server_name, SoundFile.filename == old_clean
    ).first()
    if rec:
        # Копию перезаписываем под новым именем, чтобы имена не разъезжались.
        data = read_backup(rec.backup_filename) if rec.backup_filename else None
        if data is not None:
            delete_backup(rec.backup_filename)
            rec.backup_filename = _write_backup(server_name, new_clean, data)
        rec.filename = new_clean
        db.commit()
    return {"filename": new_clean}


def delete_sound(db: Session, server_name: str, filename: str, admin_id: int) -> dict:
    """Удаляет звук с сервера. Локальная копия НЕ удаляется — по ней звук
    можно восстановить (см. restore_sound)."""
    clean = validate_sound_name(filename)
    server_number = _server_number_by_name(server_name)
    mgr = _manager(db, server_number)
    mgr.delete_file(clean)

    rec = db.query(SoundFile).filter(
        SoundFile.server_name == server_name, SoundFile.filename == clean
    ).first()
    if rec:
        rec.is_deleted_remotely = True
        rec.deleted_at = datetime.utcnow()
        rec.deleted_by_admin_id = admin_id
        db.commit()
        restorable = bool(rec.backup_filename)
    else:
        # Файл лежал на сервере мимо панели — локальной копии нет.
        restorable = False
    return {"filename": clean, "restorable": restorable}


def restore_sound(db: Session, server_name: str, filename: str, admin_id: int) -> dict:
    """Возвращает удалённый звук на сервер из локальной сжатой копии."""
    clean = validate_sound_name(filename)
    rec = db.query(SoundFile).filter(
        SoundFile.server_name == server_name, SoundFile.filename == clean
    ).first()
    if not rec or not rec.backup_filename:
        raise ValueError("Локальная копия этого звука не найдена")
    data = read_backup(rec.backup_filename)
    if data is None:
        raise ValueError("Файл локальной копии отсутствует на диске")

    server_number = _server_number_by_name(server_name)
    mgr = _manager(db, server_number)
    mgr.upload_file("", data, clean)

    rec.is_deleted_remotely = False
    rec.deleted_at = None
    rec.deleted_by_admin_id = None
    rec.uploaded_by_admin_id = admin_id
    rec.uploaded_at = datetime.utcnow()
    db.commit()
    return {"filename": clean, "size_bytes": len(data)}


def purge_backup(db: Session, server_name: str, filename: str) -> None:
    """Окончательно убирает локальную копию удалённого звука."""
    clean = validate_sound_name(filename)
    rec = db.query(SoundFile).filter(
        SoundFile.server_name == server_name, SoundFile.filename == clean
    ).first()
    if not rec:
        return
    delete_backup(rec.backup_filename)
    db.delete(rec)
    db.commit()


def get_sound_bytes(db: Session, server_name: str, filename: str) -> Tuple[bytes, str]:
    """Содержимое звука для прослушивания/скачивания.

    Сначала локальная копия (быстро, не нагружает игровой сервер), при её
    отсутствии — чтение с удалённого сервера.
    """
    clean = validate_sound_name(filename)
    rec = db.query(SoundFile).filter(
        SoundFile.server_name == server_name, SoundFile.filename == clean
    ).first()
    if rec and rec.backup_filename:
        data = read_backup(rec.backup_filename)
        if data is not None:
            return data, clean

    server_number = _server_number_by_name(server_name)
    mgr = _manager(db, server_number)
    data = mgr.download_file(clean)
    if data is None:
        raise ValueError("Файл не найден")
    return data, clean

"""
Ключи входа без Discord (страница /slogin).

Запасной путь входа для случая, когда основной недоступен: Discord лежит,
аккаунт заблокирован, нужен вход с машины без браузерной сессии. Поэтому
ключ намеренно самодостаточен — одной строки достаточно, чтобы войти.

Из этого следуют ограничения, которые здесь и соблюдаются:
  • Ключ выдаёт ТОЛЬКО суперпользователь и только конкретному существующему
    администратору — самостоятельной регистрации по ключу нет.
  • В базе лежит лишь хеш. Показать ключ повторно нельзя даже суперпользователю.
  • Каждая попытка входа считается по IP: 160 бит случайности перебрать нельзя,
    но и превращать эндпоинт в бесплатный генератор нагрузки незачем.
"""
import hashlib
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from models import AdminLoginKey, AdminUser

logger = logging.getLogger(__name__)

KEY_PREFIX = "nd_"
# 32 байта → 64 hex-символа. Плюс префикс, чтобы ключ можно было опознать
# глазами в переписке и заметить, если он куда-то утёк.
_KEY_BYTES = 32

# Ограничение попыток: 10 за 5 минут с одного адреса.
_ATTEMPT_LIMIT = 10
_ATTEMPT_WINDOW = 300
DEFAULT_EXPIRY_DAYS = 7
MAX_EXPIRY_DAYS = 30
_USAGE_TOUCH_INTERVAL = timedelta(minutes=5)
_attempts: dict = {}
_attempts_lock = threading.Lock()


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def generate_key() -> Tuple[str, str, str]:
    """Возвращает (сам ключ, его хеш, префикс для отображения)."""
    raw = KEY_PREFIX + secrets.token_hex(_KEY_BYTES)
    return raw, _hash_key(raw), raw[:11]


def rate_limit_ok(ip: Optional[str]) -> bool:
    """False, если с этого адреса уже слишком много попыток."""
    if not ip:
        return True
    now = time.time()
    with _attempts_lock:
        window = [t for t in _attempts.get(ip, []) if now - t < _ATTEMPT_WINDOW]
        # Заодно подчищаем чужие протухшие записи, чтобы словарь не рос вечно
        # на сервере, который долго не перезапускали.
        if len(_attempts) > 512:
            for key in [k for k, v in _attempts.items() if not any(now - t < _ATTEMPT_WINDOW for t in v)]:
                _attempts.pop(key, None)
        if len(window) >= _ATTEMPT_LIMIT:
            _attempts[ip] = window
            return False
        window.append(now)
        _attempts[ip] = window
    return True


def create_key(db: Session, admin_id: int, created_by_admin_id: Optional[int],
               label: Optional[str] = None, expires_days: Optional[int] = None) -> Tuple[AdminLoginKey, str]:
    """Создаёт ключ. Возвращает (строку БД, сам ключ) — ключ вернуть можно
    только здесь и только один раз."""
    raw, key_hash, prefix = generate_key()
    days = DEFAULT_EXPIRY_DAYS if expires_days is None else expires_days
    if not isinstance(days, int) or days <= 0 or days > MAX_EXPIRY_DAYS:
        raise ValueError(f"expires_days must be between 1 and {MAX_EXPIRY_DAYS}")
    now = datetime.utcnow()
    expires_at = now + timedelta(days=days)

    row = AdminLoginKey(
        admin_id=admin_id,
        key_hash=key_hash,
        key_prefix=prefix,
        label=(label or "").strip() or None,
        created_by_admin_id=created_by_admin_id,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def resolve_admin(db: Session, raw_key: str, client_ip: Optional[str] = None) -> Optional[AdminUser]:
    """Администратор по ключу, либо None.

    Отдельных причин отказа наружу не сообщаем: «ключ отозван» и «ключа нет» —
    разная информация для того, кто перебирает.
    """
    raw_key = (raw_key or "").strip()
    if not raw_key:
        return None

    row = db.query(AdminLoginKey).filter(AdminLoginKey.key_hash == _hash_key(raw_key)).first()
    if not row or row.revoked:
        return None
    if row.expires_at is not None and row.expires_at <= datetime.utcnow():
        return None

    admin = db.query(AdminUser).filter(AdminUser.id == row.admin_id).first()
    if not admin or getattr(admin, "is_deleted", 0) == 1:
        return None

    _touch_usage(row, client_ip, db)
    return admin


def _touch_usage(row: AdminLoginKey, client_ip: Optional[str], db: Session) -> None:
    now = datetime.utcnow()
    if row.last_used_at and now - row.last_used_at < _USAGE_TOUCH_INTERVAL and row.last_used_ip == client_ip:
        return
    row.last_used_at = now
    row.last_used_ip = client_ip
    row.use_count = (row.use_count or 0) + 1
    db.commit()


def resolve_api_login_key(db: Session, raw_key: str, client_ip: Optional[str] = None) -> Optional[Tuple[int, int]]:
    """Resolve an API login key and record bounded usage metadata."""
    raw_key = (raw_key or "").strip()
    if not raw_key:
        return None

    row = db.query(AdminLoginKey).filter(AdminLoginKey.key_hash == _hash_key(raw_key)).first()
    if not row or row.revoked:
        return None
    if row.expires_at is not None and row.expires_at <= datetime.utcnow():
        return None

    admin = db.query(AdminUser).filter(AdminUser.id == row.admin_id).first()
    if not admin or getattr(admin, "is_deleted", 0) == 1:
        return None
    _touch_usage(row, client_ip, db)
    return row.id, admin.id


def serialize(row: AdminLoginKey) -> dict:
    return {
        "id": row.id,
        "key_prefix": row.key_prefix,
        "label": row.label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by.discord_username if row.created_by else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked": bool(row.revoked),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_used_ip": row.last_used_ip,
        "use_count": row.use_count or 0,
    }

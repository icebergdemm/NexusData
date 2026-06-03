from datetime import datetime, timedelta
from typing import Optional
import secrets
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import logging

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# JWT Config
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=ALGORITHM
    )
    return encoded_jwt


def verify_token(token: str) -> dict:
    """Verify JWT token"""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning(f"Token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired"
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )


def verify_api_key(api_key: str) -> bool:
    """Verify API key"""
    # Simple comparison - в production используй хеширование
    return api_key == settings.api_key


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Dependency: verify JWT token from Authorization header"""
    token = credentials.credentials
    payload = verify_token(token)
    user_id: str = payload.get("sub")
    
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    return {"user_id": user_id}


async def verify_api_key_dependency(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> dict:
    """Dependency: verify API key from Authorization header or api_key query"""
    client_ip = None
    if request.client and request.client.host:
        client_ip = request.client.host

    # Блок разрешённых IP без проверки ключа
    if client_ip and client_ip in settings.allowed_ips_list:
        return {"authenticated": True, "client_ip": client_ip}

    api_key = None
    if credentials and credentials.credentials:
        api_key = credentials.credentials
    else:
        api_key = request.query_params.get("api_key")

    if not api_key or not verify_api_key(api_key):
        logger.warning(f"Invalid API key attempted from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )

    return {"authenticated": True, "client_ip": client_ip}


async def verify_external_api_key_dependency(request: Request) -> dict:
    """Simple API key check for external event sources.

    Supports `X-API-Key` header and `api_key` query parameter.
    """
    client_ip = request.client.host if request.client else None

    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if not api_key or not verify_api_key(api_key):
        logger.warning(f"Invalid external API key attempted from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )

    return {"authenticated": True, "client_ip": client_ip, "auth_type": "external_api_key"}


def generate_api_key() -> str:
    """Generate random API key"""
    return secrets.token_urlsafe(32)


# ── Privilege helpers ────────────────────────────────────────────────────────

def check_permission(admin, page: str, action: str = "view") -> bool:
    """Check if admin has a specific page permission.

    Resolution order:
    1. is_superuser → always True
    2. individual_permissions override (if key present)
    3. privilege_group.permissions
    4. legacy flat permissions list fallback
    """
    if admin.is_superuser:
        return True

    # Individual overrides
    ind = admin.individual_permissions or {}
    ind_pages = ind.get("pages", {})
    if page in ind_pages and action in ind_pages[page]:
        return bool(ind_pages[page][action])

    # Group permissions
    grp = getattr(admin, "privilege_group", None)
    if grp and grp.permissions:
        raw = grp.permissions
        # Support both {"pages": {page: {action: bool}}} and flat {page: {action: bool}}
        grp_pages = raw.get("pages", raw)
        if page in grp_pages and action in grp_pages[page]:
            return bool(grp_pages[page][action])

    # No group or permission found
    return False


def require_permission(admin, page: str, action: str = "view"):
    """Raise HTTPException(403) if permission is missing."""
    if not check_permission(admin, page, action):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def check_rate_limit(db, admin, action: str = "general") -> bool:
    """Return True if admin is within rate limit. Increments counter.

    Returns False (over limit) if admin's group has rate_limit_count > 0
    and the admin has exceeded it within rate_limit_window_secs.
    Superusers bypass rate limiting. rate_limit_count == 0 means unlimited.
    """
    if admin.is_superuser:
        return True

    grp = getattr(admin, "privilege_group", None)
    if not grp:
        return True  # no group = no limit

    # rate_limit_count <= 0 means rate limiting is disabled for this group
    max_count = grp.rate_limit_count
    if max_count is None or max_count <= 0:
        return True  # unlimited

    from models import AdminRateLimit

    window_secs = grp.rate_limit_window_secs or 3600
    window_start_cutoff = datetime.utcnow() - timedelta(seconds=window_secs)

    record = (
        db.query(AdminRateLimit)
        .filter(
            AdminRateLimit.admin_id == admin.id,
            AdminRateLimit.action == action,
            AdminRateLimit.window_start >= window_start_cutoff,
        )
        .first()
    )

    if record is None:
        record = AdminRateLimit(admin_id=admin.id, action=action, count=1, window_start=datetime.utcnow())
        db.add(record)
        db.commit()
        return True

    if record.count >= max_count:
        return False  # over limit

    record.count += 1
    db.commit()
    return True


def enforce_rate_limit(db, admin, action: str = "general"):
    """Raise HTTPException(429) if over rate limit."""
    if not check_rate_limit(db, admin, action):
        raise HTTPException(status_code=429, detail="Превышен лимит действий. Попробуйте позже.")


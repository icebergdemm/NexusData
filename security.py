from datetime import datetime, timedelta
from typing import Optional
import hashlib
import secrets
import threading
import time
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import logging

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


def hash_admin_session_token(token: str) -> str:
    """Hash a browser bearer token before it reaches the database."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def get_active_admin_session(db, raw_token: str, request: Optional[Request] = None, touch: bool = True):
    """Find and optionally touch a valid browser session without exposing its token."""
    from models import AdminSession

    if not raw_token:
        return None
    now = datetime.utcnow()
    session = db.query(AdminSession).filter(
        AdminSession.token == hash_admin_session_token(raw_token),
        AdminSession.revoked == 0,
    ).first()
    if not session:
        return None
    last_seen = session.last_seen_at or session.created_at or now
    if session.expires_at and session.expires_at <= now:
        return None
    if now - last_seen > timedelta(seconds=settings.session_idle_timeout):
        session.revoked = 1
        db.commit()
        return None
    if touch and (session.last_seen_at is None or now - last_seen >= SESSION_TOUCH_INTERVAL):
        session.last_seen_at = now
        if request is not None:
            session.last_seen_ip = request.client.host if request.client else None
            session.user_agent = (request.headers.get("user-agent") or "")[:500] or None
        db.commit()
    return session

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
    """Verify API key using constant-time comparison (timing-attack safe)."""
    if not api_key:
        return False
    return secrets.compare_digest(str(api_key), str(settings.api_key))


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
    """Dependency: verify API key from Authorization header, X-API-Key header,
    or api_key query param.

    NOTE: there used to be an `allowed_ips` bypass here (skip the key entirely
    for a hardcoded IP). It was removed: the app now runs behind
    proxy_server.py (and nginx), so `request.client.host` is derived from
    X-Forwarded-For and is attacker-controllable — an IP allowlist there would
    be a trivially spoofable auth bypass. Every legitimate caller (the game
    server plugin) already sends the API key, so key verification is the sole
    gate. `allowed_ips` remains only for reference/logging, never for auth.

    X-API-Key support added alongside Authorization/query — the game server
    plugin sends X-API-Key (same as verify_external_api_key_dependency, used
    by routes/dispatch.py, world_state.py, game_api.py, events.py,
    moderation.py), but this dependency (routes/players.py, kits.py,
    stats.py, status.py) previously only checked Authorization/query, so
    every request from the plugin to those routes was silently rejected with
    401 regardless of how correct the key was.
    """
    client_ip = None
    if request.client and request.client.host:
        client_ip = request.client.host

    api_key = None
    if credentials and credentials.credentials:
        api_key = credentials.credentials
    elif request.headers.get("X-API-Key"):
        api_key = request.headers.get("X-API-Key")
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


async def verify_client_api_key_dependency(request: Request) -> dict:
    """API key check for read-only external "client" integrations (world-
    state export, warehouse export) — a DIFFERENT secret than `api_key`
    (that one's for the game server's own ingest calls), so a read-only
    consumer never needs the ingest credential. See config.py's
    `client_api_key`. Fails closed (401) if that setting is left empty —
    an unconfigured key must never accidentally "match" an empty header.
    """
    client_ip = request.client.host if request.client else None
    configured = settings.client_api_key

    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if not configured or not api_key or not secrets.compare_digest(str(api_key), str(configured)):
        logger.warning(f"Invalid client API key attempted from {client_ip}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    return {"authenticated": True, "client_ip": client_ip, "auth_type": "client_api_key"}


def generate_api_key() -> str:
    """Generate random API key"""
    return secrets.token_urlsafe(32)


# ── Privilege helpers ────────────────────────────────────────────────────────

PERMISSION_POLICY_SETTING = "permission_policy_version"
LEGACY_PERMISSION_POLICY = "legacy_additive"
DENY_PERMISSION_POLICY = "deny_v2"


def get_permission_policy_mode(db) -> str:
    """Return the persisted NexusData permission policy, failing closed to legacy."""
    from models import AppSetting

    row = db.query(AppSetting).filter(AppSetting.key == PERMISSION_POLICY_SETTING).first()
    return row.value if row and row.value in (LEGACY_PERMISSION_POLICY, DENY_PERMISSION_POLICY) else LEGACY_PERMISSION_POLICY


def _permission_value(raw, page: str, action: str):
    if not isinstance(raw, dict):
        return None
    pages = raw.get("pages", raw)
    if not isinstance(pages, dict) or not isinstance(pages.get(page), dict):
        return None
    value = pages[page].get(action)
    return value if isinstance(value, bool) else None


def grant_only_permissions(raw) -> dict:
    """Keep only explicit grants in a non-individual permission matrix.

    Group and department permissions are additive by design: they can open a
    module or site action, but cannot revoke access granted by another source.
    Individual permissions are intentionally not passed through this helper;
    they are the only layer allowed to contain an explicit deny.
    """
    pages = raw.get("pages", raw) if isinstance(raw, dict) else {}
    cleaned = {}
    if not isinstance(pages, dict):
        return {"pages": {}}
    for page, actions in pages.items():
        if not isinstance(actions, dict):
            continue
        grants = {key: True for key, value in actions.items() if value is True}
        if grants:
            cleaned[page] = grants
    return {"pages": cleaned}


def check_permission(admin, page: str, action: str = "view") -> bool:
    """Check if admin has a specific page permission.

    Resolution order (hybrid — individual permissions are the primary source
    of truth, while group and department access are secondary, additive
    layers):
    1. is_superuser → always True
    2. individual_permissions override (if key present), including deny
    3. baseline: every authenticated admin can view Documents and create
       personal documents, and can view the Departments tab (scoped to
       their own memberships — see api_list_departments/api_get_department)
    4. privilege_group.permissions grants only
    5. department site-wide grants (superuser-authored only — see
       compute_department_site_permissions, attached to `admin` by each
       router's get_current_admin as `_department_site_permissions`)
    """
    if admin.is_superuser:
        return True

    # An explicit individual rule is the only non-superuser veto and the
    # highest-priority decision. Group and department rules cannot override it.
    individual_value = _permission_value(getattr(admin, "individual_permissions", None), page, action)
    if individual_value is not None:
        return individual_value

    if page == "documents" and action in ("create_personal", "view"):
        return True

    if page == "departments" and action == "view":
        return True

    # Group permissions are grant-only. Stored false values from the old
    # editor are inert and intentionally do not participate in policy mode.
    grp = getattr(admin, "privilege_group", None)
    if grp and grp.permissions:
        raw = grp.permissions
        # Support both {"pages": {page: {action: bool}}} and flat {page: {action: bool}}
        grp_pages = raw.get("pages", raw)
        if page in grp_pages and action in grp_pages[page] and grp_pages[page][action] is True:
            return True

    # Hybrid tier: additive site-wide grants from a department access level,
    # settable only by a superuser (see routes/departments.py access-level
    # PATCH gate). Never removes access, only adds — so it's checked even when
    # individual/group permissions above exist but don't grant this action,
    # instead of being reached only when neither tier mentions the page/action
    # at all (that used to let "Пользователи ND" silently override/erase
    # department-granted access instead of summing with it).
    hybrid = getattr(admin, "_department_site_permissions", None)
    if hybrid:
        hybrid_pages = hybrid.get("pages", {})
        if page in hybrid_pages and hybrid_pages[page].get(action):
            return True

    # Управлять общим выключателем ИИ-диспетчера могут кураторы и старшие
    # должности даже без отдельной галочки в группе. Для любых остальных
    # пользователей по-прежнему работает обычное индивидуальное/групповое
    # право dispatch:manage_ai, проверенное выше.
    if page == "dispatch" and action == "manage_ai":
        return _has_ai_dispatcher_management_rank(admin)

    return False


def check_moderation_action_permission(admin, action: str) -> bool:
    """Keep the pre-split moderation:edit grant compatible with action keys."""
    return check_permission(admin, "moderation", action) or check_permission(admin, "moderation", "edit")


def csrf_token_from_request(request, form_token: Optional[str] = None) -> Optional[str]:
    """Read the CSRF token used by both JSON headers and legacy forms."""
    return request.headers.get("X-CSRF-Token") or form_token


def _has_ai_dispatcher_management_rank(admin) -> bool:
    """Return True for the project's curator-or-higher panel ranks.

    Privilege groups are database-defined and intentionally have no numeric
    hierarchy, therefore this narrow allow-list mirrors the actual project
    ladder instead of treating every holder of broad ``dispatch:edit`` as a
    curator. Explicit ``dispatch:manage_ai`` grants remain the extensibility
    mechanism for custom groups.
    """

    rank_names = {
        "куратор",
        "кураторы",
        "старший куратор",
        "старшие кураторы",
        "главный куратор",
        "главные кураторы",
        "управляющий",
        "управляющие",
        "администратор",
        "администраторы",
        "главный администратор",
        "главные администраторы",
    }
    group = getattr(admin, "privilege_group", None)
    candidates = (
        getattr(admin, "role", None),
        getattr(group, "name", None) if group is not None else None,
    )
    return any(
        str(candidate).strip().casefold() in rank_names
        for candidate in candidates
        if candidate
    )


_DEPT_SITE_PERMS_CACHE_TTL_SECONDS = 30.0
_dept_site_perms_cache: dict = {}  # admin_id -> (result, monotonic timestamp)
_dept_site_perms_cache_lock = threading.Lock()


def compute_department_site_permissions(db, admin_id: int) -> dict:
    """Union of {"pages": {page: {action: bool}}} site-wide grants from every
    department access level `admin_id` currently holds. Computed once per
    request by each router's get_current_admin and cached on the AdminUser
    instance so check_permission (a pure function, no db access) can consult
    it without threading a db session through ~250 call sites.

    This function itself is called on EVERY admin-panel request (2 queries),
    so its result is additionally cached here for a short TTL keyed by
    admin_id — department membership changes rarely enough that a 30s-stale
    read is a non-issue, and it cuts 2 DB round-trips (+ event-loop time,
    since callers are mostly synchronous inside async def) per request down
    to a dict lookup for repeat requests within the window."""
    now = time.monotonic()
    with _dept_site_perms_cache_lock:
        cached = _dept_site_perms_cache.get(admin_id)
        if cached is not None and (now - cached[1]) < _DEPT_SITE_PERMS_CACHE_TTL_SECONDS:
            return cached[0]

    from models import DepartmentMember, DepartmentAccessLevel

    memberships = db.query(DepartmentMember).filter(DepartmentMember.admin_id == admin_id).all()
    if not memberships:
        result = {}
    else:
        level_keys_by_dept: dict = {}
        for m in memberships:
            level_keys_by_dept.setdefault(m.department_id, set()).update(m.held_level_keys or [])

        dept_ids = list(level_keys_by_dept.keys())
        if not dept_ids:
            result = {}
        else:
            levels = db.query(DepartmentAccessLevel).filter(DepartmentAccessLevel.department_id.in_(dept_ids)).all()
            merged: dict = {}
            for level in levels:
                if level.key not in level_keys_by_dept.get(level.department_id, set()):
                    continue
                for page, actions in (level.site_permissions or {}).get("pages", {}).items():
                    for action, allowed in (actions or {}).items():
                        # Departments can grant a site permission, but they
                        # cannot revoke one.  In particular, false must not
                        # participate in deny_v2's explicit-deny merge.
                        if allowed is not True:
                            continue
                        merged.setdefault(page, {})[action] = True
            result = {"pages": merged} if merged else {}

    with _dept_site_perms_cache_lock:
        _dept_site_perms_cache[admin_id] = (result, now)
    return result


def evict_stale_dept_site_perms() -> int:
    """Drop expired entries from the dept-site-permissions TTL cache. Bounded
    by admin count already, but this keeps it from holding memory for admins
    who've gone idle. Called periodically by workers.task_memory_maintenance.
    Returns the number of entries removed."""
    now = time.monotonic()
    with _dept_site_perms_cache_lock:
        stale = [
            aid for aid, (_result, ts) in _dept_site_perms_cache.items()
            if (now - ts) >= _DEPT_SITE_PERMS_CACHE_TTL_SECONDS
        ]
        for aid in stale:
            _dept_site_perms_cache.pop(aid, None)
    return len(stale)


def check_department_permission(admin, department, member, page: str, action: str = "view") -> bool:
    """Check if admin has a specific module permission within a department.

    `member` is the caller's DepartmentMember row for this department (None
    if they aren't a member at all). `department` must have `access_levels`
    available (SQLAlchemy relationship, loaded lazily or eagerly).

    Resolution order:
    1. is_superuser → always True
    2. not a member → False
    3. is_head → always True within their own department
    4. union of every held access level's {"pages": {module: {action: bool}}}
    """
    if admin.is_superuser:
        return True
    if member is None:
        return False
    held = member.held_level_keys or []
    decisions = []
    for level in department.access_levels:
        if level.key in held:
            pages = (level.permissions or {}).get("pages", {})
            if page in pages and isinstance(pages[page], dict) and isinstance(pages[page].get(action), bool):
                if pages[page][action] is True:
                    # Department module permissions are grant-only.  Stored
                    # false values from the old editor are inert, just like
                    # an absent permission.
                    decisions.append(True)

    if member.is_head:
        return True

    if True in decisions:
        return True
    return False


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
        .order_by(AdminRateLimit.window_start.desc())
        .first()
    )

    if record is None:
        record = AdminRateLimit(admin_id=admin.id, action=action, count=1, window_start=datetime.utcnow())
        db.add(record)
        db.commit()
        return True

    if record.count >= max_count:
        return False  # over limit

    # Atomic increment to avoid lost-update race between concurrent requests
    db.query(AdminRateLimit).filter(
        AdminRateLimit.id == record.id
    ).update({AdminRateLimit.count: AdminRateLimit.count + 1}, synchronize_session=False)
    db.commit()
    return True


def enforce_rate_limit(db, admin, action: str = "general"):
    """Raise HTTPException(429) if over rate limit."""
    if not check_rate_limit(db, admin, action):
        raise HTTPException(status_code=429, detail="Превышен лимит действий. Попробуйте позже.")

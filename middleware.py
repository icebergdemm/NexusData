import re
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
import logging
from collections import defaultdict
from time import time

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# Maximum number of tracked IPs before a full cleanup is forced
_MAX_TRACKED_IPS = 5000

# Пути, которые обязаны отвечать быстро и без лишней логики поверх —
# healthcheck'и (Pterodactyl/мониторинг) и статика/сборка SPA.
_EXEMPT_PREFIXES = ("/healthz", "/static", "/admin/assets", "/docs", "/redoc", "/openapi.json")
LOGIN_KEY_RATE_MULTIPLIER = 4

_AUDIT_EXEMPT_PATHS = (
    "/health", "/healthz", "/static", "/admin/assets", "/docs", "/redoc", "/openapi.json",
)
_AUDIT_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def should_capture_audit_request(request: Request, status_code: int) -> bool:
    """Return whether the fallback audit hook should record this request."""
    path = getattr(getattr(request, "url", None), "path", "") or ""
    if getattr(getattr(request, "state", None), "audit_logged", False):
        return False
    if request.method.upper() not in _AUDIT_MUTATING_METHODS or not 200 <= status_code < 400:
        return False
    if path.startswith(_AUDIT_EXEMPT_PATHS):
        return False
    return path.startswith(("/api/v1/admin/", "/adminold/"))


def audit_fallback_action(request: Request) -> str:
    """Build a stable fallback action without IDs or query strings."""
    path = (getattr(getattr(request, "url", None), "path", "") or "").strip("/")
    if path.startswith("api/v1/"):
        path = path[7:]
    parts = [part for part in path.split("/") if part and not part.isdigit()]
    return ".".join(["http", request.method.lower(), *parts[:2]])


class AuditCaptureMiddleware(BaseHTTPMiddleware):
    """Fallback logger for meaningful admin mutations without explicit events."""

    async def dispatch(self, request: Request, call_next):
        from audit_service import set_current_audit_request, reset_current_audit_request

        context_token = set_current_audit_request(request)
        try:
            response = await call_next(request)
        finally:
            reset_current_audit_request(context_token)
        if should_capture_audit_request(request, response.status_code):
            try:
                from audit_service import record_audit_event
                from database import SessionLocal

                db = SessionLocal()
                try:
                    record_audit_event(
                        db,
                        audit_fallback_action(request),
                        {"route": request.scope.get("path"), "method": request.method},
                        request=request,
                        admin_id=getattr(request.state, "nd_admin_id", None),
                    )
                finally:
                    db.close()
            except Exception:
                logger.exception("Fallback audit capture failed for %s %s", request.method, request.url.path)
        return response


def resolve_api_login_key(raw_key, source_ip=None):
    """Resolve a NexusData login key for middleware identity/rate data."""
    from database import SessionLocal
    from services_login_keys import resolve_api_login_key as _resolve

    db = SessionLocal()
    try:
        return _resolve(db, raw_key, source_ip)
    finally:
        db.close()


def client_ip(request: Request) -> str:
    """Единая точка получения IP клиента для всех security-middleware этого
    файла. request.client.host уже прошёл через uvicorn.run(proxy_headers=True,
    forwarded_allow_ips=...) — доверенный X-Forwarded-For применяется ТАМ
    (граница доверия настраивается переменными окружения, см. api_server.py
    и proxy_server.py), сюда долетает уже итоговое значение."""
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory limiter by IP or validated admin login key."""

    def __init__(self, app, requests_per_window: int, window_seconds: int):
        super().__init__(app)
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.request_history = defaultdict(list)
        self._last_cleanup = time()
        self._cleanup_interval = 300  # Full cleanup every 5 minutes

    def _cleanup_stale_ips(self, now: float):
        """Remove IPs with no recent requests to prevent memory growth."""
        stale_ips = [
            ip for ip, timestamps in self.request_history.items()
            if not timestamps or (now - timestamps[-1]) > self.window_seconds
        ]
        for ip in stale_ips:
            del self.request_history[ip]
        self._last_cleanup = now

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_EXEMPT_PREFIXES) or request.url.path == "/health":
            return await call_next(request)

        if not settings.rate_limit_enabled:
            return await call_next(request)

        login_key_id = None
        login_key_header_present = False
        if request.url.path.startswith("/api/"):
            raw_login_key = request.headers.get("X-ND-Login-Key")
            if raw_login_key is not None:
                login_key_header_present = True
                identity = resolve_api_login_key(raw_login_key)
                if identity:
                    login_key_id, admin_id = identity
                    request.state.nd_auth_type = "login_key"
                    request.state.nd_login_key_id = login_key_id
                    request.state.nd_admin_id = admin_id
                else:
                    request.state.nd_auth_type = "invalid_login_key"

        # Игровой сервер (плагин) шлёт X-API-Key на каждый вызов — это уже
        # авторизованный, доверенный источник с потенциально куда более
        # частым легитимным трафиком (события с десятков одновременных
        # игроков), чем у человека в браузере. Гонять его через тот же
        # публичный лимит означало бы либо душить сам игровой сервер, либо
        # держать лимит настолько высоким, что он перестаёт что-либо ловить
        # у анонимного трафика. Ключ уже сам по себе защита от подбора
        # (secrets.compare_digest, verify_api_key) — здесь просто не дублируем
        # это IP-лимитом.
        import secrets as _secrets
        provided_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if not login_key_header_present and provided_key and (
            (settings.api_key and _secrets.compare_digest(str(provided_key), str(settings.api_key)))
            or (settings.client_api_key and _secrets.compare_digest(str(provided_key), str(settings.client_api_key)))
        ):
            return await call_next(request)

        if login_key_id is not None:
            bucket = f"login-key:{login_key_id}"
            limit = self.requests_per_window * LOGIN_KEY_RATE_MULTIPLIER
        else:
            bucket = f"ip:{client_ip(request)}"
            limit = self.requests_per_window
        now = time()

        # Periodic cleanup of stale IPs to prevent memory leak
        if now - self._last_cleanup > self._cleanup_interval or len(self.request_history) > _MAX_TRACKED_IPS:
            self._cleanup_stale_ips(now)

        self.request_history[bucket] = [
            req_time for req_time in self.request_history[bucket]
            if now - req_time < self.window_seconds
        ]

        if len(self.request_history[bucket]) >= limit:
            logger.warning("Rate limit exceeded for %s", bucket)
            # ВОЗВРАЩАЕМ ответ, а не raise HTTPException. Внутри
            # BaseHTTPMiddleware исключение уже некому превратить в ответ:
            # ExceptionMiddleware, который делает из HTTPException аккуратный
            # JSON, стоит НИЖЕ пользовательских middleware в стеке. Поэтому
            # raise улетал наружу до ServerErrorMiddleware — клиент получал
            # 500 вместо 429 (то есть «сервер сломался» вместо «притормози»),
            # а в лог на КАЖДОЕ превышение лимита валился двойной traceback.
            # anyio.EndOfStream в его верхней половине — внутренняя кухня
            # BaseHTTPMiddleware, а не причина. Лимит по своей природе
            # срабатывает очередями (скан, зациклившийся клиент), так что лог
            # забивался пачками, а форматирование стектрейса и запись в stdout
            # идут прямо в event loop и держат его.
            # InjectionGuardMiddleware ниже в этом же файле всегда делал
            # правильно — возвращал PlainTextResponse.
            oldest = self.request_history[bucket][0]
            retry_after = max(1, int(self.window_seconds - (now - oldest)) + 1)
            return JSONResponse(
                # Форма тела ровно та же, что раньше отдавал бы FastAPI из
                # HTTPException ({"detail": ...}) — фронтенду менять нечего.
                {"detail": "Rate limit exceeded. Too many requests."},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
            )

        self.request_history[bucket].append(now)

        return await call_next(request)


# ══════════════════════════════════════════════════════════════════════════
# INJECTION GUARD — распознаёт классические payload'ы автоматических
# сканеров (command injection, SQLi, path traversal, XSS, log4j-пробы) в
# path+query ДО того, как запрос доберётся до роутинга/БД, и коротко
# блокирует источник. Не претендует на замену настоящего WAF — это
# грубый, но дешёвый фильтр специально под массовое автоматическое
# сканирование вроде GSCAN_CMDI, а не под таргетированную атаку.
#
# Проверяется ТОЛЬКО путь+query string, НИКОГДА тело запроса: в теле
# легитимно живёт свободный текст (заметки, чат, документы), который может
# случайно содержать похожие символы (';', '<script>' как цитата и т.п.) —
# сканить его тем же способом означало бы блокировать реальных
# пользователей за их собственный текст. Путь и query string, наоборот, у
# нас всегда типизированные (ID, флаги, короткие строки без shell-
# синтаксиса) — такие сигнатуры там взяться неоткуда, если это не атака.
# ══════════════════════════════════════════════════════════════════════════

_ATTACK_PATTERNS = [
    # Command injection: разделитель команд + характерная shell-команда сразу
    # после него (сам по себе ';' в строке ни о чём не говорит — свободный
    # текст в query это не запрещает, поэтому требуется КОМБИНАЦИЯ).
    re.compile(r"[;&|]\s*(echo|cat|ls|id|whoami|wget|curl|nc|ncat|bash|sh|python[23]?|perl|ping|nslookup)\b", re.I),
    re.compile(r"`[^`]{1,200}`"),                    # `command substitution`
    re.compile(r"\$\([^)]{1,200}\)"),                 # $(command substitution)
    re.compile(r"\$\{jndi:(ldap|rmi|dns)://", re.I),  # log4j / JNDI-пробы

    # SQL injection: классические маркеры UNION/comment/сон-based.
    re.compile(r"union(\s|%20|\+)+(all\s+)?select", re.I),
    re.compile(r"'\s*or\s*'?\s*1\s*'?\s*=\s*'?\s*1", re.I),
    re.compile(r"\b(sleep|benchmark)\s*\(\s*\d", re.I),
    re.compile(r"\bxp_cmdshell\b", re.I),
    re.compile(r"information_schema\.(tables|columns)", re.I),

    # Path traversal / обращение к системным файлам.
    re.compile(r"(\.\.[/\\]){2,}"),
    re.compile(r"\.\.%2[fF]"),
    re.compile(r"/etc/passwd\b"),
    re.compile(r"\bboot\.ini\b", re.I),

    # Server-side code injection / веб-шеллы.
    re.compile(r"<\?php", re.I),
    re.compile(r"<%[@=]?\s*(page|eval|import)", re.I),

    # XSS-пробы (актуально для путей/query, которые где-то отражаются без
    # экранирования — сам факт присылки такого в URL уже подозрителен).
    re.compile(r"<script[\s>/]", re.I),
    re.compile(r"\bon(error|load)\s*=", re.I),
]

# Сколько времени держим источник заблокированным после срабатывания —
# достаточно, чтобы «сбить» автоматический сканер с этого IP, но не
# бессрочно (динамические IP, общие NAT/VPN-выходы).
_BLOCK_SECONDS = 30 * 60
_MAX_TRACKED_BLOCKED_IPS = 2000


class InjectionGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._blocked_until: dict[str, float] = {}

    def _is_blocked(self, ip: str, now: float) -> bool:
        until = self._blocked_until.get(ip)
        if until is None:
            return False
        if until <= now:
            del self._blocked_until[ip]
            return False
        return True

    def _block(self, ip: str, now: float):
        if len(self._blocked_until) > _MAX_TRACKED_BLOCKED_IPS:
            # Грубая защита от неограниченного роста словаря при массовом
            # сканировании с разных IP разом — чистим протухшие записи.
            self._blocked_until = {k: v for k, v in self._blocked_until.items() if v > now}
        self._blocked_until[ip] = now + _BLOCK_SECONDS

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        ip = client_ip(request)
        now = time()

        # Service integrations may share the Nexus host/NAT address with an
        # unrelated scanner.  A prior malicious anonymous request from that
        # address must not lock out authenticated game ingest or the AI's
        # read-only world client for 30 minutes.  A valid key only bypasses an
        # existing IP block; the current URL is still checked against the
        # injection signatures below.
        import secrets as _secrets
        provided_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        authenticated_service = bool(
            provided_key
            and (
                settings.api_key
                and _secrets.compare_digest(str(provided_key), str(settings.api_key))
                or settings.client_api_key
                and _secrets.compare_digest(str(provided_key), str(settings.client_api_key))
            )
        )
        authorization = request.headers.get("authorization", "")
        authenticated_worker_request = bool(
            request.url.path.startswith("/api/v1/external-worker/")
            and authorization.lower().startswith("bearer ")
            and authorization[7:].strip()
        )

        if self._is_blocked(ip, now) and not authenticated_service and not authenticated_worker_request:
            return PlainTextResponse("Forbidden", status_code=403)

        # path уже url-decoded Starlette'ом; query_string — сырой, декодируем
        # так же, как это делает большинство серверов (attacker обычно шлёт
        # payload percent-encoded именно ради обхода наивных текстовых
        # фильтров). unquote_plus, а не unquote: в query string пробел
        # традиционно кодируется как '+' (form-urlencoded), просто unquote()
        # его не трогает — сигнатура "; echo X #" пришла бы как ";+echo+X+#"
        # и не совпала бы ни с одним паттерном ниже.
        from urllib.parse import unquote_plus
        haystack = request.url.path + " " + unquote_plus(request.url.query or "")

        for pattern in _ATTACK_PATTERNS:
            if pattern.search(haystack):
                logger.warning(
                    f"[injection-guard] Заблокирован подозрительный запрос от {ip}: "
                    f"{request.method} {request.url.path}?{request.url.query} "
                    f"(сигнатура: {pattern.pattern[:60]})"
                )
                self._block(ip, now)
                return PlainTextResponse("Bad Request", status_code=400)

        return await call_next(request)

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timedelta
import logging
from collections import defaultdict
from time import time

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter by IP address"""
    
    def __init__(self, app, requests_per_window: int, window_seconds: int):
        super().__init__(app)
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.request_history = defaultdict(list)
    
    async def dispatch(self, request: Request, call_next):
        # Пропусти проверку для /health
        if request.url.path in ["/health", "/status", "/docs", "/redoc", "/openapi.json"]:
            return await call_next(request)
        
        if not settings.rate_limit_enabled:
            return await call_next(request)
        
        # Получи IP адрес клиента
        client_ip = request.client.host if request.client else "unknown"
        
        # Текущее время
        now = time()
        
        # Очисти старые запросы
        self.request_history[client_ip] = [
            req_time for req_time in self.request_history[client_ip]
            if now - req_time < self.window_seconds
        ]
        
        # Проверь лимит
        if len(self.request_history[client_ip]) >= self.requests_per_window:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Too many requests."
            )
        
        # Добавь текущий запрос
        self.request_history[client_ip].append(now)
        
        return await call_next(request)

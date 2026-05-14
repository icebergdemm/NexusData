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

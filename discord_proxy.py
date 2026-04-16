"""
Discord API Прокси - для обхода блокировок провайдера
Запустите на германском сервере, затем используйте как прокси для Discord API
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
import httpx
import os
from typing import Optional, Dict, Any
import logging
from dotenv import load_dotenv

# Загрузить переменные окружения из .env файла
load_dotenv()

# Конфигурация
app = FastAPI(title="Discord API Proxy", version="1.0")
logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH_URL = "https://discord.com/api/v10/oauth2/token"

# Получите из переменных окружения или используйте напрямую
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "YOUR_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "YOUR_CLIENT_SECRET")


@app.get("/health")
async def health_check():
    """Проверка здоровья"""
    return {"status": "ok", "service": "Discord API Proxy"}


@app.post("/oauth/token")
async def exchange_code(
    code: str = Query(...),
    redirect_uri: str = Query(...)
):
    """
    Обмен кода авторизации на токен доступа
    
    Query параметры:
    - code: Код авторизации от Discord
    - redirect_uri: URI редиректа (должен совпадать с зарегистрированным)
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                DISCORD_OAUTH_URL,
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.json()
                )
            
            return response.json()
    
    except Exception as e:
        logger.error(f"OAuth token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/users/@me")
async def get_current_user(
    access_token: str = Query(...)
):
    """
    Получить информацию о текущем пользователе
    
    Query параметры:
    - access_token: Bearer токен доступа
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to get user info"
                )
            
            return response.json()
    
    except Exception as e:
        logger.error(f"Get user error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/users/@me/guilds")
async def get_user_guilds(
    access_token: str = Query(...)
):
    """
    Получить серверы пользователя (гильдии)
    
    Query параметры:
    - access_token: Bearer токен доступа
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to get guilds"
                )
            
            return response.json()
    
    except Exception as e:
        logger.error(f"Get guilds error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/users/{user_id}")
async def get_user(
    user_id: str,
    access_token: str = Query(...)
):
    """
    Получить информацию о любом пользователе по ID
    
    Path параметры:
    - user_id: ID пользователя Discord
    
    Query параметры:
    - access_token: Bearer токен доступа
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/users/{user_id}",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to get user"
                )
            
            return response.json()
    
    except Exception as e:
        logger.error(f"Get user error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/guilds/{guild_id}/members/{user_id}")
async def get_guild_member(
    guild_id: str,
    user_id: str,
    access_token: str = Query(...)
):
    """
    Получить информацию о члене сервера
    
    Path параметры:
    - guild_id: ID сервера
    - user_id: ID пользователя
    
    Query параметры:
    - access_token: Bearer токен доступа
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{user_id}",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to get guild member"
                )
            
            return response.json()
    
    except Exception as e:
        logger.error(f"Get guild member error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/oauth/token/refresh")
async def refresh_token(
    refresh_token: str = Query(...)
):
    """
    Обновить токен доступа по refresh токену
    
    Query параметры:
    - refresh_token: Refresh токен
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                DISCORD_OAUTH_URL,
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to refresh token"
                )
            
            return response.json()
    
    except Exception as e:
        logger.error(f"Refresh token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    
    # Запуск на всех интерфейсах, порт 8001
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info"
    )

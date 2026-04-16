#!/usr/bin/env python3
"""
Discord Proxy Примеры использования
Напроверяйте, что прокси работает перед запуском
"""

import httpx
import asyncio


class DiscordProxyClient:
    """Клиент для работы с Discord Proxy"""
    
    def __init__(self, proxy_url: str):
        """
        Инициализация клиента
        
        Args:
            proxy_url: URL прокси-сервера (например, http://192.168.1.100:8001)
        """
        self.proxy_url = proxy_url.rstrip('/')
    
    async def health_check(self) -> bool:
        """Проверить доступность прокси"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.proxy_url}/health")
                return response.status_code == 200
        except Exception as e:
            print(f"Health check failed: {e}")
            return False
    
    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """
        Обменять код авторизации на токен
        
        Args:
            code: Код авторизации от Discord
            redirect_uri: URI редиректа
        
        Returns:
            Словарь с access_token и другой информацией
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.proxy_url}/oauth/token",
                    params={
                        "code": code,
                        "redirect_uri": redirect_uri,
                    }
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Exchange code failed: {e}")
            return {}
    
    async def get_user(self, access_token: str) -> dict:
        """
        Получить информацию о пользователе
        
        Args:
            access_token: Токен доступа
        
        Returns:
            Словарь с информацией о пользователе (id, username, avatar и т.д.)
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.proxy_url}/users/@me",
                    params={"access_token": access_token}
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Get user failed: {e}")
            return {}
    
    async def refresh_token(self, refresh_token: str) -> dict:
        """
        Обновить токен доступа
        
        Args:
            refresh_token: Refresh токен
        
        Returns:
            Новый access_token
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.proxy_url}/oauth/token/refresh",
                    params={"refresh_token": refresh_token}
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Refresh token failed: {e}")
            return {}


async def demo():
    """Демонстрация использования"""
    
    # Замените на URL вашего прокси-сервера
    PROXY_URL = "http://192.168.1.100:8001"  # Измените на реальный IP/домен
    
    client = DiscordProxyClient(PROXY_URL)
    
    # 1. Проверить доступность прокси
    print("1. Проверка доступности прокси...")
    is_alive = await client.health_check()
    print(f"   Статус: {'✅ OK' if is_alive else '❌ FAILED'}")
    
    if not is_alive:
        print("   ❌ Прокси недоступен!")
        return
    
    # 2. Пример обмена кода (обычно происходит после OAuth редиректа)
    print("\n2. Обмен кода на токен...")
    # auth_code = "получить из Discord OAuth"
    # token_data = await client.exchange_code(auth_code, "http://your-site.com/callback")
    # print(f"   Access Token: {token_data.get('access_token', 'FAILED')}")
    
    # 3. Пример получения информации о пользователе
    print("\n3. Получение информации о пользователе...")
    # access_token = token_data.get('access_token')
    # user_info = await client.get_user(access_token)
    # print(f"   Username: {user_info.get('username')}")
    # print(f"   User ID: {user_info.get('id')}")
    # print(f"   Avatar: {user_info.get('avatar')}")
    
    print("\n✅ Демонстрация завершена!")
    print("\nДля полного тестирования используйте реальный auth_code и access_token")


if __name__ == "__main__":
    # # Запуск демонстрации
    # asyncio.run(demo())
    
    # Или используйте в своем приложении:
    # client = DiscordProxyClient("http://german-server:8001")
    # token_data = asyncio.run(client.exchange_code(code, redirect_uri))
    # user_info = asyncio.run(client.get_user(token_data['access_token']))
    
    print("Discord Proxy Client готов к использованию!")

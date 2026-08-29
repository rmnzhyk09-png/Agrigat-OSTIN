"""HTTP утилиты с retry."""
import asyncio
import random
from typing import Optional

import httpx


async def get_with_retry(
    url: str,
    max_retries: int = 3,
    timeout: float = 20.0,
    headers: Optional[dict] = None,
) -> Optional[httpx.Response]:
    """
    GET с retry и экспоненциальным backoff.
    
    Args:
        url: URL
        max_retries: Максимум попыток
        timeout: Таймаут
        headers: Заголовки
        
    Returns:
        Response или None
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries):
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:
                    # Rate limit - подождём
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(wait_time)
                elif response.status_code >= 500:
                    # Server error - подождём
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(wait_time)
                else:
                    return None
            except httpx.RequestError:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(wait_time)
                else:
                    return None
        return None

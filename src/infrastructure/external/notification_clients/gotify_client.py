"""
Gotify 通知客户端
"""
from typing import Dict, Optional

import httpx

from .base import NotificationClient


class GotifyClient(NotificationClient):
    """Gotify 通知客户端"""

    channel_key = "gotify"
    display_name = "Gotify"

    def __init__(
        self,
        gotify_url: Optional[str] = None,
        gotify_token: Optional[str] = None,
        pcurl_to_mobile: bool = True,
    ):
        super().__init__(
            enabled=bool(gotify_url and gotify_token),
            pcurl_to_mobile=pcurl_to_mobile,
        )
        self.gotify_url = (gotify_url or "").rstrip("/")
        self.gotify_token = gotify_token

    async def send(self, product_data: Dict, reason: str) -> None:
        if not self.is_enabled():
            raise RuntimeError("Gotify 未启用")

        message = self._build_message(product_data, reason)
        payload = {
            "title": message.notification_title,
            "message": message.content,
            "priority": 5,
        }
        final_url = f"{self.gotify_url}/message"
        headers = {"X-Gotify-Key": self.gotify_token}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                final_url, json=payload, headers=headers,
            )
            response.raise_for_status()

"""
Ntfy 通知客户端
"""
from typing import Dict, Optional

import httpx

from .base import NotificationClient


class NtfyClient(NotificationClient):
    """Ntfy 通知客户端"""

    channel_key = "ntfy"
    display_name = "Ntfy"

    def __init__(self, topic_url: Optional[str] = None, pcurl_to_mobile: bool = True):
        super().__init__(enabled=bool(topic_url), pcurl_to_mobile=pcurl_to_mobile)
        self.topic_url = topic_url

    async def send(self, product_data: Dict, reason: str) -> None:
        """发送 Ntfy 通知"""
        if not self.is_enabled():
            raise RuntimeError("Ntfy 未启用")

        message = self._build_message(product_data, reason)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                self.topic_url,
                content=message.content.encode('utf-8'),
                headers={
                    "Title": message.notification_title,
                    "Priority": "urgent",
                    "Tags": "bell,vibration",
                },
            )
            response.raise_for_status()

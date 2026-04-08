"""
Telegram 通知客户端
"""
import html
from typing import Dict, Optional

import httpx

from src.infrastructure.config.settings import DEFAULT_TELEGRAM_API_BASE_URL

from .base import NotificationClient


class TelegramClient(NotificationClient):
    """Telegram 通知客户端"""

    channel_key = "telegram"
    display_name = "Telegram"

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        api_base_url: str = DEFAULT_TELEGRAM_API_BASE_URL,
        pcurl_to_mobile: bool = True,
    ):
        super().__init__(enabled=bool(bot_token and chat_id), pcurl_to_mobile=pcurl_to_mobile)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base_url = (
            (api_base_url or DEFAULT_TELEGRAM_API_BASE_URL).rstrip("/")
        )

    async def send(self, product_data: Dict, reason: str) -> None:
        """发送 Telegram 通知"""
        if not self.is_enabled():
            raise RuntimeError("Telegram 未启用")

        message = self._build_message(product_data, reason)
        safe_title = html.escape(message.title[:50])
        ellipsis = "..." if len(message.title) > 50 else ""
        telegram_message = [
            "🚨 <b>新推荐!</b>",
            "",
            f"<b>{safe_title}{ellipsis}</b>",
            "",
            f"💰 价格: {html.escape(message.price)}",
            f"📝 原因: {html.escape(message.reason)}",
        ]
        if message.mobile_link:
            safe_mobile = html.escape(message.mobile_link)
            telegram_message.append(f'📱 <a href="{safe_mobile}">手机端链接</a>')
        safe_desktop = html.escape(message.desktop_link)
        telegram_message.append(f'💻 <a href="{safe_desktop}">电脑端链接</a>')

        # bot_token appears in URL path per Telegram Bot API requirement
        telegram_api_url = f"{self.api_base_url}/bot{self.bot_token}/sendMessage"
        telegram_payload = {
            "chat_id": self.chat_id,
            "text": "\n".join(telegram_message),
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }

        headers = {"Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                telegram_api_url,
                json=telegram_payload,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                raise RuntimeError(result.get("description", "Telegram 返回未知错误"))

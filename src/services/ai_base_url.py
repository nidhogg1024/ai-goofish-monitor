"""AI Base URL 标准化工具。"""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_openai_base_url(value: str | None) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""

    path = (urlparse(raw).path or "").rstrip("/")
    if path.endswith("/v1"):
        return raw
    if path.endswith("/api"):
        return f"{raw}/v1"
    return raw

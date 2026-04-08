"""
URL 内容抓取服务

支持抓取网页正文 + 评论内容。
已适配平台优先走 API 抓取评论，其他平台 fallback 到 Playwright 渲染。
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}


_TIMEOUT = 20.0
_MAX_ARTICLE_LENGTH = 10000
_MAX_COMMENT_LENGTH = 6000

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"}


# ---------------------------------------------------------------------------
#  主入口
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"不支持的 URL scheme: {parsed.scheme}")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL 缺少主机名")
    _PRIVATE_PATTERNS = ("localhost", "127.", "10.", "192.168.", "172.16.", "169.254.", "0.0.0.0", "[::1]")
    if any(hostname.startswith(p) or hostname == p.rstrip(".") for p in _PRIVATE_PATTERNS):
        raise ValueError("不允许访问内部网络地址")


async def fetch_url_content(url: str) -> str:
    """抓取 URL，返回正文 + 评论的完整文本。"""
    _validate_url(url)
    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers=_HEADERS,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    # 提取正文
    article_text = _extract_article(html)

    # 提取评论（根据平台分发）
    comment_text = await _fetch_comments(url, html)

    parts = [article_text]
    if comment_text:
        parts.append(f"\n\n===== 评论区（精选） =====\n\n{comment_text}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
#  正文提取
# ---------------------------------------------------------------------------

def _extract_article(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > _MAX_ARTICLE_LENGTH:
        text = text[:_MAX_ARTICLE_LENGTH] + "\n...(正文已截断)"
    return text


# ---------------------------------------------------------------------------
#  评论抓取（平台分发）
# ---------------------------------------------------------------------------

async def _fetch_comments(url: str, html: str) -> str:
    """根据 URL 域名分发到对应的评论抓取器。"""
    host = urlparse(url).hostname or ""

    if "bilibili.com" in host or "b23.tv" in host:
        return await _fetch_bilibili_comments(html)

    # 未适配平台：尝试 Playwright fallback
    return await _playwright_fetch_comments(url)


# ---------------------------------------------------------------------------
#  B 站评论 API
# ---------------------------------------------------------------------------

async def _fetch_bilibili_comments(html: str) -> str:
    """从 B 站页面 HTML 中提取评论参数，调用评论 API 获取热门评论 + 子评论。"""
    m = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;", html)
    if not m:
        return ""

    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return ""

    basic = _deep_get(state, "detail", "basic") or {}
    oid = basic.get("comment_id_str") or ""
    comment_type = basic.get("comment_type")

    if not oid or comment_type is None:
        return ""

    bili_headers = {**_HEADERS, "Referer": "https://www.bilibili.com"}

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=bili_headers) as client:
        resp = await client.get(
            "https://api.bilibili.com/x/v2/reply",
            params={"type": comment_type, "oid": oid, "sort": 1, "ps": 20, "nohot": 0},
        )
        data = resp.json()

        if data.get("code") != 0:
            return ""

        replies = data.get("data", {}).get("replies") or []
        total = data.get("data", {}).get("page", {}).get("count", 0)

        lines: list[str] = []
        lines.append(f"共 {total} 条评论，以下为热门精选：\n")

        for reply in replies:
            uname = reply.get("member", {}).get("uname", "匿名")
            message = _clean_bilibili_text(reply.get("content", {}).get("message", ""))
            likes = reply.get("like", 0)
            if not message:
                continue

            lines.append(f"[{likes}赞] {uname}: {message}")

            # 抓取子评论：如果主评论有回复，调用子评论 API
            rcount = reply.get("rcount", 0)
            rpid = reply.get("rpid")
            if rcount > 0 and rpid:
                try:
                    sub_resp = await client.get(
                        "https://api.bilibili.com/x/v2/reply/reply",
                        params={"oid": oid, "type": comment_type, "root": rpid, "ps": 10, "pn": 1},
                    )
                    sub_data = sub_resp.json()
                    sub_replies = sub_data.get("data", {}).get("replies") or []
                    for sub in sub_replies:
                        sub_uname = sub.get("member", {}).get("uname", "匿名")
                        sub_msg = _clean_bilibili_text(sub.get("content", {}).get("message", ""))
                        sub_likes = sub.get("like", 0)
                        if sub_msg:
                            lines.append(f"  └ [{sub_likes}赞] {sub_uname}: {sub_msg}")
                except Exception as exc:
                    logger.warning("获取 B 站子评论失败 (rpid=%s): %s", rpid, exc)

    result = "\n".join(lines)
    if len(result) > _MAX_COMMENT_LENGTH:
        result = result[:_MAX_COMMENT_LENGTH] + "\n...(评论已截断)"
    return result


def _clean_bilibili_text(text: str) -> str:
    """清理 B 站文本：去除表情标记、@回复前缀。"""
    text = text.strip()
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"^回复\s*@.*?:", "", text).strip()
    return text


# ---------------------------------------------------------------------------
#  Playwright fallback
# ---------------------------------------------------------------------------

_PLAYWRIGHT_TOTAL_TIMEOUT = 30.0


async def _playwright_fetch_comments(url: str) -> str:
    """使用 Playwright 渲染页面并滚动加载评论区。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ""

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            context.set_default_timeout(_PLAYWRIGHT_TOTAL_TIMEOUT * 1000)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)

            # 滚动几次以触发评论懒加载
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(800)

            # 尝试常见的评论区选择器
            comment_selectors = [
                "[class*='comment']",
                "[class*='Comment']",
                "[class*='reply']",
                "[id*='comment']",
            ]

            comments: list[str] = []
            for selector in comment_selectors:
                elements = await page.query_selector_all(selector)
                for el in elements:
                    text = (await el.inner_text() or "").strip()
                    if text and len(text) > 5 and text not in comments:
                        comments.append(text)
                if comments:
                    break

            await browser.close()

            if not comments:
                return ""

            result = "\n\n".join(comments[:30])
            if len(result) > _MAX_COMMENT_LENGTH:
                result = result[:_MAX_COMMENT_LENGTH] + "\n...(评论已截断)"
            return result

    except Exception as exc:
        logger.warning("Playwright 评论抓取失败 (%s): %s", url, exc)
        return ""


# ---------------------------------------------------------------------------
#  工具函数
# ---------------------------------------------------------------------------

def _deep_get(d: dict, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d

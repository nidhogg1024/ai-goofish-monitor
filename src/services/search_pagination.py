import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.utils import log_time, random_sleep

logger = logging.getLogger(__name__)

SEARCH_RESULTS_API_FRAGMENT = "/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/"

NEXT_PAGE_SELECTORS = [
    "[class*='search-pagination-arrow-right']:not([class*='disabled'])",
    ".ant-pagination-next:not(.ant-pagination-disabled)",
    "li.ant-pagination-next > button",
    "button[aria-label='next']",
    "button[aria-label='Next']",
]

PAGE_REQUEST_TIMEOUT_SEC = 20
PAGE_RETRY_DELAY_SECONDS = 5
PAGE_RETRY_COUNT = 2
PAGE_CLICK_SLEEP_MIN = 2.0
PAGE_CLICK_SLEEP_MAX = 5.0


@dataclass(frozen=True)
class PageAdvanceResult:
    advanced: bool
    response: Optional[Any] = None
    stop_reason: Optional[str] = None


def is_search_results_response(
    response: Any,
    api_url_fragment: str = SEARCH_RESULTS_API_FRAGMENT,
) -> bool:
    request = getattr(response, "request", None)
    request_method = getattr(request, "method", None)
    response_url = getattr(response, "url", "")
    return api_url_fragment in response_url and request_method == "POST"


async def _find_next_button(page: Any, logger: Callable[[str], None] = log_time) -> Optional[Any]:
    for selector in NEXT_PAGE_SELECTORS:
        locator = page.locator(selector)
        if await locator.count() > 0:
            logger(f"[翻页] 匹配到下一页按钮: {selector}")
            return locator.first
    return None


async def _wait_for_search_response(
    page: Any,
    timeout_sec: float,
) -> Optional[Any]:
    """监听页面响应事件，捕获搜索 API 的返回（兼容 AJAX 和全页导航）。"""
    loop = asyncio.get_running_loop()
    captured: asyncio.Future = loop.create_future()

    def on_response(response: Any) -> None:
        try:
            if not captured.done() and is_search_results_response(response):
                captured.set_result(response)
        except Exception as exc:
            logger.warning("on_response 回调异常: %s", exc)

    page.on("response", on_response)
    try:
        return await asyncio.wait_for(captured, timeout=timeout_sec)
    except asyncio.TimeoutError:
        return None
    finally:
        page.remove_listener("response", on_response)


async def advance_search_page(
    *,
    page: Any,
    page_num: int,
    logger: Callable[[str], None] = log_time,
    wait_after_click: Callable[[float, float], Awaitable[None]] = random_sleep,
    retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_retries: int = PAGE_RETRY_COUNT,
) -> PageAdvanceResult:
    """点击"下一页"按钮并等待搜索 API 返回新数据。"""

    next_button = await _find_next_button(page, logger)
    if not next_button:
        logger("已到达最后一页，未找到可用的'下一页'按钮，停止翻页。")
        return PageAdvanceResult(advanced=False, stop_reason="no_next_button")

    for retry_index in range(max_retries):
        response_task = asyncio.create_task(
            _wait_for_search_response(page, PAGE_REQUEST_TIMEOUT_SEC)
        )

        try:
            await next_button.scroll_into_view_if_needed()
            await next_button.click()
            await wait_after_click(PAGE_CLICK_SLEEP_MIN, PAGE_CLICK_SLEEP_MAX)

            response = await response_task

            if response and response.ok:
                logger(f"成功翻到第 {page_num} 页。")
                return PageAdvanceResult(advanced=True, response=response)

            logger(f"翻页到第 {page_num} 页后未收到有效的搜索 API 响应。")

        except Exception as exc:
            if not response_task.done():
                response_task.cancel()
            logger(f"翻页到第 {page_num} 页出错: {exc}")

        if retry_index < max_retries - 1:
            logger(f"翻页到第 {page_num} 页超时，{PAGE_RETRY_DELAY_SECONDS}秒后重试...")
            await retry_sleep(PAGE_RETRY_DELAY_SECONDS)
            next_button = await _find_next_button(page, logger)
            if not next_button:
                logger("重试时未找到'下一页'按钮，停止翻页。")
                return PageAdvanceResult(advanced=False, stop_reason="no_next_button")
            continue

        logger(f"翻页到第 {page_num} 页超时 {max_retries} 次，停止翻页。")
        return PageAdvanceResult(advanced=False, stop_reason="timeout")

    return PageAdvanceResult(advanced=False, stop_reason="unknown")

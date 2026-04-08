import asyncio

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.services.search_pagination import advance_search_page
from src.services.search_pagination import is_search_results_response


class FakeRequest:
    def __init__(self, method: str = "POST"):
        self.method = method


class FakeResponse:
    def __init__(self, url: str, ok: bool = True, method: str = "POST"):
        self.url = url
        self.ok = ok
        self.request = FakeRequest(method)


class FakeButton:
    def __init__(
        self,
        *,
        disabled: bool = False,
        markup: str = "",
        click_error: Exception | None = None,
    ):
        self._disabled = disabled
        self._markup = markup
        self.clicks = 0
        self.scrolls = 0
        self.click_timeout = None
        self._click_error = click_error

    async def is_disabled(self) -> bool:
        return self._disabled

    async def evaluate(self, _script: str) -> str:
        return self._markup

    async def scroll_into_view_if_needed(self) -> None:
        self.scrolls += 1

    async def click(self, timeout: int | None = None) -> None:
        self.clicks += 1
        self.click_timeout = timeout
        if self._click_error is not None:
            raise self._click_error


class FakeLocator:
    def __init__(self, buttons: list[FakeButton]):
        self._buttons = buttons

    @property
    def first(self):
        return self._buttons[0] if self._buttons else None

    async def count(self) -> int:
        return len(self._buttons)

    def nth(self, index: int) -> FakeButton:
        return self._buttons[index]


class FakePage:
    def __init__(
        self,
        next_button_count: int,
        outcomes: list[object],
        click_error: Exception | None = None,
    ):
        if next_button_count > 1:
            next_btn = FakeButton(
                disabled=False,
                markup='<div class="search-pagination-arrow-right"></div>',
                click_error=click_error,
            )
            self._next_locator = FakeLocator([next_btn])
        else:
            self._next_locator = FakeLocator([])
        self._outcomes = list(outcomes)
        self._listeners: dict[str, list] = {}

    @property
    def locator_stub(self) -> FakeLocator:
        return self._next_locator

    def locator(self, _selector: str) -> FakeLocator:
        return self._next_locator

    def on(self, event: str, callback) -> None:
        self._listeners.setdefault(event, []).append(callback)
        if event == "response" and self._outcomes:
            outcome = self._outcomes.pop(0)
            if not isinstance(outcome, Exception):
                callback(outcome)

    def remove_listener(self, event: str, callback) -> None:
        listeners = self._listeners.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    def expect_response(self, _predicate, timeout: int):
        assert timeout == 20000
        if not self._outcomes:
            raise AssertionError("missing fake response outcome")
        return FakeResponseContext(self._outcomes.pop(0))


async def _noop_random_sleep(_min_seconds: float, _max_seconds: float) -> None:
    return None


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_advance_search_page_stops_when_no_next_button() -> None:
    page = FakePage(next_button_count=0, outcomes=[])
    logs: list[str] = []

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=logs.append,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is False
    assert result.response is None
    assert result.stop_reason == "no_next_button"
    assert logs == ["已到达最后一页，未找到可用的'下一页'按钮，停止翻页。"]


def test_advance_search_page_stops_after_timeout_retries() -> None:
    page = FakePage(
        next_button_count=2,
        outcomes=[
            PlaywrightTimeoutError("page 2 timeout"),
            PlaywrightTimeoutError("page 2 timeout"),
        ],
    )
    logs: list[str] = []

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=logs.append,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is False
    assert result.response is None
    assert result.stop_reason == "timeout"
    button = page.locator_stub.nth(0)
    assert button.clicks == 2
    assert button.scrolls == 2


def test_advance_search_page_returns_new_response_on_success() -> None:
    response = FakeResponse(
        url="https://example.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/?page=2"
    )
    page = FakePage(next_button_count=2, outcomes=[response])

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=lambda _message: None,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is True
    assert result.response is response
    assert result.stop_reason is None
    button = page.locator_stub.nth(0)
    assert button.clicks == 1
    assert button.scrolls == 1


def test_advance_search_page_stops_when_click_times_out() -> None:
    page = FakePage(
        next_button_count=2,
        outcomes=[
            FakeResponse(url="https://example.com/unused"),
            FakeResponse(url="https://example.com/unused"),
        ],
        click_error=PlaywrightTimeoutError("click timeout"),
    )
    logs: list[str] = []

    result = asyncio.run(
        advance_search_page(
            page=page,
            page_num=2,
            logger=logs.append,
            wait_after_click=_noop_random_sleep,
            retry_sleep=_noop_sleep,
        )
    )

    assert result.advanced is False
    assert result.response is None
    assert result.stop_reason == "timeout"


def test_is_search_results_response_matches_exact_search_api() -> None:
    response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/?foo=bar",
        method="POST",
    )

    assert is_search_results_response(response) is True


def test_is_search_results_response_rejects_search_shade_api() -> None:
    response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search.shade/1.0/?foo=bar",
        method="POST",
    )

    assert is_search_results_response(response) is False


def test_is_search_results_response_rejects_non_post_request() -> None:
    response = FakeResponse(
        url="https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/?foo=bar",
        method="GET",
    )

    assert is_search_results_response(response) is False

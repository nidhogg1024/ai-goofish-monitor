import pytest

from src.services.browser_login_service import BrowserLoginService


class _FakeLocator:
    def __init__(self, *, count: int, visible: bool):
        self._count = count
        self._visible = visible

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def is_visible(self, timeout=0):
        return self._visible


class _FakePage:
    def __init__(self, *, url: str, login_visible: bool = False):
        self.url = url
        self._login_visible = login_visible

    def locator(self, _selector: str):
        return _FakeLocator(count=1 if self._login_visible else 0, visible=self._login_visible)


class _FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    async def cookies(self):
        return self._cookies


@pytest.mark.asyncio
async def test_is_authenticated_requires_cookie_value_and_non_login_page():
    service = BrowserLoginService()
    context = _FakeContext([
        {"name": "tracknick", "value": ""},
        {"name": "cookie2", "value": "deleted"},
    ])
    page = _FakePage(url="https://www.goofish.com/")

    assert await service._is_authenticated(context, page) is False


@pytest.mark.asyncio
async def test_is_authenticated_rejects_page_when_login_entry_still_visible():
    service = BrowserLoginService()
    context = _FakeContext([
        {"name": "tracknick", "value": "user_nick"},
    ])
    page = _FakePage(url="https://www.goofish.com/", login_visible=True)

    assert await service._is_authenticated(context, page) is False


@pytest.mark.asyncio
async def test_is_authenticated_accepts_valid_cookie_and_hidden_login_entry():
    service = BrowserLoginService()
    context = _FakeContext([
        {"name": "cookie2", "value": "abcdef"},
    ])
    page = _FakePage(url="https://www.goofish.com/", login_visible=False)

    assert await service._is_authenticated(context, page) is True

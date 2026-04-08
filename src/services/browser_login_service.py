"""
浏览器扫码登录服务
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.async_api import async_playwright

from src.failure_guard import FailureGuard
from src.infrastructure.config.env_manager import env_manager
from src.infrastructure.config.settings import settings as app_settings
from src.risk_control_guard import GlobalRiskControlGuard
from src.scraper import _is_login_url, _resolve_browser_channel
from src.services.account_state_service import prepare_account_path, register_account_path


ACTIVE_JOB_STATUSES = {"launching", "awaiting_scan", "saving"}
AUTH_COOKIE_NAMES = {"tracknick", "cookie2", "unb"}
DEFAULT_STATE_FILE = "xianyu_state.json"
_JOB_RETENTION_SECONDS = 600
LOGIN_ENTRY_SELECTORS = (
    "text=登录",
    "text=去登录",
    "button:has-text('登录')",
    "a:has-text('登录')",
)
logger = logging.getLogger(__name__)

TASK_FAILURE_GUARD = FailureGuard()
GLOBAL_RISK_GUARD = GlobalRiskControlGuard(
    cooldown_seconds=app_settings.risk_control_cooldown_seconds
)


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _strip_quotes(value: str) -> str:
    if not value:
        return value
    if value.startswith(("\"", "'")) and value.endswith(("\"", "'")):
        return value[1:-1]
    return value

def _default_state_file() -> Path:
    raw = env_manager.get_value("STATE_FILE", DEFAULT_STATE_FILE) or DEFAULT_STATE_FILE
    return Path(_strip_quotes(raw.strip()))


class BrowserLoginService:
    def __init__(self, timeout_seconds: int = 180) -> None:
        self.timeout_seconds = timeout_seconds
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def _purge_stale_jobs(self) -> None:
        """Remove completed jobs older than _JOB_RETENTION_SECONDS."""
        now = time.monotonic()
        stale = [
            jid
            for jid, job in self._jobs.items()
            if job["status"] not in ACTIVE_JOB_STATUSES
            and now - job.get("_finished_mono", now) > _JOB_RETENTION_SECONDS
        ]
        for jid in stale:
            self._jobs.pop(jid, None)

    async def start_job(self, account_name: str, *, set_as_default: bool = True) -> Dict[str, Any]:
        async with self._lock:
            self._purge_stale_jobs()
            for job in self._jobs.values():
                if job["status"] in ACTIVE_JOB_STATUSES:
                    raise RuntimeError("已有扫码登录任务正在进行，请先完成当前任务。")

            job_id = uuid.uuid4().hex
            _, account_path = prepare_account_path(account_name)
            default_state_path = _default_state_file()
            job = {
                "id": job_id,
                "account_name": account_name,
                "status": "launching",
                "message": "正在启动浏览器...",
                "created_at": _utc_timestamp(),
                "updated_at": _utc_timestamp(),
                "finished_at": None,
                "error": None,
                "account_path": str(account_path),
                "set_as_default": set_as_default,
                "default_state_path": str(default_state_path) if set_as_default else None,
                "browser_opened": False,
                "task": None,
            }
            self._jobs[job_id] = job
            job["task"] = asyncio.create_task(
                self._run_job(job_id),
                name=f"browser-login-{job_id}",
            )
            return self._serialize_job(job)

    async def get_job(self, job_id: str) -> Dict[str, Any]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            return self._serialize_job(job)

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = [
                job.get("task")
                for job in self._jobs.values()
                if job.get("task") and not job["task"].done()
            ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_job(self, job_id: str) -> None:
        browser = None
        context = None
        playwright = None
        page = None
        try:
            playwright = await async_playwright().start()
            browser = await self._launch_browser(playwright)
            context = await browser.new_context(
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1440, "height": 960},
            )
            page = await context.new_page()
            await self._set_job_state(
                job_id,
                status="launching",
                message="浏览器已启动，正在打开闲鱼登录页...",
                browser_opened=True,
            )

            await page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)

            if await self._is_authenticated(context, page):
                await self._set_job_state(job_id, status="saving", message="检测到已登录，正在保存状态...")
                await self._persist_state(job_id, context, page)
                await self._set_job_state(job_id, status="completed", message="登录状态已保存。")
                return

            clicked = await self._click_login_entry(page)
            if clicked:
                message = "浏览器已打开，请使用手机扫码登录。"
            else:
                message = "浏览器已打开，如未看到二维码，请在页面右上角点击登录后扫码。"
            await self._set_job_state(job_id, status="awaiting_scan", message=message, browser_opened=True)

            deadline = time.monotonic() + self.timeout_seconds
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError("扫码窗口已被关闭，请重新发起登录。")
                if await self._is_authenticated(context, page):
                    await page.wait_for_timeout(1200)
                    if not await self._is_authenticated(context, page):
                        await self._set_job_state(
                            job_id,
                            status="awaiting_scan",
                            message="检测到页面状态变化，正在确认是否已真正登录...",
                            browser_opened=True,
                        )
                        await asyncio.sleep(1.0)
                        continue
                    await self._set_job_state(job_id, status="saving", message="扫码成功，正在保存登录状态...")
                    await self._persist_state(job_id, context, page)
                    await self._set_job_state(job_id, status="completed", message="登录状态已保存。")
                    return
                await asyncio.sleep(1.5)

            raise TimeoutError("等待扫码登录超时，请重新发起。")
        except asyncio.CancelledError:
            await self._set_job_state(job_id, status="cancelled", message="扫码登录任务已取消。")
            raise
        except Exception as exc:
            await self._set_job_state(
                job_id,
                status="failed",
                message=str(exc) or "扫码登录失败。",
                error=str(exc) or "扫码登录失败。",
            )
        finally:
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
            if playwright is not None:
                with contextlib.suppress(Exception):
                    await playwright.stop()

    async def _launch_browser(self, playwright):
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--start-maximized",
        ]
        channel = _resolve_browser_channel()
        candidates = [channel]
        if channel != "chromium":
            candidates.append("chromium")
        candidates.append(None)

        errors = []
        for candidate in candidates:
            try:
                launch_kwargs: Dict[str, Any] = {
                    "headless": False,
                    "args": launch_args,
                }
                if candidate:
                    launch_kwargs["channel"] = candidate
                return await playwright.chromium.launch(**launch_kwargs)
            except Exception as exc:
                label = candidate or "playwright-default"
                errors.append(f"{label}: {exc}")

        raise RuntimeError("启动浏览器失败：" + " | ".join(errors))

    async def _click_login_entry(self, page) -> bool:
        for selector in LOGIN_ENTRY_SELECTORS:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible(timeout=1000):
                    await locator.click(timeout=1500)
                    await page.wait_for_timeout(1000)
                    return True
            except Exception:
                continue
        return _is_login_url(page.url)

    def _has_auth_cookie_values(self, cookies) -> bool:
        for cookie in cookies:
            name = str(cookie.get("name", "")).strip()
            value = str(cookie.get("value", "")).strip()
            if name not in AUTH_COOKIE_NAMES:
                continue
            if value and value.lower() not in {"deleted", "null", "undefined"}:
                return True
        return False

    async def _has_visible_login_entry(self, page) -> bool:
        for selector in LOGIN_ENTRY_SELECTORS:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                if await locator.is_visible(timeout=600):
                    return True
            except Exception:
                continue
        return False

    async def _is_authenticated(self, context, page=None) -> bool:
        cookies = await context.cookies()
        if not self._has_auth_cookie_values(cookies):
            return False
        if page is None:
            return True
        if _is_login_url(page.url):
            return False
        if await self._has_visible_login_entry(page):
            return False
        return True

    async def _persist_state(self, job_id: str, context, page) -> None:
        storage_state = await context.storage_state()
        snapshot = await self._build_snapshot(storage_state, page)
        serialized = json.dumps(snapshot, ensure_ascii=False, indent=2)

        job = self._jobs[job_id]
        account_path = Path(job["account_path"])
        account_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._atomic_write, account_path, serialized)
        register_account_path(job["account_name"], account_path)

        # Writing both account file and default state file is NOT atomic
        # across the two files; a crash between writes may leave them out of sync.
        if job.get("set_as_default"):
            default_state_path = Path(job["default_state_path"])
            if default_state_path.parent != Path("."):
                default_state_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self._atomic_write, default_state_path, serialized)

        TASK_FAILURE_GUARD.reset_all()
        GLOBAL_RISK_GUARD.clear()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        dir_path = str(path.parent) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    async def _build_snapshot(self, storage_state: Dict[str, Any], page) -> Dict[str, Any]:
        metadata = await page.evaluate(
            """() => ({
                navigator: {
                    userAgent: navigator.userAgent,
                    language: navigator.language,
                    maxTouchPoints: navigator.maxTouchPoints || 0,
                },
                screen: {
                    width: window.screen.width,
                    height: window.screen.height,
                    devicePixelRatio: window.devicePixelRatio || 1,
                },
                intl: {
                    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                },
                page: {
                    url: window.location.href,
                    title: document.title,
                },
            })"""
        )
        language = metadata.get("navigator", {}).get("language") or "zh-CN"
        return {
            "cookies": storage_state.get("cookies", []),
            "origins": storage_state.get("origins", []),
            "headers": {
                "User-Agent": metadata.get("navigator", {}).get("userAgent", ""),
                "Accept-Language": language,
            },
            "env": {
                "navigator": metadata.get("navigator", {}),
                "screen": metadata.get("screen", {}),
                "intl": metadata.get("intl", {}),
            },
            "page": metadata.get("page", {}),
            "storage": {
                "origins_count": len(storage_state.get("origins", [])),
            },
        }

    async def _set_job_state(self, job_id: str, **updates: Any) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(updates)
            job["updated_at"] = _utc_timestamp()
            if updates.get("status") in {"completed", "failed", "cancelled"}:
                job["finished_at"] = _utc_timestamp()
                job["_finished_mono"] = time.monotonic()

    def _serialize_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": job["id"],
            "account_name": job["account_name"],
            "status": job["status"],
            "message": job["message"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "finished_at": job["finished_at"],
            "error": job.get("error"),
            "account_path": job["account_path"],
            "set_as_default": job.get("set_as_default", True),
            "default_state_path": job.get("default_state_path"),
            "browser_opened": job.get("browser_opened", False),
        }


browser_login_service = BrowserLoginService()

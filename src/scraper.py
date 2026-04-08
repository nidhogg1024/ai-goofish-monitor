import asyncio
import json
import os
import random
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import (
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from src.ai_handler import (
    download_all_images,
    get_ai_analysis,
    send_ntfy_notification,
    cleanup_task_images,
)
from src.config import (
    AI_DEBUG_MODE,
    DETAIL_API_URL_PATTERN,
    LOGIN_IS_EDGE,
    RUN_HEADLESS,
    RUNNING_IN_DOCKER,
    SKIP_AI_ANALYSIS,
    STATE_FILE,
)
from src.parsers import (
    _parse_search_results_json,
    _parse_user_items_data,
    calculate_reputation_from_ratings,
    parse_ratings_data,
    parse_user_head_data,
)
from src.utils import (
    format_registration_days,
    get_link_unique_key,
    log_time,
    random_sleep,
    safe_get,
    save_to_jsonl,
)
from src.rotation import RotationPool, load_state_files, parse_proxy_pool, RotationItem
from src.failure_guard import FailureGuard
from src.infrastructure.config.settings import settings as app_settings
from src.risk_control_guard import GlobalRiskControlGuard
from src.services.account_strategy_service import resolve_account_runtime_plan
from src.infrastructure.persistence.storage_names import build_result_filename
from src.services.item_analysis_dispatcher import (
    ItemAnalysisDispatcher,
    ItemAnalysisJob,
)
from src.services.price_history_service import (
    build_market_reference,
    load_price_snapshots,
    record_market_snapshots,
)
from src.services.result_storage_service import load_processed_link_keys
from src.services.seller_profile_cache import SellerProfileCache
from src.services.search_pagination import (
    advance_search_page,
    is_search_results_response,
)
from src.task_item_matcher import match_task_item


class RiskControlError(Exception):
    pass


class LoginRequiredError(Exception):
    """Raised when Goofish redirects to the passport/mini_login flow."""


class InteractiveRecoveryRequested(Exception):
    """Raised when a visible-browser recovery succeeded and the task should retry."""


_LOGIN_WAIT_TIMEOUT = 180  # 等待扫码登录的超时时间（秒）
_AUTH_COOKIE_NAMES = {"tracknick", "cookie2", "unb"}
_LOGIN_ENTRY_SELECTORS = (
    "text=登录",
    "text=去登录",
    "button:has-text('登录')",
    "a:has-text('登录')",
)
_RISK_WIDGET_SELECTORS = (
    "div.baxia-dialog-mask",
    "div.J_MIDDLEWARE_FRAME_WIDGET",
)


def _has_valid_auth_cookie_values(cookies) -> bool:
    for cookie in cookies:
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", "")).strip()
        if name not in _AUTH_COOKIE_NAMES:
            continue
        if value and value.lower() not in {"deleted", "null", "undefined"}:
            return True
    return False


async def _has_visible_login_entry(page) -> bool:
    for selector in _LOGIN_ENTRY_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            if await locator.is_visible(timeout=600):
                return True
        except Exception:
            continue
    return False


async def _has_visible_risk_overlay(page) -> bool:
    for selector in _RISK_WIDGET_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            if await locator.is_visible(timeout=600):
                return True
        except Exception:
            continue
    return False


async def _is_authenticated_session(page, context, *, require_clear_page: bool = True) -> bool:
    cookies = await context.cookies()
    if not _has_valid_auth_cookie_values(cookies):
        return False
    if not require_clear_page:
        return True
    if _is_login_url(page.url):
        return False
    if await _has_visible_login_entry(page):
        return False
    if await _has_visible_risk_overlay(page):
        return False
    return True


async def _save_storage_state(context, state_file: str) -> None:
    storage_state = await context.storage_state()
    state_dir = os.path.dirname(state_file)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(storage_state, f, ensure_ascii=False, indent=2)


async def _click_login_entry(page) -> bool:
    for selector in _LOGIN_ENTRY_SELECTORS:
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


async def _wait_for_login(page, context, state_file: str, *, timeout: int = _LOGIN_WAIT_TIMEOUT) -> bool:
    """检测到需要登录时，等待用户扫码完成，保存状态后返回 True；超时返回 False。"""
    print("\n" + "=" * 60)
    print("🔑 检测到需要登录，请在浏览器中扫码完成登录。")
    print(f"   等待时间：最多 {timeout} 秒")
    print("=" * 60 + "\n")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if page.is_closed():
                print("⚠️  浏览器页面已关闭，无法继续等待登录。")
                return False
            if await _is_authenticated_session(page, context):
                print("\n✅ 扫码登录成功！正在保存登录状态...")
                await _save_storage_state(context, state_file)
                print(f"✅ 登录状态已保存到 {state_file}")
                # 等一下让页面稳定
                await asyncio.sleep(2)
                return True
        except Exception:
            pass
        await asyncio.sleep(1.5)

    print("\n⏰ 等待扫码登录超时，任务将中止。")
    return False


FAILURE_GUARD = FailureGuard()
GLOBAL_RISK_GUARD = GlobalRiskControlGuard(
    cooldown_seconds=app_settings.risk_control_cooldown_seconds
)
EDGE_DOCKER_WARNING_PRINTED = False
MANUAL_VERIFICATION_ALERT_INTERVAL = 600
_manual_verification_alerts: dict[str, float] = {}


def _is_login_url(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "passport.goofish.com" in lowered or "mini_login" in lowered


def _resolve_browser_channel() -> str:
    global EDGE_DOCKER_WARNING_PRINTED
    if RUNNING_IN_DOCKER:
        if LOGIN_IS_EDGE and not EDGE_DOCKER_WARNING_PRINTED:
            print(
                "检测到 LOGIN_IS_EDGE=true，但 Docker 镜像未内置 Edge，"
                "任务运行时将改用 Chromium。"
            )
            EDGE_DOCKER_WARNING_PRINTED = True
        return "chromium"
    return "msedge" if LOGIN_IS_EDGE else "chrome"


def _should_send_manual_verification_alert(task_key: str) -> bool:
    now = time.monotonic()
    last_sent_at = _manual_verification_alerts.get(task_key)
    if last_sent_at and now - last_sent_at < MANUAL_VERIFICATION_ALERT_INTERVAL:
        return False
    _manual_verification_alerts[task_key] = now
    return True


async def _notify_manual_verification_required(
    *,
    task_name: str,
    keyword: str,
    target_url: Optional[str],
    reason: str,
) -> None:
    task_key = f"{task_name}|{keyword}|{reason}"
    if not _should_send_manual_verification_alert(task_key):
        print(f"手动验证提醒已在冷却中，跳过重复通知: {task_name}")
        return

    product_data = {
        "商品标题": f"[需要人工验证] {task_name}",
        "当前售价": "N/A",
        "商品链接": target_url or "https://www.goofish.com/",
    }
    notify_reason = (
        f"任务触发闲鱼登录/风控验证，需要你手动完成一次验证。"
        f"\n任务: {task_name}"
        f"\n关键词: {keyword or 'N/A'}"
        f"\n原因: {reason}"
        f"\n系统已自动打开可见浏览器窗口。完成验证后会自动保存登录态并继续任务。"
    )
    try:
        await send_ntfy_notification(product_data, notify_reason)
    except Exception as exc:
        print(f"发送人工验证提醒失败: {exc}")


def _should_analyze_images(task_config: dict) -> bool:
    raw_value = task_config.get("analyze_images", True)
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).strip().lower() not in {"false", "0", "no", "off"}


def _format_failure_reason(reason: str, limit: int = 500) -> str:
    if not reason:
        return "未知错误"
    cleaned = " ".join(str(reason).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


async def _notify_task_failure(
    task_config: dict, reason: str, *, cookie_path: Optional[str]
) -> None:
    task_name = task_config.get("task_name", "未命名任务")
    keyword = task_config.get("keyword", "")
    formatted_reason = _format_failure_reason(reason)

    # Some failures are deterministic misconfiguration and should pause/notify immediately.
    pause_immediately = any(
        marker in formatted_reason
        for marker in (
            "未找到可用的代理地址",
            "未找到可用的登录状态文件",
        )
    )

    guard_result = FAILURE_GUARD.record_failure(
        task_name,
        formatted_reason,
        cookie_path=cookie_path,
        min_failures_to_pause=1 if pause_immediately else None,
    )

    if not guard_result.get("should_notify"):
        print(
            f"[FailureGuard] 任务 '{task_name}' 失败计数 {guard_result.get('consecutive_failures')}/{FAILURE_GUARD.threshold}，暂不通知。"
        )
        return

    paused_until = guard_result.get("paused_until")
    paused_until_str = (
        paused_until.strftime("%Y-%m-%d %H:%M:%S") if paused_until else "N/A"
    )

    product_data = {
        "商品标题": f"[任务异常] {task_name}",
        "当前售价": "N/A",
        "商品链接": "#",
    }
    notify_reason = (
        f"任务运行失败(已连续 {guard_result.get('consecutive_failures')}/{FAILURE_GUARD.threshold} 次): {formatted_reason}"
        f"\n任务: {task_name}"
        f"\n关键词: {keyword or 'N/A'}"
        f"\n已自动暂停重试，暂停到: {paused_until_str}"
        f"\n修复后(更新登录态/cookies文件)将自动恢复。"
    )

    try:
        await send_ntfy_notification(product_data, notify_reason)
    except Exception as e:
        print(f"发送任务异常通知失败: {e}")


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_rotation_settings(task_config: dict) -> dict:
    account_cfg = task_config.get("account_rotation") or {}
    proxy_cfg = task_config.get("proxy_rotation") or {}

    account_enabled = _as_bool(
        account_cfg.get("enabled"),
        _as_bool(os.getenv("ACCOUNT_ROTATION_ENABLED"), False),
    )
    account_mode = (
        account_cfg.get("mode") or os.getenv("ACCOUNT_ROTATION_MODE", "per_task")
    ).lower()
    account_state_dir = account_cfg.get("state_dir") or os.getenv(
        "ACCOUNT_STATE_DIR", "state"
    )
    account_retry_limit = _as_int(
        account_cfg.get("retry_limit"),
        _as_int(os.getenv("ACCOUNT_ROTATION_RETRY_LIMIT"), 2),
    )
    account_blacklist_ttl = _as_int(
        account_cfg.get("blacklist_ttl_sec"),
        _as_int(os.getenv("ACCOUNT_BLACKLIST_TTL"), 300),
    )

    proxy_enabled = _as_bool(
        proxy_cfg.get("enabled"), _as_bool(os.getenv("PROXY_ROTATION_ENABLED"), False)
    )
    proxy_mode = (
        proxy_cfg.get("mode") or os.getenv("PROXY_ROTATION_MODE", "per_task")
    ).lower()
    proxy_pool = proxy_cfg.get("proxy_pool") or os.getenv("PROXY_POOL", "")
    proxy_retry_limit = _as_int(
        proxy_cfg.get("retry_limit"),
        _as_int(os.getenv("PROXY_ROTATION_RETRY_LIMIT"), 2),
    )
    proxy_blacklist_ttl = _as_int(
        proxy_cfg.get("blacklist_ttl_sec"),
        _as_int(os.getenv("PROXY_BLACKLIST_TTL"), 300),
    )

    return {
        "account_enabled": account_enabled,
        "account_mode": account_mode,
        "account_state_dir": account_state_dir,
        "account_retry_limit": max(1, account_retry_limit),
        "account_blacklist_ttl": max(0, account_blacklist_ttl),
        "proxy_enabled": proxy_enabled,
        "proxy_mode": proxy_mode,
        "proxy_pool": proxy_pool,
        "proxy_retry_limit": max(1, proxy_retry_limit),
        "proxy_blacklist_ttl": max(0, proxy_blacklist_ttl),
    }


def _get_ai_analysis_concurrency(task_config: dict) -> int:
    configured = task_config.get("ai_analysis_concurrency")
    default = _as_int(os.getenv("AI_ANALYSIS_CONCURRENCY"), 2)
    return max(1, _as_int(configured, default))


def _get_seller_profile_cache_ttl(task_config: dict) -> int:
    configured = task_config.get("seller_profile_cache_ttl")
    default = _as_int(os.getenv("SELLER_PROFILE_CACHE_TTL"), 1800)
    return max(0, _as_int(configured, default))


def _default_context_options() -> dict:
    return {
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 900},
        "device_scale_factor": 2,
        "is_mobile": False,
        "has_touch": False,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "permissions": ["geolocation"],
        "geolocation": {"longitude": 121.4737, "latitude": 31.2304},
        "color_scheme": "light",
    }


def _clean_kwargs(options: dict) -> dict:
    return {k: v for k, v in options.items() if v is not None}


def _looks_like_mobile(ua: str) -> Optional[bool]:
    if not ua:
        return None
    ua_lower = ua.lower()
    if "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        return True
    if "windows" in ua_lower or "macintosh" in ua_lower:
        return False
    return None


def _build_context_overrides(snapshot: dict) -> dict:
    env = snapshot.get("env") or {}
    headers = snapshot.get("headers") or {}
    navigator = env.get("navigator") or {}
    screen = env.get("screen") or {}
    intl = env.get("intl") or {}

    overrides = {}

    ua = (
        headers.get("User-Agent")
        or headers.get("user-agent")
        or navigator.get("userAgent")
    )
    if ua:
        overrides["user_agent"] = ua

    accept_language = headers.get("Accept-Language") or headers.get("accept-language")
    locale = None
    if accept_language:
        locale = accept_language.split(",")[0].strip()
    elif navigator.get("language"):
        locale = navigator["language"]
    if locale:
        overrides["locale"] = locale

    tz = intl.get("timeZone")
    if tz:
        overrides["timezone_id"] = tz

    width = screen.get("width")
    height = screen.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        overrides["viewport"] = {"width": int(width), "height": int(height)}

    dpr = screen.get("devicePixelRatio")
    if isinstance(dpr, (int, float)):
        overrides["device_scale_factor"] = float(dpr)

    touch_points = navigator.get("maxTouchPoints")
    if isinstance(touch_points, (int, float)):
        overrides["has_touch"] = touch_points > 0

    mobile_flag = _looks_like_mobile(ua or "")
    if mobile_flag is not None:
        overrides["is_mobile"] = mobile_flag

    return _clean_kwargs(overrides)


def _build_extra_headers(raw_headers: Optional[dict]) -> dict:
    if not raw_headers:
        return {}
    excluded = {"cookie", "content-length"}
    headers = {}
    for key, value in raw_headers.items():
        if not key or key.lower() in excluded or value is None:
            continue
        headers[key] = value
    return headers


async def _interactive_recover_session(
    state_file: str,
    *,
    task_name: str,
    keyword: str,
    target_url: Optional[str],
    reason: str,
    proxy_server: Optional[str] = None,
    timeout: int = _LOGIN_WAIT_TIMEOUT,
) -> bool:
    print("\n" + "=" * 66)
    print("⚠️  任务在无头模式下触发了登录/风控校验。")
    print(f"原因: {reason}")
    print("即将临时打开一个可见浏览器窗口，请手动完成扫码或验证。")
    print("完成后系统会自动保存登录状态，并回到无头模式继续重试。")
    print("=" * 66 + "\n")

    GLOBAL_RISK_GUARD.activate(
        task_name=task_name,
        keyword=keyword,
        reason=reason,
    )

    await _notify_manual_verification_required(
        task_name=task_name,
        keyword=keyword,
        target_url=target_url,
        reason=reason,
    )

    snapshot_data = None
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)
        except Exception as e:
            print(f"警告：读取登录状态文件失败，将直接按路径使用: {e}")

    async with async_playwright() as p:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--start-maximized",
        ]
        launch_kwargs = {"headless": False, "args": launch_args}
        if proxy_server:
            launch_kwargs["proxy"] = {"server": proxy_server}
        launch_kwargs["channel"] = _resolve_browser_channel()

        browser = await p.chromium.launch(**launch_kwargs)
        try:
            context_kwargs = _default_context_options()
            storage_state_arg = state_file if os.path.exists(state_file) else None
            if isinstance(snapshot_data, dict):
                if any(
                    key in snapshot_data for key in ("env", "headers", "page", "storage")
                ):
                    storage_state_arg = {"cookies": snapshot_data.get("cookies", [])}
                    context_kwargs.update(_build_context_overrides(snapshot_data))
                    extra_headers = _build_extra_headers(snapshot_data.get("headers"))
                    if extra_headers:
                        context_kwargs["extra_http_headers"] = extra_headers
                else:
                    storage_state_arg = snapshot_data

            context = await browser.new_context(
                storage_state=storage_state_arg,
                **_clean_kwargs(context_kwargs),
            )
            try:
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [
                            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
                            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                            {name: 'Native Client', filename: 'internal-nacl-plugin'},
                        ]
                    });
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
                    window.chrome = {runtime: {}, loadTimes: function() {}, csi: function() {}};
                """)
            except Exception:
                pass

            page = await context.new_page()
            destination = target_url or "https://www.goofish.com/"
            await page.goto(destination, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1200)
            await _click_login_entry(page)

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if page.is_closed():
                    print("⚠️  交互恢复窗口已关闭，未能完成恢复。")
                    return False
                if await _is_authenticated_session(page, context):
                    await page.wait_for_timeout(1200)
                    if not await _is_authenticated_session(page, context):
                        await asyncio.sleep(1.0)
                        continue
                    await _save_storage_state(context, state_file)
                    GLOBAL_RISK_GUARD.clear()
                    print(f"✅ 交互恢复成功，登录状态已更新到 {state_file}")
                    return True
                await asyncio.sleep(1.5)

            print("⏰ 交互恢复等待超时，本次任务将中止。")
            return False
        finally:
            await browser.close()


async def scrape_user_profile(context, user_id: str) -> dict:
    """
    【新版】访问指定用户的个人主页，按顺序采集其摘要信息、完整的商品列表和完整的评价列表。
    """
    print(f"   -> 开始采集用户ID: {user_id} 的完整信息...")
    profile_data = {}
    page = await context.new_page()

    # 为各项异步任务准备Future和数据容器
    head_api_future = asyncio.get_running_loop().create_future()

    all_items, all_ratings = [], []
    stop_item_scrolling, stop_rating_scrolling = asyncio.Event(), asyncio.Event()

    async def handle_response(response: Response):
        # 捕获头部摘要API
        if (
            "mtop.idle.web.user.page.head" in response.url
            and not head_api_future.done()
        ):
            try:
                head_api_future.set_result(await response.json())
                print(f"      [API捕获] 用户头部信息... 成功")
            except Exception as e:
                if not head_api_future.done():
                    head_api_future.set_exception(e)

        # 捕获商品列表API
        elif "mtop.idle.web.xyh.item.list" in response.url:
            try:
                data = await response.json()
                all_items.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API捕获] 商品列表... 当前已捕获 {len(all_items)} 件")
                if not data.get("data", {}).get("nextPage", True):
                    stop_item_scrolling.set()
            except Exception as e:
                stop_item_scrolling.set()

        # 捕获评价列表API
        elif "mtop.idle.web.trade.rate.list" in response.url:
            try:
                data = await response.json()
                all_ratings.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API捕获] 评价列表... 当前已捕获 {len(all_ratings)} 条")
                if not data.get("data", {}).get("nextPage", True):
                    stop_rating_scrolling.set()
            except Exception as e:
                stop_rating_scrolling.set()

    page.on("response", handle_response)

    try:
        # --- 任务1: 导航并采集头部信息 ---
        await page.goto(
            f"https://www.goofish.com/personal?userId={user_id}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        head_data = await asyncio.wait_for(head_api_future, timeout=15)
        profile_data = await parse_user_head_data(head_data)

        # --- 任务2: 滚动加载所有商品 (默认页面) ---
        print("      [采集阶段] 开始采集该用户的商品列表...")
        await random_sleep(2, 4)  # 等待第一页商品API完成
        while not stop_item_scrolling.is_set():
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            try:
                await asyncio.wait_for(stop_item_scrolling.wait(), timeout=8)
            except asyncio.TimeoutError:
                print("      [滚动超时] 商品列表可能已加载完毕。")
                break
        profile_data["卖家发布的商品列表"] = await _parse_user_items_data(all_items)

        # --- 任务3: 点击并采集所有评价 ---
        print("      [采集阶段] 开始采集该用户的评价列表...")
        rating_tab_locator = page.locator("//div[text()='信用及评价']/ancestor::li")
        if await rating_tab_locator.count() > 0:
            await rating_tab_locator.click()
            await random_sleep(3, 5)  # 等待第一页评价API完成

            while not stop_rating_scrolling.is_set():
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    await asyncio.wait_for(stop_rating_scrolling.wait(), timeout=8)
                except asyncio.TimeoutError:
                    print("      [滚动超时] 评价列表可能已加载完毕。")
                    break

            profile_data["卖家收到的评价列表"] = await parse_ratings_data(all_ratings)
            reputation_stats = await calculate_reputation_from_ratings(all_ratings)
            profile_data.update(reputation_stats)
        else:
            print("      [警告] 未找到评价选项卡，跳过评价采集。")

    except Exception as e:
        print(f"   [错误] 采集用户 {user_id} 信息时发生错误: {e}")
    finally:
        page.remove_listener("response", handle_response)
        await page.close()
        print(f"   -> 用户 {user_id} 信息采集完成。")

    return profile_data


async def scrape_xianyu(task_config: dict, debug_limit: int = 0):
    """
    【核心执行器】
    根据单个任务配置，异步爬取闲鱼商品数据，并对每个新发现的商品进行实时的、独立的AI分析和通知。
    """
    keyword = task_config["keyword"]
    task_name = task_config.get("task_name", "未命名任务")
    max_pages = task_config.get("max_pages", 1)
    personal_only = task_config.get("personal_only", False)
    min_price = task_config.get("min_price")
    max_price = task_config.get("max_price")
    ai_prompt_text = task_config.get("ai_prompt_text", "")
    analyze_images = _should_analyze_images(task_config)
    decision_mode = str(task_config.get("decision_mode", "ai")).strip().lower()
    if decision_mode not in {"ai", "keyword"}:
        decision_mode = "ai"
    keyword_rules = task_config.get("keyword_rules") or []
    free_shipping = task_config.get("free_shipping", False)
    raw_new_publish = task_config.get("new_publish_option") or ""
    new_publish_option = raw_new_publish.strip()
    if new_publish_option == "__none__":
        new_publish_option = ""
    region_filter = (task_config.get("region") or "").strip()

    processed_links = set()
    history_run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    history_seen_item_ids: set[str] = set()
    historical_snapshots = load_price_snapshots(keyword)
    result_filename = build_result_filename(keyword)
    processed_links = load_processed_link_keys(keyword)

    is_cold_start = len(processed_links) == 0
    if is_cold_start:
        effective_max_pages = task_config.get("first_scan_max_pages", 10)
        print(f"LOG: 冷启动模式：结果集 {result_filename} 为空，首次扫描将拉取 {effective_max_pages} 页（综合排序）")
    else:
        effective_max_pages = max_pages
        print(f"LOG: 增量模式：已有 {len(processed_links)} 条历史数据，将扫描 {effective_max_pages} 页（新发布排序）")

    rotation_settings = _get_rotation_settings(task_config)
    account_items = load_state_files(rotation_settings["account_state_dir"])
    runtime_plan = resolve_account_runtime_plan(
        strategy=task_config.get("account_strategy"),
        account_state_file=task_config.get("account_state_file"),
        has_root_state_file=os.path.exists(STATE_FILE),
        available_account_files=account_items,
    )
    forced_account = runtime_plan["forced_account"]
    if runtime_plan["prefer_root_state"]:
        account_items = [STATE_FILE]
        rotation_settings["account_enabled"] = False
    elif runtime_plan["use_account_pool"]:
        rotation_settings["account_enabled"] = True
    else:
        rotation_settings["account_enabled"] = False

    account_pool = RotationPool(
        account_items, rotation_settings["account_blacklist_ttl"], "account"
    )
    proxy_pool = RotationPool(
        parse_proxy_pool(rotation_settings["proxy_pool"]),
        rotation_settings["proxy_blacklist_ttl"],
        "proxy",
    )

    selected_account: Optional[RotationItem] = None
    selected_proxy: Optional[RotationItem] = None

    def _select_account(force_new: bool = False) -> Optional[RotationItem]:
        nonlocal selected_account
        if forced_account:
            return RotationItem(value=forced_account)
        if not rotation_settings["account_enabled"]:
            if os.path.exists(STATE_FILE):
                return RotationItem(value=STATE_FILE)
            return None
        if (
            rotation_settings["account_mode"] == "per_task"
            and selected_account
            and not force_new
        ):
            return selected_account
        picked = account_pool.pick_random()
        return picked or selected_account

    def _select_proxy(force_new: bool = False) -> Optional[RotationItem]:
        nonlocal selected_proxy
        if not rotation_settings["proxy_enabled"]:
            return None
        if (
            rotation_settings["proxy_mode"] == "per_task"
            and selected_proxy
            and not force_new
        ):
            return selected_proxy
        picked = proxy_pool.pick_random()
        return picked or selected_proxy

    async def _run_scrape_attempt(state_file: str, proxy_server: Optional[str]) -> int:
        processed_item_count = 0
        stop_scraping = False

        if not os.path.exists(state_file):
            if RUN_HEADLESS:
                raise FileNotFoundError(f"登录状态文件不存在: {state_file}（headless 模式无法扫码，请先完成登录）")
            print(f"⚠️  登录状态文件不存在: {state_file}，将打开浏览器等待扫码登录。")

        snapshot_data = None
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    snapshot_data = json.load(f)
            except Exception as e:
                print(f"警告：读取登录状态文件失败，将直接按路径使用: {e}")

        async with async_playwright() as p:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]

            launch_kwargs = {"headless": RUN_HEADLESS, "args": launch_args}
            if proxy_server:
                launch_kwargs["proxy"] = {"server": proxy_server}

            launch_kwargs["channel"] = _resolve_browser_channel()

            browser = await p.chromium.launch(**launch_kwargs)

            context_kwargs = _default_context_options()
            storage_state_arg = state_file if os.path.exists(state_file) else None
            analysis_dispatcher: Optional[ItemAnalysisDispatcher] = None

            if isinstance(snapshot_data, dict):
                # 新版扩展导出的增强快照，包含环境和Header
                if any(
                    key in snapshot_data
                    for key in ("env", "headers", "page", "storage")
                ):
                    print(f"检测到增强浏览器快照，应用环境参数: {state_file}")
                    storage_state_arg = {"cookies": snapshot_data.get("cookies", [])}
                    context_kwargs.update(_build_context_overrides(snapshot_data))
                    extra_headers = _build_extra_headers(snapshot_data.get("headers"))
                    if extra_headers:
                        context_kwargs["extra_http_headers"] = extra_headers
                else:
                    storage_state_arg = snapshot_data

            context_kwargs = _clean_kwargs(context_kwargs)
            context = await browser.new_context(
                storage_state=storage_state_arg, **context_kwargs
            )
            seller_profile_cache = SellerProfileCache(
                ttl_seconds=_get_seller_profile_cache_ttl(task_config)
            )
            analysis_dispatcher = ItemAnalysisDispatcher(
                concurrency=_get_ai_analysis_concurrency(task_config),
                skip_ai_analysis=SKIP_AI_ANALYSIS,
                seller_loader=lambda user_id: seller_profile_cache.get_or_load(
                    str(user_id),
                    lambda seller_key: scrape_user_profile(context, seller_key),
                ),
                image_downloader=download_all_images,
                ai_analyzer=get_ai_analysis,
                notifier=send_ntfy_notification,
                saver=save_to_jsonl,
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
                        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                        {name: 'Native Client', filename: 'internal-nacl-plugin'},
                    ]
                });
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
                window.chrome = {runtime: {}, loadTimes: function() {}, csi: function() {}};
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({state: Notification.permission}) :
                        originalQuery(parameters)
                );
            """)

            page = await context.new_page()

            try:
                # 步骤 0 - 模拟真实用户：先访问首页（重要的反检测措施）
                log_time("步骤 0 - 模拟真实用户访问首页...")
                await page.goto(
                    "https://www.goofish.com/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                log_time("[反爬] 在首页停留，模拟浏览...")
                await random_sleep(1, 2)

                # 模拟随机滚动（移动设备的触摸滚动）
                try:
                    await page.evaluate("window.scrollBy(0, Math.random() * 500 + 200)")
                except Exception as e:
                    if "Execution context was destroyed" in str(e):
                        log_time("首页仍在跳转，跳过一次滚动模拟。")
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    else:
                        raise
                await random_sleep(1, 2)

                log_time("步骤 1 - 导航到搜索结果页...")
                actual_search_term = task_config.get("search_query") or keyword
                params = {"q": actual_search_term}
                search_url = f"https://www.goofish.com/search?{urlencode(params)}"
                if actual_search_term != keyword:
                    log_time(f"宽搜索模式：搜索词 '{actual_search_term}'，过滤词 '{keyword}'")
                log_time(f"目标URL: {search_url}")

                # 先监听搜索接口响应，再执行导航，避免错过首次请求
                async with page.expect_response(
                    is_search_results_response, timeout=30000
                ) as initial_response_info:
                    await page.goto(
                        search_url, wait_until="domcontentloaded", timeout=60000
                    )
                if _is_login_url(page.url):
                    if RUN_HEADLESS:
                        recovered = await _interactive_recover_session(
                            state_file,
                            task_name=task_name,
                            keyword=keyword,
                            target_url=search_url,
                            reason=f"跳转到登录页: {page.url}",
                            proxy_server=proxy_server,
                        )
                        if recovered:
                            raise InteractiveRecoveryRequested("已完成交互登录，准备重新执行任务。")
                        raise LoginRequiredError(
                            f"Login required: redirected to {page.url} (交互恢复未完成)"
                        )
                    logged_in = await _wait_for_login(page, context, state_file)
                    if not logged_in:
                        raise LoginRequiredError("扫码登录超时，任务中止。")
                    # 登录成功后重新导航到搜索页
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

                # 捕获初始搜索的API数据
                initial_response = await initial_response_info.value

                # 等待页面加载出关键筛选元素，以确认已成功进入搜索结果页
                try:
                    await page.wait_for_selector("text=新发布", timeout=15000)
                except PlaywrightTimeoutError as e:
                    if _is_login_url(page.url):
                        if RUN_HEADLESS:
                            recovered = await _interactive_recover_session(
                                state_file,
                                task_name=task_name,
                                keyword=keyword,
                                target_url=search_url,
                                reason=f"等待搜索页时跳转到登录页: {page.url}",
                                proxy_server=proxy_server,
                            )
                            if recovered:
                                raise InteractiveRecoveryRequested("已完成交互登录，准备重新执行任务。") from e
                            raise LoginRequiredError(
                                f"Login required: redirected to {page.url} (交互恢复未完成)"
                            ) from e
                        logged_in = await _wait_for_login(page, context, state_file)
                        if not logged_in:
                            raise LoginRequiredError("扫码登录超时，任务中止。") from e
                        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                        await page.wait_for_selector("text=新发布", timeout=15000)
                    else:
                        raise

                # 模拟真实用户行为：页面加载后的初始停留和浏览
                log_time("[反爬] 模拟用户查看页面...")
                await random_sleep(1, 3)

                # --- 新增：检查是否存在验证弹窗 ---
                baxia_dialog = page.locator("div.baxia-dialog-mask")
                middleware_widget = page.locator("div.J_MIDDLEWARE_FRAME_WIDGET")
                try:
                    # 等待弹窗在2秒内出现。如果出现，则执行块内代码。
                    await baxia_dialog.wait_for(state="visible", timeout=2000)
                    print(
                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                    )
                    print("检测到闲鱼反爬虫验证弹窗 (baxia-dialog)。")
                    if RUN_HEADLESS:
                        recovered = await _interactive_recover_session(
                            state_file,
                            task_name=task_name,
                            keyword=keyword,
                            target_url=page.url,
                            reason="触发 baxia-dialog 验证弹窗",
                            proxy_server=proxy_server,
                        )
                        print("===================================================================")
                        if recovered:
                            raise InteractiveRecoveryRequested("已完成交互验证，准备重新执行任务。")
                        raise RiskControlError("baxia-dialog")
                    print("请在浏览器中手动完成验证（滑动验证码等），完成后将自动继续。")
                    print(f"等待时间：最多 {_LOGIN_WAIT_TIMEOUT} 秒")
                    print("===================================================================")
                    # 等待弹窗消失（用户手动完成验证后弹窗会关闭）
                    try:
                        await baxia_dialog.wait_for(state="hidden", timeout=_LOGIN_WAIT_TIMEOUT * 1000)
                        print("✅ 验证通过，继续执行任务。")
                        await random_sleep(1, 2)
                    except PlaywrightTimeoutError:
                        print("⏰ 等待验证超时，任务将中止。")
                        raise RiskControlError("baxia-dialog 验证超时")
                except PlaywrightTimeoutError:
                    # 2秒内弹窗未出现，这是正常情况，继续执行
                    pass

                # 检查是否有J_MIDDLEWARE_FRAME_WIDGET覆盖层
                try:
                    await middleware_widget.wait_for(state="visible", timeout=2000)
                    print(
                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                    )
                    print("检测到闲鱼验证弹窗 (J_MIDDLEWARE_FRAME_WIDGET)。")
                    if RUN_HEADLESS:
                        recovered = await _interactive_recover_session(
                            state_file,
                            task_name=task_name,
                            keyword=keyword,
                            target_url=page.url,
                            reason="触发 J_MIDDLEWARE_FRAME_WIDGET 验证弹窗",
                            proxy_server=proxy_server,
                        )
                        print("===================================================================")
                        if recovered:
                            raise InteractiveRecoveryRequested("已完成交互验证，准备重新执行任务。")
                        raise RiskControlError("J_MIDDLEWARE_FRAME_WIDGET")
                    print("请在浏览器中手动完成验证，完成后将自动继续。")
                    print(f"等待时间：最多 {_LOGIN_WAIT_TIMEOUT} 秒")
                    print("===================================================================")
                    try:
                        await middleware_widget.wait_for(state="hidden", timeout=_LOGIN_WAIT_TIMEOUT * 1000)
                        print("✅ 验证通过，继续执行任务。")
                        await random_sleep(1, 2)
                    except PlaywrightTimeoutError:
                        print("⏰ 等待验证超时，任务将中止。")
                        raise RiskControlError("J_MIDDLEWARE_FRAME_WIDGET 验证超时")
                except PlaywrightTimeoutError:
                    # 2秒内弹窗未出现，这是正常情况，继续执行
                    pass
                # --- 结束新增 ---

                try:
                    await page.click("div[class*='closeIconBg']", timeout=3000)
                    print("LOG: 已关闭广告弹窗。")
                except PlaywrightTimeoutError:
                    print("LOG: 未检测到广告弹窗。")

                final_response = None
                log_time("步骤 2 - 应用筛选条件...")
                effective_publish_option = ""
                if is_cold_start:
                    log_time("冷启动模式：跳过新发布排序，使用默认综合排序以获取更广泛的数据")
                else:
                    effective_publish_option = new_publish_option if new_publish_option else "最新"
                    log_time(f"增量模式：强制启用新发布排序 → {effective_publish_option}")
                if effective_publish_option:
                    try:
                        await page.click("text=新发布")
                        await random_sleep(1, 2)
                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.click(f"text={effective_publish_option}")
                            await random_sleep(2, 4)
                        final_response = await response_info.value
                    except PlaywrightTimeoutError:
                        log_time(
                            f"新发布筛选 '{effective_publish_option}' 请求超时，继续执行。"
                        )
                    except Exception as e:
                        print(f"LOG: 应用新发布筛选失败: {e}")

                if personal_only:
                    async with page.expect_response(
                        is_search_results_response, timeout=20000
                    ) as response_info:
                        await page.click("text=个人闲置")
                        # --- 修改: 将固定等待改为随机等待，并加长 ---
                        await random_sleep(2, 4)  # 原来是 asyncio.sleep(5)
                    final_response = await response_info.value

                if free_shipping:
                    try:
                        async with page.expect_response(
                            is_search_results_response, timeout=20000
                        ) as response_info:
                            await page.click("text=包邮")
                            await random_sleep(2, 4)
                        final_response = await response_info.value
                    except PlaywrightTimeoutError:
                        log_time("包邮筛选请求超时，继续执行。")
                    except Exception as e:
                        print(f"LOG: 应用包邮筛选失败: {e}")

                if region_filter:
                    try:
                        area_trigger = page.get_by_text("区域", exact=True)
                        if await area_trigger.count():
                            await area_trigger.first.click()
                            await random_sleep(1.5, 2)
                            popover_candidates = page.locator("div.ant-popover")
                            popover = popover_candidates.filter(
                                has=page.locator(
                                    ".areaWrap--FaZHsn8E, [class*='areaWrap']"
                                )
                            ).last
                            if not await popover.count():
                                popover = popover_candidates.filter(
                                    has=page.get_by_text("重新定位")
                                ).last
                            if not await popover.count():
                                popover = popover_candidates.filter(
                                    has=page.get_by_text("查看")
                                ).last
                            if not await popover.count():
                                print("LOG: 未找到区域弹窗，跳过区域筛选。")
                                raise PlaywrightTimeoutError("region-popover-not-found")
                            await popover.wait_for(state="visible", timeout=5000)

                            # 列表容器：第一层 children 即省/市/区三列，不再强依赖具体类名，提升鲁棒性
                            area_wrap = popover.locator(
                                ".areaWrap--FaZHsn8E, [class*='areaWrap']"
                            ).first
                            await area_wrap.wait_for(state="visible", timeout=3000)
                            columns = area_wrap.locator(":scope > div")
                            col_prov = columns.nth(0)
                            col_city = columns.nth(1)
                            col_dist = columns.nth(2)

                            region_parts = [
                                p.strip() for p in region_filter.split("/") if p.strip()
                            ]

                            async def _click_in_column(
                                column_locator, text_value: str, desc: str
                            ) -> None:
                                option = column_locator.locator(
                                    ".provItem--QAdOx8nD", has_text=text_value
                                ).first
                                if await option.count():
                                    await option.click()
                                    await random_sleep(1.5, 2)
                                    try:
                                        await option.wait_for(
                                            state="attached", timeout=1500
                                        )
                                        await option.wait_for(
                                            state="visible", timeout=1500
                                        )
                                    except PlaywrightTimeoutError:
                                        pass
                                else:
                                    print(f"LOG: 未找到{desc} '{text_value}'，跳过。")

                            if len(region_parts) >= 1:
                                await _click_in_column(
                                    col_prov, region_parts[0], "省份"
                                )
                                await random_sleep(1, 2)
                            if len(region_parts) >= 2:
                                await _click_in_column(
                                    col_city, region_parts[1], "城市"
                                )
                                await random_sleep(1, 2)
                            if len(region_parts) >= 3:
                                await _click_in_column(
                                    col_dist, region_parts[2], "区/县"
                                )
                                await random_sleep(1, 2)

                            search_btn = popover.locator(
                                "div.searchBtn--Ic6RKcAb"
                            ).first
                            if await search_btn.count():
                                try:
                                    async with page.expect_response(
                                        is_search_results_response,
                                        timeout=20000,
                                    ) as response_info:
                                        await search_btn.click()
                                        await random_sleep(2, 3)
                                    final_response = await response_info.value
                                except PlaywrightTimeoutError:
                                    log_time("区域筛选提交超时，继续执行。")
                            else:
                                print(
                                    "LOG: 未找到区域弹窗的“查看XX件宝贝”按钮，跳过提交。"
                                )
                        else:
                            print("LOG: 未找到区域筛选触发器。")
                    except PlaywrightTimeoutError:
                        log_time(f"区域筛选 '{region_filter}' 请求超时，继续执行。")
                    except Exception as e:
                        print(f"LOG: 应用区域筛选 '{region_filter}' 失败: {e}")

                log_time("所有筛选已完成，开始处理商品列表...")

                current_response = (
                    final_response
                    if final_response and final_response.ok
                    else initial_response
                )
                for page_num in range(1, effective_max_pages + 1):
                    if stop_scraping:
                        break
                    log_time(f"开始处理第 {page_num}/{effective_max_pages} 页 ...")

                    if page_num > 1:
                        page_advance_result = await advance_search_page(
                            page=page,
                            page_num=page_num,
                        )
                        if not page_advance_result.advanced:
                            break
                        current_response = page_advance_result.response

                    if not (current_response and current_response.ok):
                        log_time(f"第 {page_num} 页响应无效，跳过。")
                        continue

                    basic_items = await _parse_search_results_json(
                        await current_response.json(), f"第 {page_num} 页"
                    )
                    if not basic_items:
                        break

                    if min_price or max_price:
                        price_floor = float(min_price) * 0.7 if min_price else 0
                        price_ceil = float(max_price) * 1.3 if max_price else float("inf")
                        before_price_filter = len(basic_items)

                        def _extract_price(item: dict) -> float:
                            raw = str(item.get("当前售价") or "0")
                            raw = raw.replace("¥", "").replace(",", "").strip()
                            try:
                                return float(raw)
                            except (ValueError, TypeError):
                                return 0.0

                        basic_items = [
                            item for item in basic_items
                            if price_floor <= _extract_price(item) <= price_ceil
                        ]
                        price_filtered = before_price_filter - len(basic_items)
                        if price_filtered > 0:
                            log_time(
                                f"[价格过滤] 过滤掉 {price_filtered} 条价格不在"
                                f" {price_floor:.0f}-{price_ceil:.0f} 范围的商品"
                            )
                        if not basic_items:
                            continue

                    filtered_basic_items = []
                    filtered_out_count = 0
                    for item in basic_items:
                        is_match, mismatch_reason = match_task_item(task_config, item)
                        if not is_match:
                            filtered_out_count += 1
                            log_time(
                                f"[硬过滤] 跳过候选商品 '{str(item.get('商品标题') or '')[:40]}'：{mismatch_reason}"
                            )
                            continue
                        filtered_basic_items.append(item)
                    if filtered_out_count:
                        log_time(
                            f"[硬过滤] 第 {page_num} 页过滤掉 {filtered_out_count} 条不匹配结果，保留 {len(filtered_basic_items)} 条。"
                        )
                    basic_items = filtered_basic_items
                    if not basic_items:
                        log_time(f"第 {page_num} 页过滤后无有效候选，继续下一页。")
                        continue
                    historical_snapshots.extend(
                        record_market_snapshots(
                            keyword=keyword,
                            task_name=task_config.get("task_name", "Untitled Task"),
                            items=basic_items,
                            run_id=history_run_id,
                            snapshot_time=datetime.now().isoformat(),
                            seen_item_ids=history_seen_item_ids,
                        )
                    )

                    total_items_on_page = len(basic_items)
                    page_new_count = 0
                    page_seen_count = 0
                    for i, item_data in enumerate(basic_items, 1):
                        if debug_limit > 0 and processed_item_count >= debug_limit:
                            log_time(
                                f"已达到调试上限 ({debug_limit})，停止获取新商品。"
                            )
                            stop_scraping = True
                            break

                        unique_key = get_link_unique_key(item_data["商品链接"])
                        if unique_key in processed_links:
                            page_seen_count += 1
                            log_time(
                                f"[页内进度 {i}/{total_items_on_page}] 商品 '{item_data['商品标题'][:20]}...' 已存在，跳过。"
                            )
                            continue
                        page_new_count += 1

                        log_time(
                            f"[页内进度 {i}/{total_items_on_page}] 发现新商品，获取详情: {item_data['商品标题'][:30]}..."
                        )
                        # --- 修改: 访问详情页前的等待时间，模拟用户在列表页上看了一会儿 ---
                        await random_sleep(2, 4)  # 原来是 (2, 4)

                        detail_page = await context.new_page()
                        try:
                            async with detail_page.expect_response(
                                lambda r: DETAIL_API_URL_PATTERN in r.url, timeout=25000
                            ) as detail_info:
                                await detail_page.goto(
                                    item_data["商品链接"],
                                    wait_until="domcontentloaded",
                                    timeout=25000,
                                )

                            detail_response = await detail_info.value
                            if detail_response.ok:
                                detail_json = await detail_response.json()

                                ret_string = str(
                                    await safe_get(detail_json, "ret", default=[])
                                )
                                if "FAIL_SYS_USER_VALIDATE" in ret_string:
                                    print(
                                        "\n==================== CRITICAL BLOCK DETECTED ===================="
                                    )
                                    print(
                                        "检测到闲鱼反爬虫验证 (FAIL_SYS_USER_VALIDATE)，程序将终止。"
                                    )
                                    long_sleep_duration = random.randint(3, 60)
                                    print(
                                        f"为避免账户风险，将执行一次长时间休眠 ({long_sleep_duration} 秒) 后再退出..."
                                    )
                                    await asyncio.sleep(long_sleep_duration)
                                    print("长时间休眠结束，现在将安全退出。")
                                    print(
                                        "==================================================================="
                                    )
                                    raise RiskControlError("FAIL_SYS_USER_VALIDATE")

                                # 解析商品详情数据并更新 item_data
                                item_do = await safe_get(
                                    detail_json, "data", "itemDO", default={}
                                )
                                seller_do = await safe_get(
                                    detail_json, "data", "sellerDO", default={}
                                )

                                reg_days_raw = await safe_get(
                                    seller_do, "userRegDay", default=0
                                )
                                registration_duration_text = format_registration_days(
                                    reg_days_raw
                                )

                                # --- START: 新增代码块 ---

                                # 1. 提取卖家的芝麻信用信息
                                zhima_credit_text = await safe_get(
                                    seller_do, "zhimaLevelInfo", "levelName"
                                )

                                # 2. 提取该商品的完整图片列表
                                image_infos = await safe_get(
                                    item_do, "imageInfos", default=[]
                                )
                                if image_infos:
                                    # 使用列表推导式获取所有有效的图片URL
                                    all_image_urls = [
                                        img.get("url")
                                        for img in image_infos
                                        if img.get("url")
                                    ]
                                    if all_image_urls:
                                        # 用新的字段存储图片列表，替换掉旧的单个链接
                                        item_data["商品图片列表"] = all_image_urls
                                        # (可选) 仍然保留主图链接，以防万一
                                        item_data["商品主图链接"] = all_image_urls[0]

                                # --- END: 新增代码块 ---
                                item_data["“想要”人数"] = await safe_get(
                                    item_do,
                                    "wantCnt",
                                    default=item_data.get("“想要”人数", "NaN"),
                                )
                                item_data["浏览量"] = await safe_get(
                                    item_do, "browseCnt", default="-"
                                )

                                detail_desc = await safe_get(
                                    item_do, "desc", default=""
                                )
                                if detail_desc:
                                    item_data["商品描述"] = detail_desc

                                props_list = await safe_get(
                                    item_do, "newItemProperties", default=[]
                                )
                                if not props_list:
                                    props_list = await safe_get(
                                        item_do, "itemProperties", default=[]
                                    )
                                if props_list and isinstance(props_list, list):
                                    structured_attrs = {}
                                    for prop in props_list:
                                        if not isinstance(prop, dict):
                                            continue
                                        label = (
                                            prop.get("label")
                                            or prop.get("key")
                                            or prop.get("name")
                                            or ""
                                        ).strip()
                                        value = (
                                            prop.get("value")
                                            or prop.get("text")
                                            or prop.get("content")
                                            or ""
                                        ).strip()
                                        if label and value:
                                            structured_attrs[label] = value
                                    if structured_attrs:
                                        item_data["商品属性"] = structured_attrs

                                is_final_match, final_mismatch_reason = match_task_item(
                                    task_config, item_data
                                )
                                if not is_final_match:
                                    log_time(
                                        f"[硬过滤] 详情校验未通过，跳过 '{item_data['商品标题'][:40]}'：{final_mismatch_reason}"
                                    )
                                    continue

                                user_id = await safe_get(seller_do, "sellerId")

                                # 构建基础记录
                                final_record = {
                                    "爬取时间": datetime.now().isoformat(),
                                    "搜索关键字": keyword,
                                    "任务名称": task_config.get(
                                        "task_name", "Untitled Task"
                                    ),
                                    "商品信息": item_data,
                                    "卖家信息": {},
                                }
                                price_reference = build_market_reference(
                                    keyword=keyword,
                                    item=item_data,
                                    current_market_items=basic_items,
                                    historical_snapshots=historical_snapshots,
                                )
                                final_record["价格参考"] = price_reference
                                final_record["price_insight"] = price_reference.get(
                                    "本商品价格位置", {}
                                )

                                analysis_dispatcher.submit(
                                    ItemAnalysisJob(
                                        keyword=keyword,
                                        task_name=task_config.get(
                                            "task_name", "Untitled Task"
                                        ),
                                        decision_mode=decision_mode,
                                        analyze_images=analyze_images,
                                        prompt_text=ai_prompt_text,
                                        keyword_rules=tuple(keyword_rules or []),
                                        final_record=final_record,
                                        seller_id=str(user_id) if user_id else None,
                                        zhima_credit_text=zhima_credit_text,
                                        registration_duration_text=registration_duration_text,
                                    )
                                )

                                processed_links.add(unique_key)
                                processed_item_count += 1
                                log_time(
                                    f"商品已提交后台分析。累计处理 {processed_item_count} 个新商品。"
                                )

                                # --- 修改: 增加单个商品处理后的主要延迟 ---
                                log_time(
                                    "[反爬] 执行一次主要的随机延迟以模拟用户浏览间隔..."
                                )
                                await random_sleep(5, 10)
                            else:
                                print(
                                    f"   错误: 获取商品详情API响应失败，状态码: {detail_response.status}"
                                )
                                if AI_DEBUG_MODE:
                                    print(
                                        f"--- [DETAIL DEBUG] FAILED RESPONSE from {item_data['商品链接']} ---"
                                    )
                                    try:
                                        print(await detail_response.text())
                                    except Exception as e:
                                        print(f"无法读取响应内容: {e}")
                                    print(
                                        "----------------------------------------------------"
                                    )

                        except PlaywrightTimeoutError:
                            print(f"   错误: 访问商品详情页或等待API响应超时。")
                        except Exception as e:
                            print(f"   错误: 处理商品详情时发生未知错误: {e}")
                        finally:
                            await detail_page.close()
                            # --- 修改: 增加关闭页面后的短暂整理时间 ---
                            await random_sleep(2, 4)  # 原来是 (1, 2.5)

                    if not is_cold_start and page_new_count == 0 and page_seen_count > 0:
                        log_time(
                            f"增量模式：第 {page_num} 页全部 {page_seen_count} 个商品已扫描过，提前结束翻页。"
                        )
                        stop_scraping = True

                    if not stop_scraping and page_num < effective_max_pages:
                        print(
                            f"--- 第 {page_num} 页处理完毕（新 {page_new_count} / 旧 {page_seen_count}），准备翻到下一页。执行页面间休息... ---"
                        )
                        await random_sleep(5, 10)

            except PlaywrightTimeoutError as e:
                if _is_login_url(page.url):
                    if RUN_HEADLESS:
                        recovered = await _interactive_recover_session(
                            state_file,
                            task_name=task_name,
                            keyword=keyword,
                            target_url=search_url,
                            reason=f"超时后跳转到登录页: {page.url}",
                            proxy_server=proxy_server,
                        )
                        if recovered:
                            raise InteractiveRecoveryRequested("已完成交互登录，准备重新执行任务。") from e
                        raise LoginRequiredError(
                            f"Login required: redirected to {page.url} (交互恢复未完成)"
                        ) from e
                    logged_in = await _wait_for_login(page, context, state_file)
                    if not logged_in:
                        raise LoginRequiredError("扫码登录超时，任务中止。") from e
                    # 登录成功但当前页面状态已乱，抛异常触发重试
                    raise LoginRequiredError("登录成功，需要重新执行任务。") from e
                print(f"\n操作超时错误: 页面元素或网络响应未在规定时间内出现。\n{e}")
                raise
            except asyncio.CancelledError:
                log_time("收到取消信号，正在终止当前爬虫任务...")
                raise
            except Exception as e:
                if type(e).__name__ == "TargetClosedError":
                    log_time("浏览器已关闭，忽略后续异常（可能是任务被停止）。")
                    return processed_item_count
                if "passport.goofish.com" in str(e):
                    if RUN_HEADLESS:
                        recovered = await _interactive_recover_session(
                            state_file,
                            task_name=task_name,
                            keyword=keyword,
                            target_url=search_url,
                            reason=f"异常中触发 passport 登录流: {e}",
                            proxy_server=proxy_server,
                        )
                        if recovered:
                            raise InteractiveRecoveryRequested("已完成交互登录，准备重新执行任务。") from e
                    else:
                        logged_in = await _wait_for_login(page, context, state_file)
                        if logged_in:
                            raise LoginRequiredError("登录成功，需要重新执行任务。") from e
                    raise LoginRequiredError(
                        f"Login required: redirected to passport flow ({e})"
                    ) from e
                print(f"\n爬取过程中发生未知错误: {e}")
                raise
            finally:
                if analysis_dispatcher is not None:
                    log_time("等待后台分析任务完成...")
                    await analysis_dispatcher.join()
                log_time("任务执行完毕，浏览器将在5秒后自动关闭...")
                await asyncio.sleep(5)
                if debug_limit:
                    await asyncio.get_running_loop().run_in_executor(
                        None, input, "按回车键关闭浏览器..."
                    )
                await browser.close()

        return processed_item_count

    processed_item_count = 0
    attempt_limit = max(
        rotation_settings["account_retry_limit"],
        rotation_settings["proxy_retry_limit"],
        1,
    )
    last_error = ""
    last_state_path: Optional[str] = None

    # If this task is already in a paused state, skip immediately.
    task_name_for_guard = task_config.get("task_name", "未命名任务")
    pause_cookie_path = None
    if (
        isinstance(task_config.get("account_state_file"), str)
        and task_config.get("account_state_file").strip()
    ):
        pause_cookie_path = task_config.get("account_state_file").strip()
    elif os.path.exists(STATE_FILE):
        pause_cookie_path = STATE_FILE

    decision = FAILURE_GUARD.should_skip_start(
        task_name_for_guard, cookie_path=pause_cookie_path
    )
    if decision.skip:
        print(
            f"[FailureGuard] 任务 '{task_name_for_guard}' 已暂停重试 (连续失败 {decision.consecutive_failures}/{FAILURE_GUARD.threshold})"
        )
        if decision.should_notify:
            try:
                await send_ntfy_notification(
                    {
                        "商品标题": f"[任务暂停] {task_name_for_guard}",
                        "当前售价": "N/A",
                        "商品链接": "#",
                    },
                    "任务处于暂停状态，将跳过执行。\n"
                    f"原因: {decision.reason}\n"
                    f"连续失败: {decision.consecutive_failures}/{FAILURE_GUARD.threshold}\n"
                    f"暂停到: {decision.paused_until.strftime('%Y-%m-%d %H:%M:%S') if decision.paused_until else 'N/A'}\n"
                    "修复方法: 更新登录态/cookies文件后会自动恢复。",
                )
            except Exception as e:
                print(f"发送任务暂停通知失败: {e}")

        cleanup_task_images(task_config.get("task_name", "default"))
        return 0

    interactive_recovery_budget = 2
    max_attempts = attempt_limit + interactive_recovery_budget
    retry_same_assignment = False
    attempt = 1
    while attempt <= max_attempts:
        if attempt == 1:
            selected_account = _select_account()
            selected_proxy = _select_proxy()
        elif retry_same_assignment:
            retry_same_assignment = False
        else:
            if (
                rotation_settings["account_enabled"]
                and rotation_settings["account_mode"] == "on_failure"
            ):
                account_pool.mark_bad(selected_account, last_error)
                selected_account = _select_account(force_new=True)
            if (
                rotation_settings["proxy_enabled"]
                and rotation_settings["proxy_mode"] == "on_failure"
            ):
                proxy_pool.mark_bad(selected_proxy, last_error)
                selected_proxy = _select_proxy(force_new=True)

        if rotation_settings["account_enabled"] and not selected_account:
            last_error = "未找到可用的登录状态文件，无法继续执行任务。"
            print(last_error)
            break
        if not rotation_settings["account_enabled"] and not selected_account:
            last_error = "未找到可用的登录状态文件，无法继续执行任务。"
            print(last_error)
            break
        if rotation_settings["proxy_enabled"] and not selected_proxy:
            last_error = "未找到可用的代理地址，无法继续执行任务。"
            print(last_error)
            break

        state_path = selected_account.value if selected_account else STATE_FILE
        last_state_path = state_path
        proxy_server = selected_proxy.value if selected_proxy else None
        if rotation_settings["account_enabled"]:
            print(f"账号轮换：使用登录状态 {state_path}")
        if rotation_settings["proxy_enabled"] and proxy_server:
            print(f"IP 轮换：使用代理 {proxy_server}")

        try:
            processed_item_count += await _run_scrape_attempt(state_path, proxy_server)
            last_error = ""
            FAILURE_GUARD.record_success(task_name_for_guard)
            break
        except InteractiveRecoveryRequested as e:
            print(f"交互恢复完成: {e}")
            last_error = ""
            if interactive_recovery_budget > 0:
                interactive_recovery_budget -= 1
                retry_same_assignment = True
                attempt += 1
                continue
            last_error = str(e)
            break
        except LoginRequiredError as e:
            last_error = str(e)
            print(f"检测到登录失效/重定向: {e}")
            break
        except RiskControlError as e:
            last_error = str(e)
            print(f"检测到风控或验证触发: {e}")
            # 风控验证通常不是简单轮换能解决的，避免无意义重试。
            break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"本次尝试失败: {last_error}")
            if attempt < max_attempts:
                print("将尝试轮换账号/IP 后重试...")
        attempt += 1

    if last_error:
        await _notify_task_failure(task_config, last_error, cookie_path=last_state_path)

    # 清理任务图片目录
    cleanup_task_images(task_config.get("task_name", "default"))

    return processed_item_count

"""
设置管理路由
"""
import asyncio
import os
import time
from typing import Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.dependencies import get_execution_queue_service, get_process_service
from src.infrastructure.config.env_manager import env_manager
from src.infrastructure.config.settings import (
    AISettings,
    reload_settings,
    scraper_settings,
)
from src.services.ai_request_compat import (
    CHAT_COMPLETIONS_API_MODE,
    RESPONSES_API_MODE,
    build_ai_request_params,
    create_ai_response_sync,
    is_chat_completions_api_unsupported_error,
    is_responses_api_unsupported_error,
    is_stream_required_by_gateway,
    is_stream_required_error,
    mark_stream_required,
)
from src.services.ai_response_parser import extract_ai_response_content
from src.services.ai_base_url import normalize_openai_base_url
from src.services.notification_config_service import (
    NotificationSettingsValidationError,
    build_configured_channels,
    build_notification_settings_response,
    build_notification_status_flags,
    load_notification_settings,
    model_dump,
    prepare_notification_test_settings,
    prepare_notification_settings_update,
)
from src.services.notification_service import build_notification_service
from src.services.process_service import ProcessService
from src.services.execution_queue_service import ExecutionQueueService


router = APIRouter(prefix="/api/settings", tags=["settings"])
AI_TEST_PROMPT = "Reply with OK only."
AI_TEST_MAX_OUTPUT_TOKENS = 32
AI_MODEL_PROBE_CACHE_TTL_SECONDS = 600
AI_MODEL_PROBE_CONCURRENCY = 2

_AI_MODEL_PROBE_CACHE: dict[tuple[str, str], dict] = {}


def _reload_env() -> None:
    load_dotenv(dotenv_path=env_manager.env_file, override=True)
    reload_settings()


def _env_bool(key: str, default: bool = False) -> bool:
    value = env_manager.get_value(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(key: str, default: int) -> int:
    value = env_manager.get_value(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _normalize_bool_value(value: bool) -> str:
    return "true" if value else "false"


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _resolve_ai_value(submitted: Optional[str], key: str) -> str:
    value = (submitted or "").strip()
    if value:
        return normalize_openai_base_url(value) if key == "OPENAI_BASE_URL" else value
    stored = env_manager.get_value(key, "")
    text = (stored or "").strip()
    return normalize_openai_base_url(text) if key == "OPENAI_BASE_URL" else text


def _build_model_catalog_urls(base_url: str) -> list[str]:
    normalized = base_url.rstrip("/")
    candidates = [f"{normalized}/models"]
    path = (urlparse(normalized).path or "").rstrip("/")
    if not path.endswith("/v1"):
        candidates.append(f"{normalized}/v1/models")

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def _extract_model_ids(payload: object) -> list[str]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            entries = payload["data"]
        elif isinstance(payload.get("models"), list):
            entries = payload["models"]
        elif isinstance(payload.get("result"), list):
            entries = payload["result"]
        else:
            entries = []
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []

    model_ids: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            candidate = entry.strip()
        elif isinstance(entry, dict):
            candidate = str(
                entry.get("id")
                or entry.get("model")
                or entry.get("name")
                or ""
            ).strip()
        else:
            candidate = ""

        if candidate and candidate not in model_ids:
            model_ids.append(candidate)
    return model_ids


def _cache_probe_result(base_url: str, model: str, result: dict) -> None:
    _AI_MODEL_PROBE_CACHE[(base_url, model)] = {
        **result,
        "cached_at": time.time(),
    }


def _get_cached_probe_result(base_url: str, model: str) -> dict | None:
    cached = _AI_MODEL_PROBE_CACHE.get((base_url, model))
    if not cached:
        return None
    if time.time() - float(cached.get("cached_at", 0)) > AI_MODEL_PROBE_CACHE_TTL_SECONDS:
        _AI_MODEL_PROBE_CACHE.pop((base_url, model), None)
        return None
    return dict(cached)


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "too many requests" in msg or "ratelimit" in msg


def _run_ai_test_request(
    *,
    api_key: str,
    base_url: str,
    proxy_url: str,
    model_name: str,
) -> dict:
    import time
    from openai import OpenAI

    use_stream = is_stream_required_by_gateway()
    client_params = {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": httpx.Timeout(45.0),
    }
    if proxy_url:
        client_params["http_client"] = httpx.Client(proxy=proxy_url)

    client = OpenAI(**client_params)
    messages = [{"role": "user", "content": AI_TEST_PROMPT}]
    api_mode = CHAT_COMPLETIONS_API_MODE
    rate_limit_retries = 0

    while True:
        try:
            response = create_ai_response_sync(
                client,
                api_mode,
                build_ai_request_params(
                    api_mode,
                    model=model_name,
                    messages=messages,
                    max_output_tokens=AI_TEST_MAX_OUTPUT_TOKENS,
                    stream=use_stream,
                ),
            )
            return {
                "success": True,
                "message": "AI模型连接测试成功！",
                "response": extract_ai_response_content(response),
            }
        except Exception as exc:
            if api_mode == CHAT_COMPLETIONS_API_MODE and is_stream_required_error(exc) and not use_stream:
                use_stream = True
                mark_stream_required()
                continue
            if api_mode == CHAT_COMPLETIONS_API_MODE and is_chat_completions_api_unsupported_error(exc):
                api_mode = RESPONSES_API_MODE
                continue
            if _is_rate_limit_error(exc) and rate_limit_retries < 2:
                rate_limit_retries += 1
                time.sleep(2 * rate_limit_retries)
                continue
            return {
                "success": False,
                "message": f"AI模型连接测试失败: {exc}",
            }


class NotificationSettingsModel(BaseModel):
    """通知设置模型"""

    NTFY_TOPIC_URL: Optional[str] = None
    GOTIFY_URL: Optional[str] = None
    GOTIFY_TOKEN: Optional[str] = None
    BARK_URL: Optional[str] = None
    WX_BOT_URL: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_API_BASE_URL: Optional[str] = None
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_METHOD: Optional[str] = None
    WEBHOOK_HEADERS: Optional[str] = None
    WEBHOOK_CONTENT_TYPE: Optional[str] = None
    WEBHOOK_QUERY_PARAMETERS: Optional[str] = None
    WEBHOOK_BODY: Optional[str] = None
    PCURL_TO_MOBILE: Optional[bool] = None


class NotificationTestRequest(BaseModel):
    """通知测试请求"""

    channel: Optional[str] = None
    settings: NotificationSettingsModel = Field(default_factory=NotificationSettingsModel)


class AISettingsModel(BaseModel):
    """AI设置模型"""

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    OPENAI_MODEL_NAME: Optional[str] = None
    SKIP_AI_ANALYSIS: Optional[bool] = None
    PROXY_URL: Optional[str] = None


class AIModelListResponse(BaseModel):
    models: list[str]
    source_url: str


class AIModelProbeRequest(BaseModel):
    models: list[str] = Field(default_factory=list)
    force_refresh: bool = False
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    PROXY_URL: Optional[str] = None


class AIModelProbeItem(BaseModel):
    model: str
    available: bool
    message: str
    checked_at: str
    cached: bool = False


class AIModelProbeResponse(BaseModel):
    items: list[AIModelProbeItem]


class RotationSettingsModel(BaseModel):
    ACCOUNT_ROTATION_ENABLED: Optional[bool] = None
    ACCOUNT_ROTATION_MODE: Optional[str] = None
    ACCOUNT_ROTATION_RETRY_LIMIT: Optional[int] = None
    ACCOUNT_BLACKLIST_TTL: Optional[int] = None
    ACCOUNT_STATE_DIR: Optional[str] = None
    PROXY_ROTATION_ENABLED: Optional[bool] = None
    PROXY_ROTATION_MODE: Optional[str] = None
    PROXY_POOL: Optional[str] = None
    PROXY_ROTATION_RETRY_LIMIT: Optional[int] = None
    PROXY_BLACKLIST_TTL: Optional[int] = None


@router.get("/notifications")
async def get_notification_settings():
    return build_notification_settings_response(load_notification_settings())


@router.put("/notifications")
async def update_notification_settings(settings: NotificationSettingsModel):
    try:
        updates, deletions, merged_settings = prepare_notification_settings_update(
            model_dump(settings, exclude_unset=True),
            load_notification_settings(),
        )
    except NotificationSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    success = env_manager.apply_changes(updates=updates, deletions=deletions)
    if not success:
        raise HTTPException(status_code=500, detail="更新通知设置失败")

    _reload_env()
    return {
        "message": "通知设置已成功更新",
        "configured_channels": build_configured_channels(merged_settings),
    }


@router.post("/notifications/test")
async def test_notification_settings(payload: NotificationTestRequest):
    try:
        merged_settings = prepare_notification_test_settings(
            model_dump(payload.settings, exclude_unset=True),
            load_notification_settings(),
            channel=payload.channel,
        )
    except NotificationSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    service = build_notification_service(merged_settings)
    if not service.clients:
        if payload.channel:
            raise HTTPException(
                status_code=422,
                detail=f"渠道 {payload.channel} 未配置或不受支持",
            )
        raise HTTPException(status_code=422, detail="请至少配置一个可用的通知渠道")

    results = await service.send_test_notification()
    if payload.channel:
        if payload.channel not in results:
            raise HTTPException(
                status_code=422,
                detail=f"渠道 {payload.channel} 未配置或不受支持",
            )
        results = {payload.channel: results[payload.channel]}

    return {
        "message": "测试通知已执行",
        "results": results,
    }


@router.get("/rotation")
async def get_rotation_settings():
    return {
        "ACCOUNT_ROTATION_ENABLED": _env_bool("ACCOUNT_ROTATION_ENABLED", False),
        "ACCOUNT_ROTATION_MODE": env_manager.get_value("ACCOUNT_ROTATION_MODE", "per_task"),
        "ACCOUNT_ROTATION_RETRY_LIMIT": _env_int("ACCOUNT_ROTATION_RETRY_LIMIT", 2),
        "ACCOUNT_BLACKLIST_TTL": _env_int("ACCOUNT_BLACKLIST_TTL", 300),
        "ACCOUNT_STATE_DIR": env_manager.get_value("ACCOUNT_STATE_DIR", "state"),
        "PROXY_ROTATION_ENABLED": _env_bool("PROXY_ROTATION_ENABLED", False),
        "PROXY_ROTATION_MODE": env_manager.get_value("PROXY_ROTATION_MODE", "per_task"),
        "PROXY_POOL": env_manager.get_value("PROXY_POOL", ""),
        "PROXY_ROTATION_RETRY_LIMIT": _env_int("PROXY_ROTATION_RETRY_LIMIT", 2),
        "PROXY_BLACKLIST_TTL": _env_int("PROXY_BLACKLIST_TTL", 300),
    }


@router.put("/rotation")
async def update_rotation_settings(settings: RotationSettingsModel):
    updates = {}
    payload = model_dump(settings, exclude_unset=True)
    for key, value in payload.items():
        if isinstance(value, bool):
            updates[key] = _normalize_bool_value(value)
        else:
            updates[key] = str(value)
    success = env_manager.update_values(updates)
    if not success:
        raise HTTPException(status_code=500, detail="更新轮换设置失败")
    _reload_env()
    return {"message": "轮换设置已成功更新"}


@router.get("/status")
async def get_system_status(
    process_service: ProcessService = Depends(get_process_service),
    execution_queue_service: ExecutionQueueService = Depends(get_execution_queue_service),
):
    state_file = "xianyu_state.json"
    login_state_exists = os.path.exists(state_file)
    env_file_exists = os.path.exists(env_manager.env_file)
    openai_api_key = env_manager.get_value("OPENAI_API_KEY", "")
    openai_base_url = env_manager.get_value("OPENAI_BASE_URL", "")
    openai_model_name = env_manager.get_value("OPENAI_MODEL_NAME", "")
    ai_settings = AISettings()
    notification_settings = load_notification_settings()
    running_task_ids = [
        task_id
        for task_id, process in process_service.processes.items()
        if process and process.returncode is None
    ]

    return {
        "ai_configured": ai_settings.is_configured(),
        "notification_configured": notification_settings.has_any_notification_enabled(),
        "headless_mode": scraper_settings.run_headless,
        "running_in_docker": scraper_settings.running_in_docker,
        "scraper_running": len(running_task_ids) > 0,
        "running_task_ids": running_task_ids,
        "execution_queue": execution_queue_service.snapshot(),
        "login_state_file": {
            "exists": login_state_exists,
            "path": state_file,
        },
        "env_file": {
            "exists": env_file_exists,
            "openai_api_key_set": bool(openai_api_key),
            "openai_base_url_set": bool(openai_base_url),
            "openai_model_name_set": bool(openai_model_name),
            **build_notification_status_flags(notification_settings),
        },
        "configured_notification_channels": build_configured_channels(notification_settings),
    }


@router.get("/ai")
async def get_ai_settings():
    return {
        "OPENAI_BASE_URL": normalize_openai_base_url(env_manager.get_value("OPENAI_BASE_URL", "")),
        "OPENAI_MODEL_NAME": env_manager.get_value("OPENAI_MODEL_NAME", ""),
        "SKIP_AI_ANALYSIS": env_manager.get_value("SKIP_AI_ANALYSIS", "false").lower() == "true",
        "PROXY_URL": env_manager.get_value("PROXY_URL", ""),
    }


@router.put("/ai")
async def update_ai_settings(settings: AISettingsModel):
    updates = {}
    if settings.OPENAI_API_KEY is not None:
        updates["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
    if settings.OPENAI_BASE_URL is not None:
        updates["OPENAI_BASE_URL"] = normalize_openai_base_url(settings.OPENAI_BASE_URL)
    if settings.OPENAI_MODEL_NAME is not None:
        updates["OPENAI_MODEL_NAME"] = settings.OPENAI_MODEL_NAME
    if settings.SKIP_AI_ANALYSIS is not None:
        updates["SKIP_AI_ANALYSIS"] = str(settings.SKIP_AI_ANALYSIS).lower()
    if settings.PROXY_URL is not None:
        updates["PROXY_URL"] = settings.PROXY_URL

    success = env_manager.update_values(updates)
    if not success:
        raise HTTPException(status_code=500, detail="更新AI设置失败")
    _reload_env()
    return {"message": "AI设置已成功更新"}


@router.post("/ai/test")
async def test_ai_settings(settings: dict):
    """测试AI模型设置是否有效"""
    api_key = _resolve_ai_value(settings.get("OPENAI_API_KEY"), "OPENAI_API_KEY")
    base_url = _resolve_ai_value(settings.get("OPENAI_BASE_URL"), "OPENAI_BASE_URL")
    proxy_url = _resolve_ai_value(settings.get("PROXY_URL"), "PROXY_URL")
    model_name = (
        str(settings.get("OPENAI_MODEL_NAME") or "").strip()
        or env_manager.get_value("OPENAI_MODEL_NAME", "")
    )
    return _run_ai_test_request(
        api_key=api_key,
        base_url=base_url,
        proxy_url=proxy_url,
        model_name=model_name,
    )


@router.post("/ai/models", response_model=AIModelListResponse)
async def list_ai_models(settings: AISettingsModel):
    api_key = _resolve_ai_value(settings.OPENAI_API_KEY, "OPENAI_API_KEY")
    base_url = _resolve_ai_value(settings.OPENAI_BASE_URL, "OPENAI_BASE_URL")
    proxy_url = _resolve_ai_value(settings.PROXY_URL, "PROXY_URL")

    if not base_url:
        raise HTTPException(status_code=422, detail="请先填写 API Base URL")
    if not api_key:
        raise HTTPException(status_code=422, detail="请先填写 API Key，或先保存已有配置")

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    tried_errors: list[str] = []

    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(20.0),
        proxy=proxy_url or None,
    ) as client:
        for url in _build_model_catalog_urls(base_url):
            try:
                response = await client.get(url)
                response.raise_for_status()
                models = sorted(_extract_model_ids(response.json()))
                if models:
                    return AIModelListResponse(models=models, source_url=url)
                tried_errors.append(f"{url}: 未返回可识别模型列表")
            except httpx.HTTPStatusError as exc:
                tried_errors.append(f"{url}: HTTP {exc.response.status_code}")
            except Exception as exc:
                tried_errors.append(f"{url}: {exc}")

    detail = "拉取模型列表失败，请检查 API Base URL、API Key 和代理设置"
    if tried_errors:
        detail = f"{detail}。尝试记录：{'；'.join(tried_errors[:3])}"
    raise HTTPException(status_code=502, detail=detail)


@router.post("/ai/models/probe", response_model=AIModelProbeResponse)
async def probe_ai_models(payload: AIModelProbeRequest):
    api_key = _resolve_ai_value(payload.OPENAI_API_KEY, "OPENAI_API_KEY")
    base_url = _resolve_ai_value(payload.OPENAI_BASE_URL, "OPENAI_BASE_URL")
    proxy_url = _resolve_ai_value(payload.PROXY_URL, "PROXY_URL")
    models = [str(model).strip() for model in payload.models if str(model).strip()]

    if not base_url:
        raise HTTPException(status_code=422, detail="请先填写 API Base URL")
    if not api_key:
        raise HTTPException(status_code=422, detail="请先填写 API Key，或先保存已有配置")
    if not models:
        raise HTTPException(status_code=422, detail="请至少提供一个待验证模型")

    semaphore = asyncio.Semaphore(AI_MODEL_PROBE_CONCURRENCY)

    async def _probe_one(model_name: str) -> AIModelProbeItem:
        if not payload.force_refresh:
            cached = _get_cached_probe_result(base_url, model_name)
            if cached:
                return AIModelProbeItem(
                    model=model_name,
                    available=bool(cached["available"]),
                    message=str(cached["message"]),
                    checked_at=str(cached["checked_at"]),
                    cached=True,
                )

        async with semaphore:
            result = await asyncio.to_thread(
                _run_ai_test_request,
                api_key=api_key,
                base_url=base_url,
                proxy_url=proxy_url,
                model_name=model_name,
            )
        item = {
            "available": bool(result["success"]),
            "message": str(result["message"]),
            "checked_at": _utc_timestamp(),
        }
        _cache_probe_result(base_url, model_name, item)
        return AIModelProbeItem(model=model_name, cached=False, **item)

    items = await asyncio.gather(*[_probe_one(model_name) for model_name in models])
    return AIModelProbeResponse(items=items)

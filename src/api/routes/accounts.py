"""
闲鱼账号管理路由
"""
import asyncio
import json
import logging
import os
import re

import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from src.services.browser_login_service import browser_login_service
from src.services.account_state_service import (
    AccountError,
    create_account_entry,
    delete_account_entry,
    list_account_entries,
    resolve_account,
    validate_display_name,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _raise_http(exc: AccountError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

_SENSITIVE_COOKIE_KEYS = re.compile(
    r"(cookie|token|session|auth|secret|password)", re.IGNORECASE,
)


class AccountCreate(BaseModel):
    name: str
    content: str


class AccountUpdate(BaseModel):
    content: str


class BrowserLoginStart(BaseModel):
    name: str
    set_as_default: bool = True


def _validate_json(content: str) -> dict | list:
    """Parse and validate JSON content with basic structure check."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="提供的内容不是有效的JSON格式。")
    if not isinstance(parsed, (dict, list)):
        raise HTTPException(status_code=400, detail="JSON 内容必须是对象或数组。")
    return parsed


def _mask_sensitive_values(data: dict | list | str) -> dict | list | str:
    """Mask sensitive cookie/session values in account data for API responses."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return data
    if isinstance(data, dict):
        masked = {}
        for k, v in data.items():
            if _SENSITIVE_COOKIE_KEYS.search(str(k)):
                masked[k] = "***" if v else v
            elif isinstance(v, (dict, list)):
                masked[k] = _mask_sensitive_values(v)
            else:
                masked[k] = v
        return masked
    if isinstance(data, list):
        return [_mask_sensitive_values(item) if isinstance(item, (dict, list)) else item for item in data]
    return data


@router.get("", response_model=List[dict])
async def list_accounts():
    return list_account_entries()


@router.get("/{name}", response_model=dict)
async def get_account(name: str):
    try:
        account_name, path = resolve_account(name)
    except AccountError as exc:
        _raise_http(exc)
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        content = await f.read()
    masked_content = _mask_sensitive_values(content)
    return {"name": account_name, "path": str(path), "content": masked_content}


@router.post("", response_model=dict)
async def create_account(data: AccountCreate):
    try:
        account_name = validate_display_name(data.name)
        _validate_json(data.content)
        _, path = create_account_entry(account_name)
    except AccountError as exc:
        _raise_http(exc)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(data.content)
    return {"message": "账号已添加", "name": account_name, "path": str(path)}


@router.put("/{name}", response_model=dict)
async def update_account(name: str, data: AccountUpdate):
    try:
        _validate_json(data.content)
        _, existing_path = resolve_account(name)
        account_name = validate_display_name(name)
        _, normalized_path = resolve_account(account_name)
    except AccountError as exc:
        _raise_http(exc)
    if str(existing_path) != str(normalized_path):
        logger.warning(
            "账号名 '%s' 规范化后路径变更: %s -> %s，写入原路径",
            name, existing_path, normalized_path,
        )
    write_path = existing_path
    async with aiofiles.open(write_path, "w", encoding="utf-8") as f:
        await f.write(data.content)
    return {"message": "账号已更新", "name": account_name, "path": str(write_path)}


@router.delete("/{name}", response_model=dict)
async def delete_account(name: str):
    try:
        path = delete_account_entry(name)
    except AccountError as exc:
        _raise_http(exc)
    try:
        await asyncio.to_thread(os.remove, path)
    except OSError as e:
        logger.warning("删除账号文件失败 %s: %s", path, e)
        raise HTTPException(status_code=500, detail="删除账号文件失败") from e
    return {"message": "账号已删除"}


@router.post("/browser-login", response_model=dict)
async def start_browser_login(data: BrowserLoginStart):
    try:
        account_name = validate_display_name(data.name)
    except AccountError as exc:
        _raise_http(exc)
    try:
        return await browser_login_service.start_job(
            account_name,
            set_as_default=data.set_as_default,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/browser-login/{job_id}", response_model=dict)
async def get_browser_login(job_id: str):
    try:
        return await browser_login_service.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="扫码登录任务不存在") from exc

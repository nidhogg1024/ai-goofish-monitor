"""
闲鱼账号管理路由
"""
import json
import os
import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from src.services.browser_login_service import browser_login_service
from src.services.account_state_service import (
    create_account_entry,
    delete_account_entry,
    list_account_entries,
    resolve_account,
    validate_display_name,
)


router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    name: str
    content: str


class AccountUpdate(BaseModel):
    content: str


class BrowserLoginStart(BaseModel):
    name: str
    set_as_default: bool = True


def _validate_json(content: str) -> None:
    try:
        json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="提供的内容不是有效的JSON格式。")


@router.get("", response_model=List[dict])
async def list_accounts():
    return list_account_entries()


@router.get("/{name}", response_model=dict)
async def get_account(name: str):
    account_name, path = resolve_account(name)
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        content = await f.read()
    return {"name": account_name, "path": str(path), "content": content}


@router.post("", response_model=dict)
async def create_account(data: AccountCreate):
    account_name = validate_display_name(data.name)
    _validate_json(data.content)
    _, path = create_account_entry(account_name)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(data.content)
    return {"message": "账号已添加", "name": account_name, "path": str(path)}


@router.put("/{name}", response_model=dict)
async def update_account(name: str, data: AccountUpdate):
    account_name = validate_display_name(name)
    _validate_json(data.content)
    _, path = resolve_account(account_name)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(data.content)
    return {"message": "账号已更新", "name": account_name, "path": str(path)}


@router.delete("/{name}", response_model=dict)
async def delete_account(name: str):
    path = delete_account_entry(name)
    os.remove(path)
    return {"message": "账号已删除"}


@router.post("/browser-login", response_model=dict)
async def start_browser_login(data: BrowserLoginStart):
    account_name = validate_display_name(data.name)
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

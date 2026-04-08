"""
登录状态管理路由
"""
import asyncio
import json
import logging
import os

import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.infrastructure.config.settings import scraper_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/login-state", tags=["login-state"])

STATE_FILE = scraper_settings.state_file


class LoginStateUpdate(BaseModel):
    """登录状态更新模型"""
    content: str


@router.post("", response_model=dict)
async def update_login_state(
    data: LoginStateUpdate,
):
    """接收前端发送的登录状态JSON字符串，并保存到 xianyu_state.json"""
    try:
        parsed = json.loads(data.content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="提供的内容不是有效的JSON格式。")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="登录状态必须是JSON对象。")

    try:
        async with aiofiles.open(STATE_FILE, 'w', encoding='utf-8') as f:
            await f.write(data.content)
        return {"message": f"登录状态文件 '{STATE_FILE}' 已成功更新。"}
    except Exception as e:
        logger.exception("写入登录状态文件时出错: %s", e)
        raise HTTPException(status_code=500, detail="写入登录状态文件时出错")


@router.delete("", response_model=dict)
async def delete_login_state():
    """删除登录状态文件"""
    if os.path.exists(STATE_FILE):
        try:
            await asyncio.to_thread(os.remove, STATE_FILE)
            return {"message": "登录状态文件已成功删除。"}
        except OSError as e:
            logger.warning("删除登录状态文件时出错: %s", e)
            raise HTTPException(status_code=500, detail="删除登录状态文件时出错")

    return {"message": "登录状态文件不存在，无需删除。"}

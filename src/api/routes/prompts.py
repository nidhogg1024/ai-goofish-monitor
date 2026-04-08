"""
Prompt 管理路由
"""
import asyncio
import logging
import os

import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompts", tags=["prompts"])

PROMPTS_DIR = "prompts"


class PromptUpdate(BaseModel):
    """Prompt 更新模型"""
    content: str


def _validate_prompt_path(filename: str) -> str:
    """Validate filename and return the safe resolved filepath."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")
    filepath = os.path.join(PROMPTS_DIR, filename)
    resolved = os.path.realpath(filepath)
    if not resolved.startswith(os.path.realpath(PROMPTS_DIR)):
        raise HTTPException(status_code=400, detail="无效的文件名")
    return filepath


@router.get("")
async def list_prompts():
    """列出所有 prompt 文件"""
    if not os.path.isdir(PROMPTS_DIR):
        return []
    entries = await asyncio.to_thread(os.listdir, PROMPTS_DIR)
    return [f for f in entries if f.endswith(".txt")]


@router.get("/{filename}")
async def get_prompt(filename: str):
    """获取 prompt 文件内容"""
    filepath = _validate_prompt_path(filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Prompt 文件未找到")

    async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
        content = await f.read()
    return {"filename": filename, "content": content}


@router.put("/{filename}")
async def update_prompt(
    filename: str,
    prompt_update: PromptUpdate,
):
    """更新 prompt 文件内容"""
    filepath = _validate_prompt_path(filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Prompt 文件未找到")

    try:
        async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
            await f.write(prompt_update.content)
        return {"message": f"Prompt 文件 '{filename}' 更新成功"}
    except Exception as e:
        logger.exception("写入 prompt 文件时出错: %s", e)
        raise HTTPException(status_code=500, detail="写入文件时出错")

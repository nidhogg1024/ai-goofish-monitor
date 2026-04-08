"""
批量任务生成执行器
"""
from __future__ import annotations

import logging
from string import Template
from typing import Any, Dict, List, Optional

from src.infrastructure.external.ai_client import AIClient
from src.services.ai_response_parser import parse_ai_response_json
from src.services.batch_generation_service import BatchGenerationService
from src.services.url_content_service import fetch_url_content

logger = logging.getLogger(__name__)

BATCH_INTENT_PROMPT = Template("""
你是"闲鱼监控任务批量配置助手"。你的职责是分析用户输入（可能是购物需求描述、装修清单、网页文章内容等），将其中的商品需求拆解成多个独立的闲鱼监控任务配置。

请严格只输出 JSON 数组，不要输出解释，不要输出 Markdown 代码块。

每个数组元素的格式：
{
  "task_name": "简短任务名（适合展示在任务列表）",
  "keyword": "适合闲鱼搜索的精简关键词",
  "reason": "推荐理由（1-2 句话，说明为什么推荐监控这个商品，比如文章中的评价、性价比分析、适合用户需求的原因等）",
  "description": "该商品的详细需求描述（包含用户对品牌、型号、成色、配置等要求，用于后续 AI 分析标准生成）",
  "min_price": null,
  "max_price": null,
  "personal_only": true,
  "free_shipping": true,
  "region": "",
  "analyze_images": true
}

规则：
1. 每个独立的商品需求拆成一个任务，不要合并不同品类的商品。
2. keyword 只保留品牌、型号、核心规格，不要把价格、成色、卖家要求等塞进去。
3. description 要尽量保留用户对该商品的具体要求（型号偏好、成色要求、注意事项等），这将用于生成 AI 分析标准。
4. 如果用户提到了预算/价格范围，提取到 min_price/max_price，没有就填 null。
5. personal_only 默认 true，free_shipping 默认 true，analyze_images 默认 true。
6. 区域没有明确要求就填空字符串。
7. 至少输出 1 个任务，最多 20 个。
8. 如果输入内容无法识别出任何商品需求，输出包含 1 个元素的数组，task_name 设为 "未识别需求"。

用户输入如下：
$user_input
""")


def _normalize_preview(raw: Any) -> Dict[str, Any]:
    """将 AI 返回的单个任务配置规范化。"""
    if not isinstance(raw, dict):
        return {}

    def _str_or_none(val: Any) -> str | None:
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None

    def _bool(val: Any, default: bool) -> bool:
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in {"1", "true", "yes"}

    return {
        "task_name": _str_or_none(raw.get("task_name")) or "未命名任务",
        "keyword": _str_or_none(raw.get("keyword")) or "",
        "reason": _str_or_none(raw.get("reason")) or "",
        "description": _str_or_none(raw.get("description")) or "",
        "min_price": _str_or_none(raw.get("min_price")),
        "max_price": _str_or_none(raw.get("max_price")),
        "personal_only": _bool(raw.get("personal_only"), True),
        "free_shipping": _bool(raw.get("free_shipping"), True),
        "region": _str_or_none(raw.get("region")) or "",
        "analyze_images": _bool(raw.get("analyze_images"), True),
        "decision_mode": "ai",
    }


def _normalize_previews(parsed: Any) -> List[Dict[str, Any]]:
    """将 AI 返回的结果规范化为任务配置列表。"""
    items: list
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = [parsed]
    else:
        return []

    results = []
    for item in items[:20]:
        normalized = _normalize_preview(item)
        if normalized.get("keyword"):
            results.append(normalized)
        else:
            logger.warning("跳过无 keyword 的预览项: %s", normalized.get("task_name", "<unknown>"))
    return results


async def run_batch_generation(
    *,
    job_id: str,
    url: str | None,
    description: str | None,
    service: BatchGenerationService,
    ai_client: Optional[AIClient] = None,
) -> None:
    """执行批量任务解析的后台作业。"""
    try:
        # Step 1: fetch
        parts: list[str] = []
        if url:
            await service.advance(job_id, "fetch", "正在抓取网页内容…")
            page_text = await fetch_url_content(url)
            parts.append(f"【参考链接内容】\n{page_text}")
        if description:
            parts.append(f"【用户需求描述】\n{description}")
        if not url:
            await service.advance(job_id, "fetch", "已接收输入。")
        combined_input = "\n\n".join(parts)

        # Step 2: analyze
        await service.advance(job_id, "analyze", "正在调用 AI 深度分析…")
        owns_client = ai_client is None
        if owns_client:
            ai_client = AIClient()
        try:
            if not ai_client.is_available():
                ai_client.refresh()
            if not ai_client.is_available():
                raise RuntimeError("AI 客户端不可用，请检查 AI 配置。")

            prompt = BATCH_INTENT_PROMPT.safe_substitute(user_input=combined_input)
            response_text = await ai_client.call_ai(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_output_tokens=4000,
                enable_json_output=True,
            )
        finally:
            if owns_client:
                await ai_client.close()

        # Step 3: parse
        await service.advance(job_id, "parse", "正在解析任务配置…")
        parsed = parse_ai_response_json(response_text)
        previews = _normalize_previews(parsed)

        if not previews:
            await service.fail(job_id, "AI 未能从输入中识别出任何商品监控需求。")
            return

        await service.complete(
            job_id,
            previews,
            f"解析完成，共识别出 {len(previews)} 个监控任务。",
        )
    except Exception as exc:
        await service.fail(job_id, f"批量解析失败: {exc}")

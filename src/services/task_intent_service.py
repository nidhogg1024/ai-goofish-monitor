"""
自然语言任务意图解析服务
"""
from __future__ import annotations

import logging
import re
from string import Template
from typing import Any, Dict

from src.domain.models.task import TaskGenerateRequest
from src.infrastructure.external.ai_client import AIClient
from src.services.ai_response_parser import parse_ai_response_json

logger = logging.getLogger(__name__)

TASK_INTENT_PROMPT_TEMPLATE = Template("""
你是“闲鱼监控任务配置助手”。你的职责是把用户的自然语言购买需求，解析成适合闲鱼监控任务的 JSON 配置。

请严格只输出 JSON，不要输出解释，不要输出 Markdown。

输出字段必须包含：
{
  "task_name": "给用户看的简短任务名",
  "category": "任务分类，例如扫地机器人/手机数码",
  "group_name": "任务组名称，例如租房两猫",
  "keyword": "适合闲鱼搜索的精简关键词",
  "min_price": null,
  "max_price": null,
  "personal_only": true,
  "free_shipping": true,
  "new_publish_option": "",
  "region": "",
  "analyze_images": true,
  "decision_mode": "ai",
  "keyword_rules": []
}

规则：
1. task_name 要简洁自然，适合展示在任务列表里。
1.1 category 尽量提炼成用户真正的品类，例如扫地机器人、手机数码、相机影像。
1.2 group_name 表达这次购买意图，例如租房两猫、主力机升级、样机与库存；没有明显意图时用“<category>关注池”。
2. keyword 要尽量短，只保留品牌、型号、核心规格，不要把预算、卖家要求、成色要求塞进去。
3. 如果用户提到了预算上限、最高价、xxx以内，提取到 max_price。
4. 如果用户提到了最低价、至少多少钱，再提取 min_price；没提就填 null。
5. 如果用户没有特别说明，personal_only 默认为 true，free_shipping 默认为 true。
6. 默认 decision_mode 固定为 "ai"，keyword_rules 固定为空数组。
7. 区域没有明确要求就填空字符串。
8. new_publish_option 只有在用户明确说“最新”“1天内”“3天内”“7天内”“14天内”时才填写这些值，否则填空字符串。
9. analyze_images 默认 true，除非用户明确说只看文字。

用户需求如下：
$user_description
""")

PRICE_PATTERNS = (
    re.compile(r"(?:预算|最高|不超过|控制在|到手|上限)\s*[:：]?\s*(\d{2,6})\s*元?", re.IGNORECASE),
    re.compile(r"(\d{2,6})\s*元?\s*(?:以内|以下|封顶)", re.IGNORECASE),
)

PUBLISH_OPTIONS = ("最新", "1天内", "3天内", "7天内", "14天内")

LEADING_PHRASES_RE = re.compile(
    r"^(我想买|我想蹲|帮我蹲|帮我找|帮我监控|想买|想蹲|求蹲|看看|需要|收|求购)\s*",
    re.IGNORECASE,
)


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _infer_keyword_from_description(description: str) -> str:
    text = description.strip()
    text = LEADING_PHRASES_RE.sub("", text)
    text = re.split(r"[，,。；;！!\n]", text)[0]
    text = re.sub(r"(预算|最高|不超过|控制在|以内|以下).*$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 24:
        text = text[:24].rstrip()
    return text or "闲鱼监控"


def _infer_task_name(description: str, keyword: str) -> str:
    base = _infer_keyword_from_description(description) or keyword or "闲鱼监控"
    base = re.sub(r"\s+", " ", base).strip()
    if len(base) > 28:
        base = base[:28].rstrip()
    return base or "闲鱼监控"


def _infer_max_price(description: str) -> str | None:
    for pattern in PRICE_PATTERNS:
        matched = pattern.search(description)
        if matched:
            return matched.group(1)
    return None


def _infer_publish_option(description: str) -> str:
    for option in PUBLISH_OPTIONS:
        if option in description:
            return option
    return ""


def _fallback_payload(description: str) -> Dict[str, Any]:
    keyword = _infer_keyword_from_description(description)
    return {
        "task_name": _infer_task_name(description, keyword),
        "category": None,
        "group_name": None,
        "keyword": keyword,
        "min_price": None,
        "max_price": _infer_max_price(description),
        "personal_only": True,
        "free_shipping": True,
        "new_publish_option": _infer_publish_option(description),
        "region": "",
        "analyze_images": True,
        "decision_mode": "ai",
        "keyword_rules": [],
    }


def _normalize_ai_payload(payload: Dict[str, Any], description: str) -> Dict[str, Any]:
    normalized = _fallback_payload(description)
    if not isinstance(payload, dict):
        return normalized

    task_name = _normalize_optional_text(payload.get("task_name"))
    keyword = _normalize_optional_text(payload.get("keyword"))
    min_price = _normalize_optional_text(payload.get("min_price"))
    max_price = _normalize_optional_text(payload.get("max_price"))
    region = _normalize_optional_text(payload.get("region")) or ""
    publish_option = _normalize_optional_text(payload.get("new_publish_option")) or ""

    normalized.update(
        {
            "task_name": task_name or normalized["task_name"],
            "category": _normalize_optional_text(payload.get("category")),
            "group_name": _normalize_optional_text(payload.get("group_name")),
            "keyword": keyword or normalized["keyword"],
            "min_price": min_price,
            "max_price": max_price or normalized["max_price"],
            "personal_only": _normalize_bool(payload.get("personal_only"), True),
            "free_shipping": _normalize_bool(payload.get("free_shipping"), True),
            "new_publish_option": publish_option if publish_option in PUBLISH_OPTIONS else normalized["new_publish_option"],
            "region": region,
            "analyze_images": _normalize_bool(payload.get("analyze_images"), True),
            "decision_mode": "ai",
            "keyword_rules": [],
        }
    )
    return normalized


async def parse_task_intent(user_description: str) -> Dict[str, Any]:
    description = str(user_description or "").strip()
    if not description:
        raise ValueError("自然语言需求不能为空。")

    fallback = _fallback_payload(description)
    ai_client = AIClient()
    try:
        if not ai_client.is_available():
            ai_client.refresh()
        if not ai_client.is_available():
            return fallback

        prompt = TASK_INTENT_PROMPT_TEMPLATE.safe_substitute(user_description=description)
        response_text = await ai_client.call_ai(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_output_tokens=500,
            enable_json_output=True,
        )
        parsed = parse_ai_response_json(response_text)
        return _normalize_ai_payload(parsed, description)
    except Exception as exc:
        logger.warning("AI 意图解析失败，使用 fallback: %s", exc)
        return fallback
    finally:
        await ai_client.close()


async def enrich_generate_request(req: TaskGenerateRequest) -> TaskGenerateRequest:
    enriched = req.model_copy()
    mode = enriched.decision_mode or "ai"
    description = str(enriched.description or "").strip()
    task_name = _normalize_optional_text(enriched.task_name)
    keyword = _normalize_optional_text(enriched.keyword)

    if mode != "ai":
        if not task_name and keyword:
            enriched.task_name = keyword
        return enriched

    if task_name and keyword:
        return enriched

    parsed = await parse_task_intent(description)
    enriched.task_name = task_name or parsed.get("task_name") or _infer_task_name(description, keyword or "")
    if not enriched.category and parsed.get("category"):
        enriched.category = parsed["category"]
    if not enriched.group_name and parsed.get("group_name"):
        enriched.group_name = parsed["group_name"]
    enriched.keyword = keyword or parsed.get("keyword") or _infer_keyword_from_description(description)

    if enriched.min_price is None and parsed.get("min_price") is not None:
        enriched.min_price = parsed["min_price"]
    if enriched.max_price is None and parsed.get("max_price") is not None:
        enriched.max_price = parsed["max_price"]
    if not enriched.region and parsed.get("region"):
        enriched.region = parsed["region"]
    if not enriched.new_publish_option and parsed.get("new_publish_option"):
        enriched.new_publish_option = parsed["new_publish_option"]
    if enriched.personal_only is True and parsed.get("personal_only") is False:
        enriched.personal_only = False
    if enriched.free_shipping is True and parsed.get("free_shipping") is False:
        enriched.free_shipping = False
    if enriched.analyze_images is True and parsed.get("analyze_images") is False:
        enriched.analyze_images = False
    return enriched

"""
任务级商品匹配器
在 AI 分析和结果入库前，先做一次硬过滤，避免型号串台和配件污染。
"""
from __future__ import annotations

import re
from typing import Any


MODEL_TOKEN_RE = re.compile(r"(?<![a-z0-9])([a-z]{1,4}\d{1,4}[a-z]{0,4})(?![a-z0-9])", re.I)
WORD_SPLIT_RE = re.compile(r"[\s,，、/|+()（）【】\\-]+")

KNOWN_BRAND_ALIASES = {
    "科沃斯": ("科沃斯", "ecovacs"),
    "追觅": ("追觅", "dreame"),
    "云鲸": ("云鲸", "narwal"),
    "石头": ("石头", "roborock"),
    "小米": ("小米", "米家", "xiaomi", "mijia"),
    "mova": ("mova",),
}

GENERIC_STOPWORDS = {
    "二手",
    "闲置",
    "个人",
    "蹲",
    "求",
    "收",
    "全新",
    "国行",
}

ROBOT_VACUUM_STRONG_ACCESSORY_TERMS = (
    "耗材",
    "边刷",
    "滤芯",
    "滤网",
    "滚刷",
    "主刷",
    "尘袋",
    "集尘袋",
    "清洁液",
    "海帕",
)

ROBOT_VACUUM_WRONG_FAMILY_TERMS = (
    "窗宝",
    "擦窗",
    "擦玻璃",
    "玻璃清洁",
)

ROBOT_VACUUM_BROKEN_MACHINE_TERMS = (
    "单机",
    "缺基站",
    "没有机器人",
    "只有这个基站",
    "仅基站",
)

ROBOT_VACUUM_MACHINE_HINTS = (
    "机器人",
    "扫地机器人",
    "扫拖",
    "基站",
    "全能基站",
    "扫拖一体",
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_text(value))


def _split_keyword_terms(keyword: str) -> list[str]:
    parts = []
    for raw in WORD_SPLIT_RE.split(str(keyword or "").strip()):
        token = raw.strip()
        if not token:
            continue
        parts.append(token)
    return parts


def _extract_model_tokens(keyword: str) -> list[str]:
    compact = _compact_text(keyword)
    seen: set[str] = set()
    tokens: list[str] = []
    for match in MODEL_TOKEN_RE.finditer(compact):
        token = match.group(1).lower()
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _extract_brand_alias_groups(keyword: str) -> list[tuple[str, ...]]:
    normalized = _normalize_text(keyword)
    groups: list[tuple[str, ...]] = []
    for canonical, aliases in KNOWN_BRAND_ALIASES.items():
        alias_hit = canonical.lower() in normalized or any(alias.lower() in normalized for alias in aliases)
        if alias_hit:
            groups.append(tuple(alias.lower() for alias in aliases))
    return groups


def _extract_fallback_terms(keyword: str, model_tokens: list[str]) -> list[str]:
    model_set = set(model_tokens)
    required: list[str] = []
    seen: set[str] = set()
    for token in _split_keyword_terms(keyword):
        normalized = token.strip().lower()
        if not normalized or normalized in GENERIC_STOPWORDS or normalized in model_set:
            continue
        if len(normalized) <= 1:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        required.append(normalized)
    return required


def _build_match_text(item_data: dict) -> str:
    title = str(item_data.get("商品标题") or "")
    tags = item_data.get("商品标签") or []
    area = str(item_data.get("发货地区") or "")
    seller = str(item_data.get("卖家昵称") or "")
    desc = str(item_data.get("商品描述") or "")
    attrs = item_data.get("商品属性") or {}
    attrs_text = " ".join(f"{k} {v}" for k, v in attrs.items()) if isinstance(attrs, dict) else ""
    return _normalize_text(" ".join([title, " ".join(map(str, tags)), area, seller, desc, attrs_text]))


def _keyword_suggests_robot_vacuum(task_config: dict) -> bool:
    category = _normalize_text(task_config.get("category") or "")
    group_name = _normalize_text(task_config.get("group_name") or "")
    keyword = _normalize_text(task_config.get("keyword") or "")
    description = _normalize_text(task_config.get("description") or "")
    combined = " ".join([category, group_name, keyword, description])
    markers = ("扫地", "扫拖", "机器人", "基站", "机械臂")
    return any(marker in combined for marker in markers)


def match_task_item(task_config: dict, item_data: dict) -> tuple[bool, str]:
    keyword = str(task_config.get("keyword") or "").strip()
    if not keyword:
        return True, "任务未配置关键词，跳过硬过滤。"

    match_text = _build_match_text(item_data)
    compact_text = _compact_text(match_text)
    title = str(item_data.get("商品标题") or "")

    if _keyword_suggests_robot_vacuum(task_config):
        for term in ROBOT_VACUUM_WRONG_FAMILY_TERMS:
            if term in match_text:
                return False, f"命中扫地机器人排除词：{term}"
        for term in ROBOT_VACUUM_BROKEN_MACHINE_TERMS:
            if term in match_text:
                return False, f"命中扫地机器人残缺机型词：{term}"
        for term in ROBOT_VACUUM_STRONG_ACCESSORY_TERMS:
            if term in match_text:
                return False, f"命中扫地机器人配件词：{term}"
        if "适配" in match_text or "通用" in match_text:
            return False, "命中扫地机器人配件词：适配/通用"
        if "配件" in match_text and not any(hint in match_text for hint in ROBOT_VACUUM_MACHINE_HINTS):
            return False, "命中扫地机器人配件词：配件"

    brand_groups = _extract_brand_alias_groups(keyword)
    if brand_groups:
        has_brand = any(any(alias in match_text for alias in aliases) for aliases in brand_groups)
        if not has_brand:
            return False, "未命中任务品牌词。"

    model_tokens = _extract_model_tokens(keyword)
    if model_tokens:
        missing = [token for token in model_tokens if token not in compact_text]
        if missing:
            return False, f"未命中任务型号词：{', '.join(missing)}"
    else:
        fallback_terms = _extract_fallback_terms(keyword, model_tokens)
        if fallback_terms:
            missing_terms = [term for term in fallback_terms if term not in match_text]
            if missing_terms:
                return False, f"未命中任务关键词：{', '.join(missing_terms[:3])}"

    text_model_tokens = set(token.lower() for token in MODEL_TOKEN_RE.findall(compact_text))
    required_model_set = set(model_tokens)
    if required_model_set:
        conflicting_tokens: list[str] = []
        for token in text_model_tokens:
            if token in required_model_set:
                continue
            prefix = re.match(r"[a-z]+", token)
            required_prefixes = {
                re.match(r"[a-z]+", model).group(0)
                for model in required_model_set
                if re.match(r"[a-z]+", model)
            }
            if prefix and prefix.group(0) in required_prefixes:
                conflicting_tokens.append(token)
        if conflicting_tokens and any(term in match_text for term in ("适配", "通用", "配件", "耗材")):
            return False, f"同时出现其他型号词：{', '.join(sorted(conflicting_tokens)[:3])}"

    return True, f"匹配通过：{title[:40]}"

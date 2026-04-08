"""
任务分类与任务组推断服务。
"""
from __future__ import annotations

from typing import Any, Tuple


CATEGORY_MARKERS: dict[str, tuple[str, ...]] = {
    "扫地机器人": (
        "扫地机器人", "扫拖机器人", "科沃斯", "追觅", "云鲸", "石头",
        "mova", "米家", "小米", "美的", "基站", "洗拖布", "集尘",
    ),
    "手机数码": ("iphone", "安卓", "手机", "小米14", "荣耀", "华为", "vivo", "oppo"),
    "相机影像": ("相机", "镜头", "索尼", "佳能", "尼康", "富士"),
    "显卡硬件": ("显卡", "rtx", "rx", "矿卡", "nvidia", "amd"),
    "游戏设备": ("switch", "ps5", "xbox", "掌机", "游戏机"),
    "智能穿戴": ("apple watch", "手表", "watch s", "iwatch"),
    "电脑整机": ("macbook", "笔记本", "thinkpad", "拯救者", "yoga"),
}

SPECIAL_GROUP_RULES: list[dict[str, Any]] = [
    {
        "category": "扫地机器人",
        "markers": ("租房", "单间", "三十多平", "38 平", "38平", "小户型"),
        "extra_condition_markers": ("猫", "猫毛"),
        "group_name": "租房两猫",
    },
]


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def infer_task_category(task_name: str | None, keyword: str | None, description: str | None) -> str:
    haystack = " ".join(
        part for part in [task_name or "", keyword or "", description or ""] if part
    ).lower()
    for category, markers in CATEGORY_MARKERS.items():
        if any(marker in haystack for marker in markers):
            return category
    return "通用监控"


def infer_task_group(
    category: str | None,
    task_name: str | None,
    keyword: str | None,
    description: str | None,
) -> str:
    current_category = _normalize_text(category) or infer_task_category(task_name, keyword, description)
    haystack = " ".join(
        part for part in [task_name or "", keyword or "", description or ""] if part
    ).lower()

    for rule in SPECIAL_GROUP_RULES:
        if current_category == rule["category"]:
            if any(m in haystack for m in rule["markers"]):
                extra = rule.get("extra_condition_markers")
                if not extra or any(m in haystack for m in extra):
                    return rule["group_name"]

    return f"{current_category}关注池"


def ensure_task_taxonomy(
    *,
    category: Any,
    group_name: Any,
    task_name: Any,
    keyword: Any,
    description: Any,
) -> Tuple[str, str]:
    normalized_category = _normalize_text(category)
    normalized_group = _normalize_text(group_name)
    task_name_text = _normalize_text(task_name)
    keyword_text = _normalize_text(keyword)
    description_text = _normalize_text(description)

    resolved_category = normalized_category or infer_task_category(
        task_name_text,
        keyword_text,
        description_text,
    )
    resolved_group = normalized_group or infer_task_group(
        resolved_category,
        task_name_text,
        keyword_text,
        description_text,
    )
    return resolved_category, resolved_group


def ensure_task_taxonomy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    values = dict(payload)
    category, group_name = ensure_task_taxonomy(
        category=values.get("category"),
        group_name=values.get("group_name"),
        task_name=values.get("task_name"),
        keyword=values.get("keyword"),
        description=values.get("description"),
    )
    values["category"] = category
    values["group_name"] = group_name
    return values

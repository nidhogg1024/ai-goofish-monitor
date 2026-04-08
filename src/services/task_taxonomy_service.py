"""
任务分类与任务组推断服务。
"""
from __future__ import annotations

from typing import Any, Tuple


ROBOT_VACUUM_MARKERS = (
    "扫地机器人",
    "扫拖机器人",
    "科沃斯",
    "追觅",
    "云鲸",
    "石头",
    "mova",
    "米家",
    "小米",
    "美的",
    "基站",
    "洗拖布",
    "集尘",
)

PHONE_MARKERS = ("iphone", "安卓", "手机", "小米14", "荣耀", "华为", "vivo", "oppo")
CAMERA_MARKERS = ("相机", "镜头", "索尼", "佳能", "尼康", "富士")
GPU_MARKERS = ("显卡", "rtx", "rx", "矿卡", "nvidia", "amd")
GAME_MARKERS = ("switch", "ps5", "xbox", "掌机", "游戏机")
WATCH_MARKERS = ("apple watch", "手表", "watch s", "iwatch")
LAPTOP_MARKERS = ("macbook", "笔记本", "thinkpad", "拯救者", "yoga")


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def infer_task_category(task_name: str | None, keyword: str | None, description: str | None) -> str:
    haystack = " ".join(
        part for part in [task_name or "", keyword or "", description or ""] if part
    ).lower()
    if any(marker in haystack for marker in ROBOT_VACUUM_MARKERS):
        return "扫地机器人"
    if any(marker in haystack for marker in PHONE_MARKERS):
        return "手机数码"
    if any(marker in haystack for marker in CAMERA_MARKERS):
        return "相机影像"
    if any(marker in haystack for marker in GPU_MARKERS):
        return "显卡硬件"
    if any(marker in haystack for marker in GAME_MARKERS):
        return "游戏设备"
    if any(marker in haystack for marker in WATCH_MARKERS):
        return "智能穿戴"
    if any(marker in haystack for marker in LAPTOP_MARKERS):
        return "电脑整机"
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

    if current_category == "扫地机器人":
        if any(marker in haystack for marker in ("租房", "单间", "三十多平", "38 平", "38平", "小户型")) and (
            "猫" in haystack or "猫毛" in haystack
        ):
            return "租房两猫"
        return "扫地机器人关注池"

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

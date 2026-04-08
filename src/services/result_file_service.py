"""
结果记录富化与文件名校验服务
"""

from src.infrastructure.persistence.storage_names import normalize_keyword_from_filename
from src.services.price_history_service import (
    build_item_price_context,
    load_price_snapshots,
    parse_price_value,
)


def validate_result_filename(filename: str) -> None:
    if not filename.endswith(".jsonl") or "/" in filename or ".." in filename:
        raise ValueError("无效的文件名")


def enrich_records_with_price_insight(records: list[dict], filename: str) -> list[dict]:
    snapshots = load_price_snapshots(normalize_keyword_from_filename(filename))
    if not snapshots:
        return records

    enriched = []
    for record in records:
        info = record.get("商品信息", {}) or {}
        clone = dict(record)
        clone["price_insight"] = build_item_price_context(
            snapshots,
            item_id=str(info.get("商品ID") or ""),
            current_price=parse_price_value(info.get("当前售价")),
        )
        enriched.append(clone)
    return enriched


def enrich_records_with_dynamic_price_insight(records: list[dict]) -> list[dict]:
    snapshot_cache: dict[str, list[dict]] = {}
    enriched = []
    for record in records:
        keyword = str(record.get("搜索关键字") or "").strip()
        if not keyword:
            enriched.append(record)
            continue
        snapshots = snapshot_cache.get(keyword)
        if snapshots is None:
            snapshots = load_price_snapshots(keyword)
            snapshot_cache[keyword] = snapshots
        if not snapshots:
            enriched.append(record)
            continue
        info = record.get("商品信息", {}) or {}
        clone = dict(record)
        clone["price_insight"] = build_item_price_context(
            snapshots,
            item_id=str(info.get("商品ID") or ""),
            current_price=parse_price_value(info.get("当前售价")),
        )
        enriched.append(clone)
    return enriched

import json
from datetime import datetime

from src.config import AI_DEBUG_MODE
from src.utils import safe_get


def _looks_like_result_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    serialized = json.dumps(item, ensure_ascii=False)
    markers = ("itemId", "targetUrl", "picUrl", "title", "price", "main")
    return any(marker in serialized for marker in markers)


def _extract_result_items(payload) -> list:
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload) and any(
            _looks_like_result_item(item) for item in payload
        ):
            return payload
        for item in payload:
            nested = _extract_result_items(item)
            if nested:
                return nested
        return []

    if not isinstance(payload, dict):
        return []

    preferred_paths = (
        ("data", "resultList"),
        ("data", "cardList"),
        ("resultList",),
        ("cardList",),
        ("data", "data", "resultList"),
        ("data", "data", "cardList"),
    )
    for path in preferred_paths:
        current = payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, list) and current:
            if any(_looks_like_result_item(item) for item in current if isinstance(item, dict)):
                return current

    for value in payload.values():
        nested = _extract_result_items(value)
        if nested:
            return nested
    return []


def _extract_main_data(item: dict) -> dict:
    candidates = (
        item.get("data", {}).get("item", {}).get("main", {}).get("exContent"),
        item.get("data", {}).get("item", {}).get("main"),
        item.get("item", {}).get("main", {}).get("exContent"),
        item.get("item", {}).get("main"),
        item.get("main", {}).get("exContent"),
        item.get("main"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _extract_click_args(item: dict) -> dict:
    candidates = (
        item.get("data", {}).get("item", {}).get("main", {}).get("clickParam", {}).get("args"),
        item.get("item", {}).get("main", {}).get("clickParam", {}).get("args"),
        item.get("main", {}).get("clickParam", {}).get("args"),
        item.get("clickParam", {}).get("args"),
        item.get("args"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _extract_target_url(item: dict, click_params: dict) -> str:
    candidates = (
        item.get("data", {}).get("item", {}).get("main", {}).get("targetUrl"),
        item.get("item", {}).get("main", {}).get("targetUrl"),
        item.get("main", {}).get("targetUrl"),
        item.get("targetUrl"),
        click_params.get("targetUrl"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _normalize_price(price_parts) -> str:
    if isinstance(price_parts, list):
        price = "".join(
            [str(p.get("text", "")) for p in price_parts if isinstance(p, dict)]
        ).replace("当前价", "").strip()
        if "万" in price:
            return f"¥{float(price.replace('¥', '').replace('万', '')) * 10000:.0f}"
        return price or "价格异常"
    if isinstance(price_parts, dict):
        text = str(price_parts.get("text") or price_parts.get("price") or "").strip()
        return text or "价格异常"
    if price_parts is None:
        return "价格异常"
    return str(price_parts).strip() or "价格异常"


async def _parse_search_results_json(json_data: dict, source: str) -> list:
    """解析搜索API的JSON数据，返回基础商品信息列表。"""
    page_data = []
    try:
        items = _extract_result_items(json_data)
        if not items:
            print(f"LOG: ({source}) API响应中未找到商品列表。")
            if isinstance(json_data, dict):
                top_level_keys = list(json_data.keys())[:10]
                data_keys = list((json_data.get("data") or {}).keys())[:10] if isinstance(json_data.get("data"), dict) else []
                print(f"LOG: ({source}) 顶层keys={top_level_keys} data.keys={data_keys}")
            if AI_DEBUG_MODE:
                print(f"--- [SEARCH DEBUG] RAW JSON RESPONSE from {source} ---")
                print(json.dumps(json_data, ensure_ascii=False, indent=2))
                print("----------------------------------------------------")
            return []

        for item in items:
            main_data = _extract_main_data(item)
            click_params = _extract_click_args(item)

            title = await safe_get(main_data, "title", default="未知标题")
            if "没有找到你想要的宝贝" in str(title) or "小闲鱼没有找到" in str(title):
                continue
            price_parts = await safe_get(main_data, "price", default=[])
            price = _normalize_price(price_parts)
            area = await safe_get(main_data, "area", default="地区未知")
            seller = await safe_get(main_data, "userNickName", default="匿名卖家")
            raw_link = _extract_target_url(item, click_params)
            image_url = await safe_get(main_data, "picUrl", default="")
            pub_time_ts = click_params.get("publishTime", "")
            item_id = await safe_get(main_data, "itemId", default="未知ID")
            if item_id == "未知ID":
                item_id = click_params.get("itemId") or item.get("itemId") or "未知ID"
            original_price = await safe_get(main_data, "oriPrice", default="暂无")
            wants_count = await safe_get(click_params, "wantNum", default='NaN')


            tags = []
            if await safe_get(click_params, "tag") == "freeship":
                tags.append("包邮")
            r1_tags = await safe_get(main_data, "fishTags", "r1", "tagList", default=[])
            for tag_item in r1_tags:
                content = await safe_get(tag_item, "data", "content", default="")
                if "验货宝" in content:
                    tags.append("验货宝")

            page_data.append({
                "商品标题": title,
                "当前售价": price,
                "商品原价": original_price,
                "“想要”人数": wants_count,
                "商品标签": tags,
                "发货地区": area,
                "卖家昵称": seller,
                "商品链接": raw_link.replace("fleamarket://", "https://www.goofish.com/"),
                "发布时间": datetime.fromtimestamp(int(pub_time_ts)/1000).strftime("%Y-%m-%d %H:%M") if pub_time_ts.isdigit() else "未知时间",
                "商品ID": item_id,
                "商品主图链接": image_url,
            })
        print(f"LOG: ({source}) 成功解析到 {len(page_data)} 条商品基础信息。")
        return page_data
    except Exception as e:
        print(f"LOG: ({source}) JSON数据处理异常: {str(e)}")
        return []


async def calculate_reputation_from_ratings(ratings_json: list) -> dict:
    """从原始评价API数据列表中，计算作为卖家和买家的好评数与好评率。"""
    seller_total = 0
    seller_positive = 0
    buyer_total = 0
    buyer_positive = 0

    for card in ratings_json:
        # 使用 safe_get 保证安全访问
        data = await safe_get(card, 'cardData', default={})
        role_tag = await safe_get(data, 'rateTagList', 0, 'text', default='')
        rate_type = await safe_get(data, 'rate') # 1=好评, 0=中评, -1=差评

        if "卖家" in role_tag:
            seller_total += 1
            if rate_type == 1:
                seller_positive += 1
        elif "买家" in role_tag:
            buyer_total += 1
            if rate_type == 1:
                buyer_positive += 1

    # 计算比率，并处理除以零的情况
    seller_rate = f"{(seller_positive / seller_total * 100):.2f}%" if seller_total > 0 else "N/A"
    buyer_rate = f"{(buyer_positive / buyer_total * 100):.2f}%" if buyer_total > 0 else "N/A"

    return {
        "作为卖家的好评数": f"{seller_positive}/{seller_total}",
        "作为卖家的好评率": seller_rate,
        "作为买家的好评数": f"{buyer_positive}/{buyer_total}",
        "作为买家的好评率": buyer_rate
    }


async def _parse_user_items_data(items_json: list) -> list:
    """解析用户主页的商品列表API的JSON数据。"""
    parsed_list = []
    for card in items_json:
        data = card.get('cardData', {})
        status_code = data.get('itemStatus')
        if status_code == 0:
            status_text = "在售"
        elif status_code == 1:
            status_text = "已售"
        else:
            status_text = f"未知状态 ({status_code})"

        parsed_list.append({
            "商品ID": data.get('id'),
            "商品标题": data.get('title'),
            "商品价格": data.get('priceInfo', {}).get('price'),
            "商品主图": data.get('picInfo', {}).get('picUrl'),
            "商品状态": status_text
        })
    return parsed_list


async def parse_user_head_data(head_json: dict) -> dict:
    """解析用户头部API的JSON数据。"""
    data = head_json.get('data', {})
    ylz_tags = await safe_get(data, 'module', 'base', 'ylzTags', default=[])
    seller_credit, buyer_credit = {}, {}
    for tag in ylz_tags:
        if await safe_get(tag, 'attributes', 'role') == 'seller':
            seller_credit = {'level': await safe_get(tag, 'attributes', 'level'), 'text': tag.get('text')}
        elif await safe_get(tag, 'attributes', 'role') == 'buyer':
            buyer_credit = {'level': await safe_get(tag, 'attributes', 'level'), 'text': tag.get('text')}
    return {
        "卖家昵称": await safe_get(data, 'module', 'base', 'displayName'),
        "卖家头像链接": await safe_get(data, 'module', 'base', 'avatar', 'avatar'),
        "卖家个性签名": await safe_get(data, 'module', 'base', 'introduction', default=''),
        "卖家在售/已售商品数": await safe_get(data, 'module', 'tabs', 'item', 'number'),
        "卖家收到的评价总数": await safe_get(data, 'module', 'tabs', 'rate', 'number'),
        "卖家信用等级": seller_credit.get('text', '暂无'),
        "买家信用等级": buyer_credit.get('text', '暂无')
    }


async def parse_ratings_data(ratings_json: list) -> list:
    """解析评价列表API的JSON数据。"""
    parsed_list = []
    for card in ratings_json:
        data = await safe_get(card, 'cardData', default={})
        rate_tag = await safe_get(data, 'rateTagList', 0, 'text', default='未知角色')
        rate_type = await safe_get(data, 'rate')
        if rate_type == 1: rate_text = "好评"
        elif rate_type == 0: rate_text = "中评"
        elif rate_type == -1: rate_text = "差评"
        else: rate_text = "未知"
        parsed_list.append({
            "评价ID": data.get('rateId'),
            "评价内容": data.get('feedback'),
            "评价类型": rate_text,
            "评价来源角色": rate_tag,
            "评价者昵称": data.get('raterUserNick'),
            "评价时间": data.get('gmtCreate'),
            "评价图片": await safe_get(data, 'pictCdnUrlList', default=[])
        })
    return parsed_list

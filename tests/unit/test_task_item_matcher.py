from src.task_item_matcher import match_task_item


def test_t30_rejects_t10_result():
    task = {
        "keyword": "科沃斯 T30",
        "category": "扫地机器人",
        "group_name": "租房两猫",
        "description": "优先看科沃斯 T30 主机",
    }
    item = {
        "商品标题": "科沃斯T10 TURBO扫拖机器人全能基站",
        "商品标签": ["包邮"],
        "卖家昵称": "个人闲置",
    }
    matched, reason = match_task_item(task, item)
    assert matched is False
    assert "型号词" in reason


def test_t30_rejects_accessory_result():
    task = {
        "keyword": "科沃斯 T30",
        "category": "扫地机器人",
        "group_name": "租房两猫",
        "description": "优先看科沃斯 T30 主机",
    }
    item = {
        "商品标题": "科沃斯T30系列适配边刷滤芯尘袋配件",
        "商品标签": ["包邮"],
        "卖家昵称": "商家",
    }
    matched, reason = match_task_item(task, item)
    assert matched is False
    assert "配件词" in reason


def test_t30_accepts_real_machine():
    task = {
        "keyword": "科沃斯 T30",
        "category": "扫地机器人",
        "group_name": "租房两猫",
        "description": "优先看科沃斯 T30 主机",
    }
    item = {
        "商品标题": "科沃斯 T30 全能基站 扫拖机器人 个人闲置",
        "商品标签": ["包邮", "验货宝"],
        "卖家昵称": "阿泽",
    }
    matched, reason = match_task_item(task, item)
    assert matched is True
    assert "匹配通过" in reason


def test_t30_accepts_machine_with_accessories_included():
    task = {
        "keyword": "科沃斯 T30",
        "category": "扫地机器人",
        "group_name": "租房两猫",
        "description": "优先看科沃斯 T30 主机",
    }
    item = {
        "商品标题": "科沃斯 T30 扫拖机器人 全能基站 配件齐全 个人闲置",
        "商品标签": ["包邮"],
        "卖家昵称": "阿哲",
    }
    matched, reason = match_task_item(task, item)
    assert matched is True
    assert "匹配通过" in reason


def test_t30_rejects_base_only_listing():
    task = {
        "keyword": "科沃斯 T30",
        "category": "扫地机器人",
        "group_name": "租房两猫",
        "description": "优先看科沃斯 T30 主机",
    }
    item = {
        "商品标题": "科沃斯T30 Pro 只有这个基站 没有机器人",
        "商品标签": ["包邮"],
        "卖家昵称": "商家",
    }
    matched, reason = match_task_item(task, item)
    assert matched is False
    assert "残缺机型词" in reason

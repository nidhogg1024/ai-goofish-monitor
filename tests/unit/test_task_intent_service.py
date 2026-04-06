import pytest

from src.domain.models.task import TaskGenerateRequest
from src.services import task_intent_service


@pytest.mark.asyncio
async def test_enrich_generate_request_fills_missing_fields(monkeypatch):
    async def _fake_parse(description: str):
        assert "科沃斯" in description
        return {
            "task_name": "科沃斯 Y30 猫家庭",
            "keyword": "科沃斯 Y30",
            "min_price": None,
            "max_price": "700",
            "personal_only": True,
            "free_shipping": True,
            "new_publish_option": "",
            "region": "",
            "analyze_images": True,
            "decision_mode": "ai",
            "keyword_rules": [],
        }

    monkeypatch.setattr(task_intent_service, "parse_task_intent", _fake_parse)

    req = TaskGenerateRequest(
        description="帮我蹲一个适合 38 平租房和两只猫的科沃斯 Y30，预算 700 以内",
        decision_mode="ai",
    )

    enriched = await task_intent_service.enrich_generate_request(req)

    assert enriched.task_name == "科沃斯 Y30 猫家庭"
    assert enriched.keyword == "科沃斯 Y30"
    assert enriched.max_price == "700"


@pytest.mark.asyncio
async def test_enrich_generate_request_keeps_manual_values(monkeypatch):
    async def _fake_parse(_description: str):
        return {
            "task_name": "不应覆盖",
            "keyword": "不应覆盖",
            "max_price": "999",
            "personal_only": False,
            "free_shipping": False,
            "new_publish_option": "1天内",
            "region": "上海",
            "analyze_images": False,
            "decision_mode": "ai",
            "keyword_rules": [],
        }

    monkeypatch.setattr(task_intent_service, "parse_task_intent", _fake_parse)

    req = TaskGenerateRequest(
        task_name="我自己的任务名",
        keyword="追觅 X30 Pro",
        description="帮我盯一下追觅 X30 Pro",
        max_price="850",
        personal_only=True,
        free_shipping=True,
        analyze_images=True,
        decision_mode="ai",
    )

    enriched = await task_intent_service.enrich_generate_request(req)

    assert enriched.task_name == "我自己的任务名"
    assert enriched.keyword == "追觅 X30 Pro"
    assert enriched.max_price == "850"
    assert enriched.personal_only is True
    assert enriched.free_shipping is True
    assert enriched.analyze_images is True

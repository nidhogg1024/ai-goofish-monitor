"""
AI 分析服务
封装 AI 分析相关的业务逻辑

DEPRECATED: This module is unused. Analysis is handled by ItemAnalysisDispatcher.
Retained temporarily for reference; scheduled for removal.
"""
import logging
import warnings
from typing import Dict, List, Optional
from src.infrastructure.external.ai_client import AIClient

logger = logging.getLogger(__name__)

warnings.warn(
    "AIAnalysisService is deprecated and unused; use ItemAnalysisDispatcher instead.",
    DeprecationWarning,
    stacklevel=2,
)


class AIAnalysisService:
    """AI 分析服务 (DEPRECATED)"""

    def __init__(self, ai_client: AIClient):
        self.ai_client = ai_client

    async def analyze_product(
        self,
        product_data: Dict,
        image_paths: List[str],
        prompt_text: str
    ) -> Optional[Dict]:
        """
        分析商品

        Args:
            product_data: 商品数据
            image_paths: 图片路径列表
            prompt_text: 分析提示词

        Returns:
            分析结果
        """
        if not self.ai_client.is_available():
            logger.warning("AI 客户端不可用，跳过分析")
            return None

        try:
            result = await self.ai_client.analyze(product_data, image_paths, prompt_text)

            if result and self._validate_result(result):
                return result
            else:
                logger.warning("AI 分析结果验证失败")
                return None
        except Exception as e:
            logger.error("AI 分析服务出错: %s", e)
            return None

    def _validate_result(self, result: Dict) -> bool:
        """验证 AI 分析结果的格式"""
        required_fields = [
            "prompt_version",
            "is_recommended",
            "reason",
            "risk_tags",
            "criteria_analysis"
        ]

        # 检查必需字段
        for field in required_fields:
            if field not in result:
                logger.warning("AI 响应缺少必需字段: %s", field)
                return False

        if not isinstance(result.get("is_recommended"), bool):
            logger.warning("is_recommended 字段不是布尔类型")
            return False

        if not isinstance(result.get("risk_tags"), list):
            logger.warning("risk_tags 字段不是列表类型")
            return False

        criteria_analysis = result.get("criteria_analysis", {})
        if not isinstance(criteria_analysis, dict) or not criteria_analysis:
            logger.warning("criteria_analysis 必须是非空字典")
            return False

        return True

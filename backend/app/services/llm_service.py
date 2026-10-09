from abc import ABC, abstractmethod


class LLMService(ABC):
    """大语言模型服务抽象接口"""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        seed: int | None = None,
    ) -> str:
        """生成文本

        seed 用于在支持的端点降低重复采样方差；
        不支持时由实现忽略。
        """
        raise NotImplementedError
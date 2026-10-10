import asyncio

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.services.llm_service import LLMService

logger = get_logger("app.llm")


class LLMEmptyResponseError(RuntimeError):
    """模型返回了空内容（未产生正文，可能只产出了思维链）。"""


class LLMTruncatedResponseError(RuntimeError):
    """模型输出被 max_tokens 截断，JSON 无法闭合。"""


# 触发重试的传输层异常。
# 批量评估会连续发起数百次调用，网络抖动或限流
# 若直接抛出，会让单个样本失败并污染统计结果。
RETRYABLE_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

# 退避策略。
# 网络类抖动与限流需要更长的等待，因此指数退避。
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 2.0
RETRY_BACKOFF_MAX_SECONDS = 20.0


class OpenAILLMService(LLMService):
    """OpenAI-compatible LLM 实现"""

    def __init__(self):
        if not settings.llm_api_key:
            raise ValueError("LLM_API_KEY 未配置")

        if not settings.llm_model:
            raise ValueError("LLM_MODEL 未配置")

        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
        )

        # 端点模型名，例如 agnes-3.0-flash。
        #
        # 不写死具体厂商：本项目换过端点（DeepSeek → Agnes），
        # 而这里的所有逻辑都是 OpenAI 兼容协议层面的，
        # 与厂商无关。真正需要厂商知识的地方只有一处 ——
        # 下面 _extract_content 里对 reasoning_content 的兼容，
        # 因为部分推理模型把正文放在那个字段。
        self.model = settings.llm_model

    @staticmethod
    def _build_messages(
        prompt: str,
        system_prompt: str | None,
    ) -> list[dict]:
        messages: list[dict] = []

        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

        return messages

    @staticmethod
    def _extract_content(message) -> str:
        """取出正文内容。

        某些兼容端点在开启思考模式时会返回 reasoning_content，
        正文为空。这里同时读取两者，避免误判为空响应。
        """

        content = getattr(message, "content", None)

        if content:
            return content

        # 兼容部分实现把正文放在 reasoning_content 的情况
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            return reasoning

        # 兜底：模型可能在 tool_calls 之外没有正文
        return ""

    @staticmethod
    def _looks_truncated(
        content: str,
        finish_reason: str | None,
    ) -> bool:
        """判断响应是否被截断。

        以 `finish_reason == "length"` 为主要依据，
        这是服务端给出的权威信号。

        括号配平只作为兜底：当 finish_reason 缺失时，
        才检查花括号是否闭合。

        注意：不要简化成"以 { 开头且不以 } 结尾"。
        那会把"JSON 完整、结尾带说明文字"的响应误判为截断，
        触发无谓的整轮重试。
        """

        if finish_reason == "length":
            return True

        stripped = content.strip()

        if not stripped:
            return False

        # finish_reason 缺失时，靠括号配平判断
        if finish_reason is None and stripped.startswith("{"):
            in_string = False
            escaped = False
            depth = 0

            for char in stripped:
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue

                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1

            if depth > 0:
                return True

        return False

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        seed: int | None = None,
        max_attempts: int | None = None,
    ) -> str:
        """生成内容。

        `max_attempts` 覆盖默认重试次数（`MAX_ATTEMPTS`）。

        为什么需要：**健康检查要快速失败并报告**，而生产路径需要
        重试来吸收抖动。实测诊断工具沿用默认重试时，
        一个不可达端点会耗掉 45 秒（4 次尝试 + 指数退避），
        让"检查环境"本身变成一件慢事 —— 那会让人不愿跑它。
        """

        if not prompt.strip():
            raise ValueError("LLM Prompt 不能为空")

        attempts = (
            MAX_ATTEMPTS if max_attempts is None else max_attempts
        )

        if attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")

        messages = self._build_messages(
            prompt=prompt,
            system_prompt=system_prompt,
        )

        request_params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            request_params["max_tokens"] = max_tokens

        # seed 用于降低重复采样的方差。
        # 各端点支持情况不同：不支持时服务端会忽略，
        # 不影响正常调用。
        if seed is not None:
            request_params["seed"] = seed

        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self.client.chat.completions.create(
                    **request_params
                )
            except RETRYABLE_EXCEPTIONS as exc:
                # 传输层失败：连接中断、超时、限流、服务端 5xx。
                # 这类错误与内容无关，重试即可恢复。
                last_error = exc
                # WARNING 而不是 ERROR：这类失败**预计会重试并可能成功**，
                # 用 ERROR 会让真正的失败淹没在噪音里。
                logger.warning(
                    "传输层失败（第 %d/%d 次）：%s: %s",
                    attempt,
                    attempts,
                    type(exc).__name__,
                    exc,
                )

                if attempt < attempts:
                    await asyncio.sleep(
                        self._backoff_seconds(attempt)
                    )

                continue

            choice = response.choices[0]
            content = self._extract_content(choice.message)
            finish_reason = getattr(choice, "finish_reason", None)

            if not content or not content.strip():
                last_error = LLMEmptyResponseError(
                    f"LLM 返回内容为空（第 {attempt}/{attempts} 次）"
                )
            elif self._looks_truncated(content, finish_reason):
                last_error = LLMTruncatedResponseError(
                    f"LLM 输出被截断（finish_reason={finish_reason}，"
                    f"第 {attempt}/{attempts} 次，"
                    f"长度={len(content)}）"
                )
            else:
                if attempt > 1:
                    # 重试后成功值得记一条：它能说明端点在抖动，
                    # 而不是"一切正常"。
                    logger.info(
                        "第 %d 次尝试成功（前 %d 次失败）",
                        attempt,
                        attempt - 1,
                    )
                return content

            logger.warning(
                "调用失败，准备重试：%s", last_error
            )

            if attempt < attempts:
                await asyncio.sleep(
                    self._backoff_seconds(attempt)
                )

        raise last_error if last_error else RuntimeError(
            "LLM 调用失败"
        )

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        """指数退避，并设置上限。

        attempt=1 → 2s，2 → 4s，3 → 8s，上限 20s。
        """

        return min(
            RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)),
            RETRY_BACKOFF_MAX_SECONDS,
        )

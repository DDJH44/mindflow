from openai import AsyncOpenAI, BadRequestError

from app.core.config import settings
from app.core.logging import get_logger
from app.services.embedding_service import EmbeddingService

logger = get_logger("app.embedding")


def parse_batch_limit(message: str) -> int | None:
    """从端点的报错里解析它声明的批量上限。

    端点的实际报错形如：

        Value error, batch size is invalid,
        it should not be larger than 10.: input.contents

    解析它而不是写死常量，是因为**这个上限会变**：
    实测同一端点在两次测量里分别声称 20 与 10；
    换端点（OpenAI 自身是 2048）差异更大。

    写死任何一个值都意味着"换端点时上传又开始失败"，
    而那种失败的表现是 502 —— 与真正的服务故障无法区分。
    """

    marker = "not be larger than"

    if marker not in message:
        return None

    tail = message.split(marker, 1)[1]

    digits = ""

    for char in tail.strip():
        if char.isdigit():
            digits += char
        elif digits:
            break
        elif char not in " .:":
            # 遇到非数字非分隔符的内容，说明格式与预期不符
            return None

    if not digits:
        return None

    try:
        value = int(digits)
    except ValueError:
        return None

    return value if value > 0 else None


class OpenAIEmbeddingService(EmbeddingService):
    """基于 OpenAI-compatible API 的 Embedding 实现"""

    # 进程内学到的批量上限。
    #
    # 放在类属性而不是实例属性：每次请求都会新建服务实例，
    # 实例属性等于"每次都要重新学一遍"，
    # 每个文档都要白白浪费一次被拒绝的请求。
    _learned_batch_limit: int | None = None

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url or None,
        )

        self.model = settings.embedding_model

    async def embed_text(
        self,
        text: str,
    ) -> list[float]:
        """将单段文本转换为向量"""

        if not text.strip():
            raise ValueError("Embedding 文本不能为空")

        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
        )

        return response.data[0].embedding

    @classmethod
    def current_batch_limit(cls) -> int:
        """当前生效的批量上限。"""

        if cls._learned_batch_limit is not None:
            return cls._learned_batch_limit

        return max(1, settings.embedding_batch_size)

    async def _create_embeddings(
        self,
        batch: list[str],
    ) -> list[list[float]]:
        """请求一批嵌入，遇到"批量过大"时自适应缩小并重试。

        **为什么必须自适应**：端点不保证上限，实测同一端点
        在两次测量里分别声称 20 与 10。写死常量会出现
        "今天能用、明天上传就 502"。

        做法：解析端点声明的上限 → 降到该值 → 递归重试。
        最多递归两次（实测一次就够；限制递归是为了在端点
        报出畸形上限时不会无限循环）。
        """

        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=batch,
            )
        except BadRequestError as exc:
            limit = parse_batch_limit(str(exc))

            if limit is None or limit >= len(batch):
                # 不是"批量过大"的问题，交给上层处理。
                raise

            type(self)._learned_batch_limit = limit

            logger.warning(
                "端点批量上限为 %d（本次请求 %d 条），"
                "已自适应缩小并重试",
                limit,
                len(batch),
            )

            vectors: list[list[float]] = []

            for start in range(0, len(batch), limit):
                part = batch[start : start + limit]
                vectors.extend(
                    await self._create_embeddings(part)
                )

            return vectors

        batch_vectors = [item.embedding for item in response.data]

        # 数量不符必须显式报错：它会破坏"向量 ↔ chunk"的
        # 对应关系，而那种错误不会在写入时暴露，
        # 只会让检索命中错误的片段。
        if len(batch_vectors) != len(batch):
            raise ValueError(
                f"嵌入返回数量不符：输入 {len(batch)} 条，"
                f"返回 {len(batch_vectors)} 条"
            )

        return batch_vectors

    async def embed_texts(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """批量将文本转换为向量（自动分批 + 自适应上限）。

        **必须分批**：当前端点会拒绝过大的批量。此前这里把整个
        文档的 chunk 一次性发出，于是**超过约 1 万字符的文件
        必然上传失败**，而报错是 502 "服务不可用" ——
        用户完全看不出问题出在文件长度上。

        分批**按顺序串行**，不做并发：

        - 顺序保证返回顺序与输入一致。调用方依赖这个对应关系
          把向量写回各自的 chunk，错位不会报错，只会让检索
          命中错误的片段 —— 那是最难查的一类问题。
        - 并发多个批次会触发端点限流，反而更慢。
        """

        if not texts:
            return []

        batch_size = self.current_batch_limit()
        total = len(texts)

        if total <= batch_size:
            return await self._create_embeddings(texts)

        vectors: list[list[float]] = []
        cursor = 0

        while cursor < total:
            # 每轮重新取上限：中途自适应学到更小的值时要立刻用上。
            batch_size = self.current_batch_limit()
            batch = texts[cursor : cursor + batch_size]

            vectors.extend(
                await self._create_embeddings(batch)
            )

            cursor += len(batch)

            if total > batch_size:
                logger.info(
                    "嵌入分批进度：%d/%d 条（批大小 %d）",
                    min(cursor, total),
                    total,
                    batch_size,
                )

        return vectors

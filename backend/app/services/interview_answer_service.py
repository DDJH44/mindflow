from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.interview_answer_repository import (
    InterviewAnswerRepository,
)


class InterviewAnswerService:
    """面试回答业务服务"""

    def __init__(self, db: AsyncSession):
        self.repository = InterviewAnswerRepository(db)

    async def create_answer(
        self,
        question_id: int,
        answer: str,
    ):
        return await self.repository.create(
            question_id=question_id,
            answer=answer,
        )

    async def get_answer(
        self,
        answer_id: int,
    ):
        return await self.repository.get_by_id(
            answer_id=answer_id
        )

    async def save_analysis(
        self,
        answer_id: int,
        analysis,
    ) -> bool:
        """把逐轮分析留档到回答记录。返回是否成功。

        ## 为什么吞掉异常而不往外抛

        这一步发生在"回答已经落库、追问也已经生成"之后。
        它失败只意味着**丢掉复盘数据**，而用户的回答与
        面试流程都不受影响。

        若让它抛出，一次写库抖动就会把已经成功的作答
        变成 5xx —— 用户会以为回答没提交成功，然后重答一遍，
        于是库里多一条重复回答。**那比丢掉复盘数据糟得多。**

        因此：失败记日志，返回 False，让调用方知道但不必中断。
        """

        from app.core.logging import get_logger

        logger = get_logger("app.answer_analysis")

        snapshot = self._build_snapshot(analysis)

        if snapshot is None:
            return False

        try:
            await self.repository.save_analysis(
                answer_id=answer_id,
                snapshot=snapshot,
            )
            return True

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "逐轮分析留档失败（不影响作答）：answer_id=%s %s: %s",
                answer_id,
                type(exc).__name__,
                exc,
            )
            return False

    @staticmethod
    def _build_snapshot(analysis) -> dict | None:
        """把 `AnswerAnalysis` 压成适合复盘的最小快照。

        刻意**不存全部字段**：
        - 存"漏了什么"（missing_points）与三个维度档位 ——
          那是复盘时真正要看的
        - 不存原始提示词/模型输出（体积大、无复盘价值）

        数据结构随分析器演进，因此这里显式挑字段而不是
        `model_dump()` —— 后者会把内部实现细节也写进历史记录，
        将来改字段时旧记录的含义就不可控了。
        """

        if analysis is None:
            return None

        anchors = {}

        try:
            for key, value in analysis.anchor_summary().items():
                if isinstance(value, dict):
                    anchors[key] = value.get("anchor")
        except Exception:  # noqa: BLE001
            anchors = {}

        return {
            "missing_points": list(analysis.missing_points or []),
            "anchors": anchors,
            "sample_count": getattr(analysis, "sample_count", None),
        }

    async def get_question_answer(
        self,
        question_id: int,
    ):
        return await self.repository.get_by_question_id(
            question_id=question_id
        )
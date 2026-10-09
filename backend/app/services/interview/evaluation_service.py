from app.core.config import settings
from app.repositories.interview_evaluation_repository import (
    InterviewEvaluationRepository,
)
from app.repositories.interview_session_repository import (
    InterviewSessionRepository,
)
from app.services.interview.evaluator import (
    InterviewEvaluator,
    online_evaluation_samples,
)
from app.services.interview.interview_state_machine import (
    SessionStatus,
    TransitionTrigger,
    assert_transition,
)
from app.services.interview_session_service import (
    InterviewSessionService,
)


class InterviewEvaluationService:
    """对整场面试做评价并落库。

    状态推进遵循 ADR-025：**只在外层调用成功后才推进状态**。

    评价的终态是 completed，而 completed 是**真终态**（ADR-026）：
    评完不可重评。理由见计划书 §10.5 ——
    重跑同一场面试只改变 LLM 采样（即测量噪声），
    不产生新信息；要重练应开新会话。

    失败时不会走进死胡同：评价调用若抛错，会话停在
    `evaluating`，再次调用本方法即可重试 ——
    `evaluating → summarizing` 本身是合法转移，无需额外规则。
    """

    def __init__(self, db):
        self.db = db
        self.session_repository = InterviewSessionRepository(db)
        self.evaluation_repository = InterviewEvaluationRepository(db)
        # 用会话服务补记轨迹（record_status_change），
        # 避免评价服务自己依赖轨迹模型 —— 那是会话服务的职责。
        self.session_service = InterviewSessionService(db)
        self.evaluator = InterviewEvaluator()

    async def evaluate_session(self, session_id: int):
        rows = await self.session_repository.get_session_questions_with_answers(
            session_id=session_id
        )

        if not rows:
            raise ValueError("面试不存在或面试中没有问题")

        # 取会话并**先校验状态**：只有 evaluating 允许进入评价。
        # 先校验再调 LLM，避免花费一次完整评价调用之后
        # 才发现会话状态根本不该评价。
        interview_session = (
            await self.session_repository.get_by_id(session_id)
        )

        if interview_session is None:
            raise ValueError(f"面试会话不存在: {session_id}")

        assert_transition(
            interview_session.status,
            SessionStatus.SUMMARIZING,
        )

        interview_content = self._build_interview_content(rows)

        # 线上评价路径，显式使用线上采样数（默认 1 次）。
        # 理由与逐轮分析相同：延迟优先，可复现性由离线评估保证
        # （见 MIND_FLOW_PLAN.md §8A.8）。
        evaluation = await self.evaluator.evaluate(
            interview_content=interview_content,
            repeats=online_evaluation_samples(),
        )

        saved_evaluation = await self.evaluation_repository.create(
            session_id=session_id,
            overall_score=evaluation.overall_score,
            technical_score=evaluation.technical_score,
            project_score=evaluation.project_score,
            communication_score=evaluation.communication_score,
            feedback=evaluation.feedback,
            # 结构化评价内容必须落库：
            # 它们是 Phase 6「能力画像与训练建议」的前置数据。
            strengths=evaluation.strengths,
            weaknesses=evaluation.weaknesses,
            suggestions=evaluation.suggestions,
            scoring_details=self._build_scoring_details(
                evaluation=evaluation,
            ),
        )

        # 评价已成功落库，此时才推进状态：
        # evaluating → summarizing → completed
        #
        # 两步分开做是因为 summarizing 的语义是
        # "正在形成最终反馈"，而 completed 是"只读复盘"。
        # 评价写入成功即完成了 summarizing 阶段。
        #
        # 这里直接调仓储（而不是 apply_transition）：评价服务
        # 持有的是仓储依赖，且 evaluating → summarizing → completed
        # 是评价服务内部的固定链条，没有分支。
        # 但"直接写"会让轨迹缺口，因此逐条补记 ——
        # 这是 §10.6 要求的可审计性，不能只靠状态校验。
        await self.session_service.record_status_change(
            interview_session=interview_session,
            to_status=SessionStatus.SUMMARIZING,
            trigger=TransitionTrigger.EVALUATION_STARTED,
        )
        await self.session_repository.update_status(
            interview_session=interview_session,
            status=SessionStatus.SUMMARIZING.value,
        )

        await self.session_service.record_status_change(
            interview_session=interview_session,
            to_status=SessionStatus.COMPLETED,
            trigger=TransitionTrigger.EVALUATION_COMPLETED,
        )
        completed_session = (
            await self.session_repository.update_status(
                interview_session=interview_session,
                status=SessionStatus.COMPLETED.value,
            )
        )

        return {
            "evaluation": evaluation,
            "saved_evaluation": saved_evaluation,
            "session_status": completed_session.status,
        }

    @staticmethod
    def _build_interview_content(rows) -> str:
        """把会话的问答拼成评价用的文本。"""

        interview_parts = []

        for question, answer in rows:
            interview_parts.append(
                f"问题：{question.question}"
            )

            if answer:
                interview_parts.append(
                    f"回答：{answer.answer}"
                )
            else:
                interview_parts.append(
                    "回答：未回答"
                )

            interview_parts.append("")

        return "\n".join(interview_parts)

    @staticmethod
    def _build_scoring_details(evaluation) -> dict:
        """构造评分明细，使历史分数可事后解释。

        记录锚点、采样次数与每次采样的分数。

        为什么必须存：
        线上只采 1 次、离线采 3 次（ADR-021），
        因此**同一个回答在不同口径下的分数可能不同**。
        只留一个数字时，出现"两次分数不同"就无法判断
        是档位跳档、模型变更，还是采样口径不同。
        有了锚点与采样明细，这类问题可以事后复现。
        """

        return {
            "anchors": evaluation.anchor_summary(),
            "sample_count": evaluation.sample_count,
            "sampled_scores": evaluation.sampled_scores,
            # 记录评分口径快照。
            # 若将来调整 online_evaluation_samples，
            # 历史记录仍能说明自己是在什么口径下产生的。
            "scoring_config": {
                "path": "online",
                "evaluation_samples": online_evaluation_samples(),
                "model": settings.llm_model or "(default)",
            },
        }

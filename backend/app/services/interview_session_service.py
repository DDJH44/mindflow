from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview.interview_status_history import (
    InterviewStatusHistory,
)
from app.repositories.interview_session_repository import (
    InterviewSessionRepository,
)
from app.repositories.project_repository import ProjectRepository
from app.services.interview.interview_state_machine import (
    PAUSABLE_STATUSES,
    SessionStatus,
    TransitionTrigger,
    allowed_transitions,
    assert_transition,
    normalize_status,
    resolve_resume_target,
)
from app.services.interview.interview_termination import (
    normalize_max_questions,
)


class InterviewSessionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = InterviewSessionRepository(db)
        self.project_repository = ProjectRepository(db)

    async def create_session(
        self,
        project_id: int,
        user_id: int,
        interview_type: str = "technical",
        target_role: str | None = None,
        max_questions: int | None = None,
    ):
        """创建面试会话。

        `user_id` 必填且在此**核对**：它会写进会话，成为
        "这场面试属于谁"的权威记录。若与项目所有者不一致，
        说明调用方逻辑有问题，必须立刻失败 ——
        写进去之后所有权校验就再也分不清谁是对的。

        `max_questions` 会经 `normalize_max_questions` 夹取到
        允许范围（见 interview_termination）——
        预算是可配置项，配置错误不应该让整场面试无法开始，
        但也不能让一个误填的大数字把额度烧穿。
        """

        project = await self.project_repository.get_by_id(project_id)

        if project is None:
            raise ValueError(f"项目不存在: {project_id}")

        if project.owner_id != user_id:
            raise ValueError(
                f"用户 {user_id} 不是项目 {project_id} 的所有者；"
                "不能为他人项目创建面试会话"
            )

        return await self.repository.create(
            project_id=project_id,
            user_id=user_id,
            interview_type=interview_type,
            target_role=target_role,
            max_questions=normalize_max_questions(max_questions),
        )

    async def get_session(
        self,
        interview_id: int,
    ):
        return await self.repository.get_by_id(interview_id)

    async def _record_history(
        self,
        interview_session,
        from_status: str,
        to_status: str,
        trigger: TransitionTrigger | str,
    ) -> None:
        """把一次转移写入轨迹表（不提交）。

        **必须与状态变更在同一次事务里提交**：
        若历史先独立提交而状态随后失败，轨迹里就会出现
        一次"从未发生"的转移 —— 那是比没有轨迹更糟的审计结果
        （它会把人引向错误的方向）。
        因此这里只 add，不 commit，交给调用方的
        `update_status` 一起提交（或随 rollback 一起丢弃）。

        同一状态不记录：状态机的转移表本就拒绝自转移，
        但直接调用本方法（例如将来新增调用点）时仍要拦一道，
        避免在库里留下无意义的噪声。

        记的是**转移发起时**的原始状态值，不做规范化 ——
        轨迹要原样反映当时库里是什么（含历史 "created"）。
        """

        if from_status == to_status:
            return

        self.db.add(
            InterviewStatusHistory(
                session_id=interview_session.id,
                from_status=from_status,
                to_status=to_status,
                trigger=(
                    trigger.value
                    if isinstance(trigger, TransitionTrigger)
                    else str(trigger)
                ),
            )
        )

    async def transition_status(
        self,
        interview_id: int,
        target_status: SessionStatus | str,
        commit: bool = True,
        trigger: TransitionTrigger | str = (
            TransitionTrigger.UNSPECIFIED
        ),
    ):
        """把会话转移到目标状态。

        非法转移抛出 `InvalidTransitionError`（ValueError 子类），
        由调用方（Router）转换为 409，而不是静默忽略。

        为什么必须校验而不是直接写库：
        状态机要保证"可恢复、可审计"（ADR-010）。
        若允许任意写状态，轨迹就无法解释
        会话是怎么从 evaluating 直接跳到 completed 的。

        commit=False 时不提交，留给调用方与业务数据一起提交。
        业务动作（提示词、LLM 调用）会失败，状态若提前独立提交，
        失败后库里就会留下与业务数据不匹配的状态（ADR-025）。

        返回 None 表示会话不存在。
        """

        interview_session = await self.repository.get_by_id(
            interview_id
        )

        if interview_session is None:
            return None

        from_status = interview_session.status

        validated = assert_transition(
            from_status,
            target_status,
            resume_status=interview_session.resume_status,
        )

        await self._record_history(
            interview_session=interview_session,
            from_status=from_status,
            to_status=validated.value,
            trigger=trigger,
        )

        return await self.repository.update_status(
            interview_session=interview_session,
            status=validated.value,
            commit=commit,
        )

    async def apply_transition(
        self,
        interview_session,
        target_status: SessionStatus | str,
        commit: bool = True,
        trigger: TransitionTrigger | str = (
            TransitionTrigger.UNSPECIFIED
        ),
    ):
        """对**已加载的会话对象**应用状态转移。

        与 `transition_status` 的区别：不重新查库。
        用途是调用方已经持有会话对象（例如 Turn 服务已经查过
        会话用于校验），再查一次是多余的往返。

        commit=False 时会在内存中同步 status 并 flush，
        因此调用方看到的对象状态与实际待提交的数据一致 ——
        这很重要：ORM 对象是共享引用，状态不同步会让调用方
        读到过期的值。
        """

        from_status = interview_session.status

        validated = assert_transition(
            from_status,
            target_status,
            resume_status=interview_session.resume_status,
        )

        await self._record_history(
            interview_session=interview_session,
            from_status=from_status,
            to_status=validated.value,
            trigger=trigger,
        )

        return await self.repository.update_status(
            interview_session=interview_session,
            status=validated.value,
            commit=commit,
        )

    async def record_status_change(
        self,
        interview_session,
        to_status: SessionStatus | str,
        trigger: TransitionTrigger | str = (
            TransitionTrigger.UNSPECIFIED
        ),
    ) -> None:
        """为**直接改写 status 字段**的调用方补记轨迹（不提交）。

        为什么需要这个入口：
        某些调用点绕过 `apply_transition` 直接设 `status`
        （例如生成题目时把 `planned` 置为 `asking` —— 那是
        planned 唯一合法的常规后继，不需要状态机再选一次）。
        但"直接写"会让轨迹出现缺口，事后无法解释状态怎么变的。

        因此约定：**凡直接改 status，必须调用本方法补记轨迹**。
        它只 add 不 commit，由调用方与状态变更一起提交。

        注意它不校验转移合法性 —— 校验责任在调用方。
        本方法只负责"把已发生的变化记下来"。
        """

        to_value = (
            to_status.value
            if isinstance(to_status, SessionStatus)
            else str(to_status)
        )

        await self._record_history(
            interview_session=interview_session,
            from_status=interview_session.status,
            to_status=to_value,
            trigger=trigger,
        )

    async def allowed_next_status(
        self,
        interview_session,
    ) -> list[str]:
        """当前会话允许转移到的状态（供前端/接口查询）。

        对 paused 会话会结合 `resume_status` 动态计算，
        因此前端不需要自己实现状态机规则。
        """

        return sorted(
            item.value
            for item in allowed_transitions(
                interview_session.status,
                resume_status=interview_session.resume_status,
            )
        )

    async def pause_session(
        self,
        interview_id: int,
        reason: str | None = None,
    ):
        """暂停会话，并记住"从哪来"以便恢复。

        只有 `PAUSABLE_STATUSES` 里的状态可以暂停：
        - `draft` 还没有任何进度，暂停没有意义
        - 终态不允许暂停

        返回 None 表示会话不存在。
        """

        interview_session = await self.repository.get_by_id(
            interview_id
        )

        if interview_session is None:
            return None

        current = normalize_status(interview_session.status)

        if current not in PAUSABLE_STATUSES:
            raise ValueError(
                f"当前状态 {current.value} 不允许暂停；"
                f"可暂停的状态为 "
                f"{sorted(item.value for item in PAUSABLE_STATUSES)}"
            )

        assert_transition(
            interview_session.status,
            SessionStatus.PAUSED,
            resume_status=current.value,
        )

        # 暂停三个字段必须同时写入：状态为 paused 而 resume_status
        # 为空会让会话变成只能取消的死胡同。
        # 直接设在 ORM 对象上，由仓储在自己的事务里一并提交。
        await self._record_history(
            interview_session=interview_session,
            from_status=interview_session.status,
            to_status=SessionStatus.PAUSED.value,
            trigger=TransitionTrigger.USER_PAUSED,
        )

        interview_session.resume_status = current.value
        interview_session.pause_reason = reason
        interview_session.paused_at = datetime.now(
            timezone.utc
        ).replace(tzinfo=None)

        return await self.repository.update_status(
            interview_session=interview_session,
            status=SessionStatus.PAUSED.value,
            commit=True,
        )

    async def _resolve_resume_target(
        self,
        interview_session,
    ) -> SessionStatus | None:
        """确定 paused 会话的恢复目标，缺 `resume_status` 时按事实推断。

        背景（D56）：`resume_status` 为空且状态为 `paused` 的会话
        在状态机里只能取消 —— **永久卡死**，既不能恢复也不能结束
        （用户点"结束并生成报告"会得到
        `非法状态转移：paused → waiting_for_answer`）。

        推断依据是问答记录，不是猜：

        - 存在**未作答的题目** → 当初停在 `asking`
        - 有过作答但当前没有未答题 → 当初停在 `waiting_for_answer`

        推断不出来（一题都没生成）时返回 None，由调用方报错。
        """

        if interview_session.resume_status:
            # 有记录就用记录（resolve_resume_target 内部会校验它是否
            # 属于可暂停状态，非法值会被丢弃并转入推断）
            return resolve_resume_target(
                resume_status=interview_session.resume_status,
                answered_any=False,
                has_unanswered_question=False,
            )

        rows = await self.repository.get_session_questions_with_answers(
            interview_session.id
        )

        if not rows:
            return None

        has_unanswered = any(
            answer is None for _question, answer in rows
        )
        answered_any = any(
            answer is not None for _question, answer in rows
        )

        return resolve_resume_target(
            resume_status=None,
            answered_any=answered_any,
            has_unanswered_question=has_unanswered,
        )

    async def resume_session(self, interview_id: int):
        """从 paused 恢复到暂停前的状态。

        恢复后清空 `resume_status` / `pause_reason` / `paused_at`，
        避免残留值被误读为"当前仍挂起的目标"。

        返回 None 表示会话不存在。
        """

        interview_session = await self.repository.get_by_id(
            interview_id
        )

        if interview_session is None:
            return None

        if normalize_status(interview_session.status) is not (
            SessionStatus.PAUSED
        ):
            raise ValueError(
                f"会话当前状态为 {interview_session.status}，"
                "只有 paused 才能恢复"
            )

        target = await self._resolve_resume_target(interview_session)

        if target is None:
            raise ValueError(
                "该会话缺少 resume_status，且没有可用于推断的问答记录，"
                "无法确定恢复到哪个状态；这类会话只能取消"
            )

        validated = assert_transition(
            interview_session.status,
            target,
            resume_status=target,
        )

        # 恢复后清空暂停信息，避免残留值被误读为
        # "当前仍挂起的目标"。
        await self._record_history(
            interview_session=interview_session,
            from_status=interview_session.status,
            to_status=validated.value,
            trigger=TransitionTrigger.USER_RESUMED,
        )

        interview_session.resume_status = None
        interview_session.pause_reason = None
        interview_session.paused_at = None

        return await self.repository.update_status(
            interview_session=interview_session,
            status=validated.value,
            commit=True,
        )

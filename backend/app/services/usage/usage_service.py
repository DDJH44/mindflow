"""用量计量与配额校验的读写服务。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.usage.usage_quota import (
    QuotaDecision,
    QuotaExceeded,
    QuotaPair,
    UsageMetric,
    current_period,
    decide_question_quota,
    decide_quota,
    normalize_question_quota,
    normalize_quota,
)


class UsageService:
    """按用户 / 账期 / 指标计量用量，并校验配额。

    为什么"先自增再判定"而不是"先判定再自增"：
    后者在并发下会一起通过 —— 两个请求同时读到 used=9 / quota=10，
    都认为还有额度。先自增可以让每个请求拿到不同的计数，
    只有真正超出的那个被拒绝。

    代价是"被拒绝的那次也计了数"。这是可接受的：
    被拒绝的请求不会产生实际成本，计数略高不会让用户损失额度
    （下个月重置），而放行过多会真实花钱。
    宁可多计，不可漏计。
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _increment(
        self,
        user_id: int,
        period: str,
        metric: UsageMetric,
    ) -> int:
        """原子自增并返回自增后的计数。

        用 PostgreSQL 的 `INSERT ... ON CONFLICT DO UPDATE ...
        RETURNING` 一条语句完成，避免"先查再写"的竞态。
        """

        result = await self.db.execute(
            text(
                """
                INSERT INTO interview_usage
                    (user_id, period, metric, count,
                     created_at, updated_at)
                VALUES
                    (:user_id, :period, :metric, 1,
                     NOW(), NOW())
                ON CONFLICT (user_id, period, metric)
                DO UPDATE SET
                    count = interview_usage.count + 1,
                    updated_at = NOW()
                RETURNING count
                """
            ),
            {
                "user_id": user_id,
                "period": period,
                "metric": metric.value,
            },
        )

        return int(result.scalar_one())

    async def ensure_available(
        self,
        user_id: int,
        metric: UsageMetric,
        quota: int | None,
    ) -> QuotaDecision:
        """只**检查**额度，不消费。

        用途：在可能失败的昂贵操作（LLM 调用）之前拦下，
        避免用户明明没额度还要白花一次调用。

        与 `consume` 的分工：
        - `ensure_available` 在 LLM 调用**前**（不写库，只读）
        - `consume` 在成功落库**后**（写库）

        为什么不只用 consume：若在调用前 consume，
        生成失败就白扣了额度；若只在调用后 consume，
        没额度的用户也能触发一次 LLM 调用。

        ⚠️ 并发下 `ensure_available` 只读不原子，理论上
        N 个并发请求可能一起通过。但本题材的额度是"月度上百题"
        的量级，少量超额可接受；**严格的并发控制留给 consume** ——
        它才是唯一的额度事实来源。
        """

        decision = await self.get_usage(
            user_id=user_id,
            metric=metric,
            quota=quota,
        )

        if not decision.allowed:
            raise QuotaExceeded(
                metric=metric,
                period=decision.period,
                used=decision.used,
                quota=decision.quota,
            )

        return decision

    async def consume(
        self,
        user_id: int,
        metric: UsageMetric,
        quota: int | None,
    ) -> QuotaDecision:
        """消费一次用量，超配额时抛出 `QuotaExceeded`。

        返回判定结果（供调用方回显剩余额度）。

        按指标选用不同的夹取上限：场次与题目的合理量级不同
        （场 vs 题），共用一个上限会让其中一边的范围不合理。
        """

        period = current_period()

        used = await self._increment(
            user_id=user_id,
            period=period,
            metric=metric,
        )

        decision = self._decide(
            metric=metric,
            used=used,
            quota=quota,
            period=period,
        )

        if not decision.allowed:
            # 不在这里回滚：调用方可能希望"记录已用尽"这一事实。
            # 由调用方决定是否 rollback —— 但要注意：
            # **已经发生的 LLM 调用不能因为回滚而"没花钱"**，
            # 所以题目用量在生成成功后才消费，失败则不消费
            # （见 interview_engine_service 的调用顺序）。
            raise QuotaExceeded(
                metric=metric,
                period=period,
                used=used,
                quota=decision.quota,
            )

        return decision

    @staticmethod
    def _decide(
        metric: UsageMetric,
        used: int,
        quota: int | None,
        period: str,
    ) -> QuotaDecision:
        """按指标选择对应的判定函数。"""

        if metric is UsageMetric.QUESTION_GENERATED:
            return decide_question_quota(
                used=used, quota=quota, period=period
            )

        return decide_quota(used=used, quota=quota, period=period)

    async def get_usage(
        self,
        user_id: int,
        metric: UsageMetric,
        quota: int | None,
        period: str | None = None,
    ) -> QuotaDecision:
        """查询当前用量与剩余额度（只读，不消费）。"""

        target_period = period or current_period()

        used = await self._current_count(
            user_id=user_id,
            period=target_period,
            metric=metric,
        )

        return self._decide(
            metric=metric,
            used=used,
            quota=quota,
            period=target_period,
        )

    async def _current_count(
        self,
        user_id: int,
        period: str,
        metric: UsageMetric,
    ) -> int:
        """读取某指标在某账期的已用量（不存在则为 0）。"""

        result = await self.db.execute(
            text(
                """
                SELECT count FROM interview_usage
                WHERE user_id = :user_id
                  AND period = :period
                  AND metric = :metric
                """
            ),
            {
                "user_id": user_id,
                "period": period,
                "metric": metric.value,
            },
        )

        row = result.scalar_one_or_none()

        return int(row) if row is not None else 0

    async def get_quota_pair(
        self,
        user_id: int,
        interview_quota: int | None,
        question_quota: int | None,
    ) -> QuotaPair:
        """读取**两份**额度的合成视图（只读）。

        为什么成对返回：任何一个先耗尽都会挡住用户，
        只报"剩余 3 场"会让人以为还能用 ——
        实际可能题目额度已经见底。
        """

        period = current_period()

        interviews_used = await self._current_count(
            user_id=user_id,
            period=period,
            metric=UsageMetric.INTERVIEW_STARTED,
        )
        questions_used = await self._current_count(
            user_id=user_id,
            period=period,
            metric=UsageMetric.QUESTION_GENERATED,
        )

        return QuotaPair(
            period=period,
            interviews_used=interviews_used,
            interview_quota=normalize_quota(interview_quota),
            questions_used=questions_used,
            question_quota=normalize_question_quota(question_quota),
        )

    async def reset(
        self,
        user_id: int,
        metric: UsageMetric,
        period: str | None = None,
    ) -> None:
        """清空某账期的用量。

        用于运维补救（例如误计费）。**不对外暴露接口** ——
        用户可以自己重置额度就等于没有配额。
        """

        target_period = period or current_period()

        await self.db.execute(
            text(
                """
                DELETE FROM interview_usage
                WHERE user_id = :user_id
                  AND period = :period
                  AND metric = :metric
                """
            ),
            {
                "user_id": user_id,
                "period": target_period,
                "metric": metric.value,
            },
        )
        await self.db.commit()


__all__ = [
    "UsageService",
    "QuotaDecision",
    "QuotaExceeded",
    "QuotaPair",
    "UsageMetric",
    "current_period",
    "normalize_quota",
    "normalize_question_quota",
]

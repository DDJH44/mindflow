"""异步文档索引 worker（MIND_FLOW_PLAN.md §28）。

上传只负责"落盘 → 解析 → 切块 → 入队"，把**嵌入**这一步移到
这个 worker 里。此前大文件同步嵌入要阻塞 37 秒以上。

## 任务领取必须是原子的

```sql
UPDATE document_index_jobs SET status='running', ...
WHERE id = (SELECT id FROM document_index_jobs
            WHERE status='pending' ORDER BY id
            FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING ...
```

先 `SELECT` 再 `UPDATE` 会让两个 worker 抢到同一个任务 ——
同一份文档被嵌入两次。危害不是报错，而是**白花钱且完全看不出来**。
`FOR UPDATE SKIP LOCKED` 让并发 worker 各自领到不同任务、互不阻塞。

## 崩溃恢复靠租约

worker 领走任务后崩溃，任务会永远停在 `running`。
`recover_expired_leases` 把超时未完成的任务放回 `pending`。

没有这条时的故障表现是"界面一直转圈、日志里没有任何报错" ——
最难查的一类问题。

## 重试上限

`MAX_ATTEMPTS` 之后标 `failed` 并停止重试。
文档本身已经是 `failed`、用户可以在资料页点"重试索引"。
无限重试没有意义：一个因内容问题的文档每次都会同样失败，
而无限重试只会持续消耗端点额度。
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger

logger = get_logger("app.index_worker")

# 单次租约时长。
#
# 取 10 分钟：实测 800KB（约 1800 块）耗尽约 38 秒，
# 但端点抖动时可能慢很多倍。租约过短会让**正常但慢**的任务
# 被判定为超时并重复执行 —— 那是白花钱。
# 过长则真的崩溃后要等很久才恢复。
LEASE_SECONDS = 600

# 最多尝试次数。
#
# 取 3：端点抖动通常 1 次重试就够；连续 3 次失败更可能是
# 内容或配置问题，继续重试不会变好，只会持续消耗额度。
MAX_ATTEMPTS = 3


class DocumentIndexWorker:
    """从任务表领取并执行文档索引。"""

    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory

    async def recover_expired_leases(self) -> int:
        """把租约超时、仍停在 `running` 的任务放回 `pending`。

        返回恢复的任务数。
        """

        async with self.session_factory() as db:
            result = await db.execute(
                text(
                    """
                    UPDATE document_index_jobs
                    SET status = 'pending',
                        lease_expires_at = NULL,
                        updated_at = now()
                    WHERE status = 'running'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < now()
                    RETURNING id, document_id
                    """
                )
            )
            recovered = list(result.all())
            await db.commit()

        if recovered:
            logger.warning(
                "恢复了 %d 个租约超时的索引任务：%s",
                len(recovered),
                [(row[0], row[1]) for row in recovered],
            )

        return len(recovered)

    async def claim_one(self) -> tuple[int, int] | None:
        """原子地领一个待处理任务。返回 (job_id, document_id)。

        没有可领的任务时返回 None。
        """

        async with self.session_factory() as db:
            result = await db.execute(
                text(
                    """
                    UPDATE document_index_jobs
                    SET status = 'running',
                        attempts = attempts + 1,
                        lease_expires_at = now()
                            + make_interval(secs => :lease),
                        updated_at = now()
                    WHERE id = (
                        SELECT id FROM document_index_jobs
                        WHERE status = 'pending'
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id, document_id
                    """
                ),
                {"lease": LEASE_SECONDS},
            )
            row = result.first()
            await db.commit()

        if row is None:
            return None

        return int(row[0]), int(row[1])

    async def finish(
        self,
        job_id: int,
        error: str | None = None,
    ) -> None:
        """标记任务结束。"""

        async with self.session_factory() as db:
            if error is None:
                await db.execute(
                    text(
                        """
                        UPDATE document_index_jobs
                        SET status = 'done',
                            last_error = NULL,
                            lease_expires_at = NULL,
                            updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": job_id},
                )
            else:
                # 判断是否还有重试机会，决定收尾状态。
                #
                # 用 `attempts` 而不是调用方的计数：它是数据库里的
                # 事实，多个 worker 并发时也不会算错。
                await db.execute(
                    text(
                        """
                        UPDATE document_index_jobs
                        SET status = CASE
                                WHEN attempts >= :max THEN 'failed'
                                ELSE 'pending'
                            END,
                            last_error = :error,
                            lease_expires_at = NULL,
                            updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": job_id,
                        "error": error[:2000],
                        "max": MAX_ATTEMPTS,
                    },
                )

            await db.commit()

    async def run_once(self) -> bool:
        """领一个任务并执行。返回是否处理了任务。

        每次领任务前先做租约恢复：这样不需要单独的清理定时器，
        而恢复的时机正好是"要干活之前"。
        """

        await self.recover_expired_leases()

        claimed = await self.claim_one()

        if claimed is None:
            return False

        job_id, document_id = claimed

        logger.info(
            "开始索引文档 %d（任务 %d）", document_id, job_id
        )

        try:
            await self._index_document(document_id)
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"

            logger.error(
                "索引文档 %d 失败（任务 %d）：%s",
                document_id,
                job_id,
                message,
            )

            await self.finish(job_id, error=message)

            # 文档级状态也要如实反映，否则界面会一直显示
            # "已切块·未索引"而用户不知道发生了什么。
            await self._mark_document_failed(document_id, message)

            return True

        await self.finish(job_id)

        logger.info("文档 %d 索引完成（任务 %d）", document_id, job_id)

        return True

    async def _index_document(self, document_id: int) -> None:
        """真正执行索引。"""

        from app.services.embedding_pipeline_service import (
            EmbeddingPipelineService,
        )

        async with self.session_factory() as db:
            pipeline = EmbeddingPipelineService(db)
            embedded = await pipeline.embed_document(document_id)

            await db.execute(
                text(
                    "UPDATE documents SET status = 'embedded', "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": document_id},
            )
            await db.commit()

        logger.info(
            "文档 %d 已嵌入 %d 段", document_id, embedded
        )

    async def _mark_document_failed(
        self,
        document_id: int,
        message: str,
    ) -> None:
        try:
            async with self.session_factory() as db:
                await db.execute(
                    text(
                        "UPDATE documents SET status = 'failed', "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": document_id},
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "标记文档 %d 失败状态时出错：%s", document_id, exc
            )

    async def run_forever(
        self,
        poll_interval: float = 2.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """持续消费任务，直到 `stop_event` 被设置。

        `poll_interval` 用 `sleep` 而不是长轮询：实现简单，
        代价是空队列时每 2 秒一次索引查询 —— 那个代价
        远小于引入新基础设施（Redis）来消除它。
        """

        logger.info(
            "索引 worker 已启动（轮询间隔 %.1fs，最多尝试 %d 次）",
            poll_interval,
            MAX_ATTEMPTS,
        )

        while stop_event is None or not stop_event.is_set():
            try:
                handled = await self.run_once()
            except Exception as exc:  # noqa: BLE001
                # 循环本身不能因为单次异常而退出 ——
                # 否则一次数据库抖动就会让所有文档永久卡住。
                logger.error("worker 循环异常：%s", exc)
                handled = False

            if not handled:
                try:
                    if stop_event is not None:
                        # 等待时也要能被唤醒停止
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=poll_interval,
                        )
                    else:
                        await asyncio.sleep(poll_interval)
                except asyncio.TimeoutError:
                    pass

        logger.info("索引 worker 已停止")

"""时间处理约定（§35）。

## 本项目的存储约定：**朴素 UTC**

数据库里所有时间列都是 `timestamp without time zone`（20 个列，实测），
存的是 **UTC 的时刻，但去掉时区信息**（naive）。

### 为什么保留这个约定，而不是迁移到 `timestamptz`

迁移能带来的实质收益只有一个：**时区语义显式**。
而代价是：

- 20 个列的 `ALTER TABLE` + 既有数据转换
  （naive 值必须按 UTC 显式解释，否则会被错解为服务器本地时间）
- **API 输出格式会变**（多出 `+00:00`），而前端已在解析这些字符串 ——
  §31 的教训是"界面不得承诺后端做不到的事"，反过来也一样：
  后端格式变更会静默破坏前端
- 而库里 20 个列**全是朴素值**、SQL 侧 `now()` 实测已是 UTC
  （容器时区 `Etc/UTC`）—— 迁移换来的只是"写法更好看"

**因此选择：保持朴素 UTC，但把这件约定变成一个有名有据的东西** ——
即本模块。这样：

- 不再依赖 `datetime.utcnow()`（Python 3.12+ 已弃用）
- 不再让各处自己写 `.replace(tzinfo=None)`（那种写法散落时
  很容易漏掉一处，而漏掉的那处会写入**本地时间**）
- 有一个明确的地方说明"为什么是朴素 UTC"

### 为什么不用 `datetime.utcnow()`

它有两个问题：

1. **已弃用**（Python 3.12+，会报 `DeprecationWarning`）
2. **返回朴素值**，因此无法在类型上区分"UTC 的朴素值"
   与"本地时间的朴素值" —— 而那正是混存的温床

`utcnow()` 返回 `datetime`（无 tzinfo），
而 `datetime.now(timezone.utc)` 返回**带 tzinfo** 的值。
本模块的 `utc_now()` 刻意返回**朴素值** —— 因为列是朴素的 ——
但它是从 aware 值显式转换来的，因此**语义无歧义**。

### 与 SQL 侧 `func.now()` 的关系

有些列用 `server_default=func.now()`（SQL 侧生成）。
实测容器时区是 `Etc/UTC`，因此 `now()` 写入的**恰好**是 UTC。

⚠️ 但那依赖**容器配置**，不是代码保证 ——
若换一个时区不是 UTC 的服务器，SQL 侧会写入本地时间的朴素值，
与 Python 侧的 UTC **混存**，而两边看起来都"正常"。

`tests/test_time_convention.py` 会把这一点断言出来：
**数据库时区必须是 UTC**，否则明确报错而不是静默混存。
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """当前 UTC 时刻，**朴素值**（`tzinfo=None`）。

    这是本项目写入数据库时间的**唯一正确方式**。

    ```python
    from app.core.time_convention import utc_now

    class Project(Base):
        created_at: Mapped[datetime] = mapped_column(
            DateTime, default=utc_now
        )
    ```

    ## 为什么显式去掉 tzinfo 而不是直接 `datetime.now()`

    `datetime.now()`（不带参数）返回**本地时间** ——
    在 UTC+8 的机器上它比 UTC 快 8 小时。

    两者都是"朴素 datetime"，**类型上完全一样**，
    因此混用不会报错，只会让时间**静默偏移**。
    实测本项目曾同时存在这两种写法。

    本函数的 `.replace(tzinfo=None)` 是**刻意的、有名字的**转换：
    读代码的人能看到"这里在把 aware 转成朴素"，
    而不是看到一个裸的 `datetime.now()` 去猜它是什么时区。
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_now_iso() -> str:
    """当前 UTC 时刻的 ISO 字符串（带 `+00:00`）。

    **只用于报告与日志的元信息**，不用于写数据库 ——
    报告是给人看的，带上时区更有用。
    """

    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    """文件名用的时间戳（`YYYYmmdd_HHMMSS`，UTC）。

    为什么用 UTC 而不是本地时间：评估报告是**跨机器**参照的产物
    （§15 要求版本化）。用本地时间会让"哪份报告更新"
    取决于生成它的机器所在时区。
    """

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

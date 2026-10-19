"""MindFlow 后端运维脚本：诊断、测量、清理与管理工具。

每个脚本可独立运行（cwd 必须是 backend/）：

    uv run python -m scripts.<脚本名>

常用：

- 依赖健康检查：scripts.doctor
- 邀请码管理：  scripts.manage_invites
- 生产密钥生成：scripts.generate_secret

分类约定：

- check_* / diagnose_* / inspect_* / list_* —— 只读诊断，不改数据
- measure_* —— 性能与质量测量
- cleanup_* —— 数据清理（先看清说明再跑）
- reembed_* / repair_* / migrate_* / recreate_* —— 索引与数据修复
- seed_* —— 造测试数据

历史注：2026-10-19 从 `app/core/` 迁入 ——
core 应只含应用运行必需的核心模块（配置 / 安全 / 日志 / 限流等），
运维工具混在里面会掩盖依赖边界（core 被应用 import，工具只被人手动运行）。
"""

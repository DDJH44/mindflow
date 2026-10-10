from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.repositories.document_repository import DocumentRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.document import DocumentResponse
from app.core.dependencies import get_current_user
from app.services.document_parser import (
    DocumentParser,
    NoExtractableText,
    UnsupportedDocumentType,
)
from app.repositories.document_chunk_repository import DocumentChunkRepository
from app.services.chunking_service import ChunkingService
from app.services.milvus_vector_store import MilvusVectorStore


router = APIRouter(
    prefix="/api/projects",
    tags=["Documents"],
)


UPLOAD_DIR = Path("uploads/documents")
UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


def _discard_upload(file_path: Path) -> None:
    """删除刚落盘但确认不可用的文件。

    用于"还来得及回头"的失败：解析不通过时把文件清掉，
    数据库里也还没建记录 —— 用户重试不会留下任何残留。
    """

    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        # 删不掉不影响对用户的响应，最坏情况是留一个孤儿文件
        pass


@router.get(
    "/{project_id}/documents",
    response_model=list[DocumentResponse],
)
async def list_documents(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """列出项目下的文档。

    没有这个端点时前端无法显示"已经传了什么"，
    用户只能靠记忆与重复上传试错。
    """

    project_repository = ProjectRepository(db)

    project = await project_repository.get_by_id(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该项目",
        )

    document_repository = DocumentRepository(db)

    return await document_repository.get_by_project(project_id)


@router.post(
    "/{project_id}/documents/{document_id}/embed",
    response_model=DocumentResponse,
    # 202：任务已入队，索引在后台进行 —— 与上传保持一致。
    # 此前这里同步嵌入，大文档会让用户继续等 30 秒以上。
    status_code=status.HTTP_202_ACCEPTED,
)
async def embed_document_endpoint(
    project_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """把文档重新排入索引队列（幂等，可重复调用）。

    存在的理由：索引是**异步**的，失败时文档会停在 `chunked` 或
    `failed`。此时检索不到它，面试只会出通用题。
    这个端点让用户能重试，而不必删掉重传（重传会丢掉已有的 chunk）。

    为什么改成入队而不是同步执行：同步版本在"重试一个 800KB 文档"
    时同样要阻塞 30 秒以上。既然上传已经异步，重试也必须异步 ——
    否则用户会看到"上传很快、重试却卡住"这种矛盾的行为。
    """

    project_repository = ProjectRepository(db)

    project = await project_repository.get_by_id(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该项目",
        )

    document_repository = DocumentRepository(db)

    document = await document_repository.get_by_id(document_id)

    if not document or document.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    # 没有 chunk 就没什么可嵌入的 —— 排队只会让 worker
    # 空转一次并把文档标成失败。这里直接说清楚。
    chunk_repository = DocumentChunkRepository(db)
    chunks = await chunk_repository.get_by_document(document_id)

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "这份资料还没有文本块，无法建立索引。"
                "请确认文件内容是可提取的文字，或重新上传。"
            ),
        )

    await enqueue_index_job(db, document_id)

    await db.refresh(document)

    return document


@router.delete(
    "/{project_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    project_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """删除文档及其向量索引。

    **必须同时删向量**：Milvus 里的向量一旦成为孤儿
    （DB 里没有对应 chunk），检索时会命中它们但回表取不到正文，
    只能静默跳过 —— **却仍然占用 top-k 名额**，
    于是实际召回数悄悄变少。用户会以为是"检索变差了"。
    """

    project_repository = ProjectRepository(db)

    project = await project_repository.get_by_id(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该项目",
        )

    document_repository = DocumentRepository(db)

    document = await document_repository.get_by_id(document_id)

    if not document or document.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    # 先取出 chunk id 用于删向量，再删文档（chunk 由外键级联删除）
    chunk_repository = DocumentChunkRepository(db)

    chunks = await chunk_repository.get_by_document(document_id)
    chunk_ids = [chunk.id for chunk in chunks]

    if chunk_ids:
        try:
            await MilvusVectorStore().delete(ids=chunk_ids)
        except Exception as exc:
            # 向量删不掉不该让整个删除失败 —— 但必须说明，
            # 因为留下的孤儿向量会降低后续召回。
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"向量索引删除失败（{type(exc).__name__}）：{exc}。"
                    "文档尚未删除，请确认 Milvus 可用后重试。"
                ),
            ) from exc

    file_path = document.file_path

    await document_repository.delete(document)

    # 顺手删掉磁盘上的文件；删不掉不影响记录已删除这件事
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass

    return None


async def enqueue_index_job(db, document_id: int) -> None:
    """把一个文档的索引任务放进队列（幂等）。

    用 `ON CONFLICT DO NOTHING` 依赖 `document_id` 的唯一约束：
    重复入队（例如用户连点"重试索引"）不该产生第二个任务 ——
    那会让同一份文档被嵌入两次，**白花钱且表面上完全正常**。

    已 `done` / `failed` 的任务会被**重置为 pending**：
    那是"重试"的语义，与"重复入队"不同。
    """

    await db.execute(
        text(
            """
            INSERT INTO document_index_jobs
                (document_id, status, attempts, last_error)
            VALUES (:d, 'pending', 0, NULL)
            ON CONFLICT (document_id) DO UPDATE
            SET status = 'pending',
                attempts = 0,
                last_error = NULL,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE document_index_jobs.status IN ('done', 'failed')
            """
        ),
        {"d": document_id},
    )
    await db.commit()


@router.post(
    "/{project_id}/documents",
    response_model=DocumentResponse,
    # 202 而不是 201：文档已经**落盘、解析、切块**完成，
    # 但索引仍在后台进行，此刻它还不能被检索到。
    # 用 201 会声称"创建完成"，那是过度承诺。
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    project_id: int,
    file: UploadFile = File(...),
    document_type: Literal["resume", "jd", "project", "code", "other"] = "other",
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """上传项目文档"""

    # 1. 检查项目
    project_repository = ProjectRepository(db)

    project = await project_repository.get_by_id(
        project_id
    )

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在",
        )

    # 2. 检查项目所有权
    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该项目",
        )

    # 3. 获取文件扩展名
    original_filename = file.filename or "unknown"

    suffix = Path(
        original_filename
    ).suffix.lower()

    # 3.1 先做类型与体积检查，**在落盘与建记录之前**。
    #
    # 顺序很重要：原先先存文件、建记录，再解析。解析失败时
    # 会留下一条 `status=failed` 的记录与一个磁盘文件 ——
    # 用户看到"上传失败"，界面上却多出一条没用的记录
    # （实测：用户传简历失败两次，库里就多了两条 failed 文档）。
    # 把能提前判断的检查放前面，失败就彻底不留痕迹。
    if suffix not in DocumentParser.SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"不支持的文件类型 {suffix or '（无扩展名）'}。"
                "当前支持："
                f"{'、'.join(DocumentParser.SUPPORTED_SUFFIXES)}"
            ),
        )

    # 4. 生成唯一文件名
    stored_filename = (
        f"{uuid4().hex}{suffix}"
    )

    file_path = UPLOAD_DIR / stored_filename

    # 5. 保存文件
    with file_path.open("wb") as buffer:

        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)

    # 6. 解析文档 —— 在建记录**之前**做。
    #
    # 解析是"能不能用"的判定：图片型 PDF、坏文件、编码异常
    # 都应该在这里被挡住，而不是先污染数据库再报错。
    try:
        content = await DocumentParser.parse(str(file_path))

    except (NoExtractableText, UnsupportedDocumentType) as exc:
        # 用户的问题（文件本身不合适）→ 400，并说明怎么改
        _discard_upload(file_path)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except (ValueError, FileNotFoundError) as exc:
        _discard_upload(file_path)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件解析失败：{exc}",
        ) from exc

    except Exception as exc:
        _discard_upload(file_path)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件解析出错（{type(exc).__name__}）：{exc}",
        ) from exc

    # 7. 创建数据库记录（此时已确认内容可解析）
    document_repository = DocumentRepository(db)

    document = await document_repository.create(
        name=Path(original_filename).stem,
        original_filename=original_filename,
        file_type=suffix.lstrip(".") or "unknown",
        document_type=document_type,
        file_path=str(file_path),
        project_id=project_id,
        content=content,
    )

    # 8. 文档切块
    #
    # 解析已经成功，这里失败属于异常情况（内容边界、存储问题）。
    # 此时记录已建、文件已存，因此**保留记录并标 failed** ——
    # 用户能看到它、也能用删除端点清掉，比静默消失好。
    try:
        chunks = ChunkingService.split_text(
            content,
            chunk_size=500,
            chunk_overlap=100,
        )

        # 守卫：有内容却切不出块，属于不该发生的状态。
        #
        # 为什么必须拦：这类文档会停在"已解析但没有块"——
        # **检索永远命中不到、界面也不显示异常**，是个静默死档。
        # 库里确实有 5 份这样的历史数据（来自切块还没接入上传
        # 流程的旧版本）。与其让前端去猜这种状态，不如在源头保证
        # "有内容的文档必有块"。
        #
        # 为什么标 failed 而不是丢弃：记录与文件已经落盘，
        # 标 failed 让用户能看到、能删除，也能据此反馈问题。
        if not chunks:
            document.status = "failed"

            await db.commit()
            await db.refresh(document)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"文档解析出 {len(content)} 字符，但切不出任何文本块。"
                    "请确认内容是有效文本；若确实如此，这是服务端的"
                    "切块问题，请反馈。"
                ),
            )

        chunk_repository = DocumentChunkRepository(db)

        await chunk_repository.create_chunks(
            document_id=document.id,
            chunks=chunks,
            chunk_metadata={
                "document_type": document.document_type,
            },
        )

        document.status = "chunked"

        await db.commit()
        await db.refresh(document)

    except HTTPException:
        # 上面那条"有内容却切不出块"的守卫抛的就是 HTTPException。
        # 必须原样放行 —— 否则会被下一条 `except Exception` 抓住、
        # 重新包成 500，把 400 与具体提示都丢掉。
        raise

    except Exception as exc:
        document.status = "failed"

        await db.commit()
        await db.refresh(document)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"文本切块失败（{type(exc).__name__}）：{exc}。"
                "记录已保留，可在资料页删除后重试。"
            ),
        ) from exc

    # 9. 入队异步索引（§28）
    #
    # 这一步是**必须**的：不嵌入就检索不到，面试只会出通用题，
    # "资料依据"整条链路落不了地。此前嵌入只存在于开发脚本里，
    # 上传接口从不触发它 —— 于是文档永远停在 chunked，
    # 是个静默的断点。
    #
    # 但嵌入**不能阻塞响应**：实测 800KB 文档同步嵌入要 30 秒以上，
    # 期间用户只能等（还要赌代理/网关的超时）。因此改为：
    # 解析与切块仍同步（它们决定文件是否有效，必须立刻告知用户），
    # 嵌入交给 worker，接口立即返回。
    #
    # 状态语义因此是"已切块·待索引"，这正是前端已有的
    # `chunked` 状态与"还有资料未索引"提示的用途 ——
    # 不需要新增状态概念。
    await enqueue_index_job(db, document.id)

    return document
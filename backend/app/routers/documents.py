from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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
from app.services.embedding_pipeline_service import (
    EmbeddingPipelineService,
)
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
)
async def embed_document_endpoint(
    project_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """为文档补齐向量索引（幂等，可重复调用）。

    存在的理由：上传时的嵌入是**非致命**的 ——
    Milvus 未就绪或嵌入服务抖动都不会让上传失败，
    但文档会停在 `chunked`。此时检索不到它，
    面试只会出通用题。这个端点让用户能重试，
    而不必删掉重传（重传会丢掉已有的 chunk）。
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

    embedded_count = await _embed_document_or_400(
        db=db,
        document=document,
    )

    if embedded_count == 0 and document.status != "embedded":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "没有待嵌入的文本块。若文档状态为 chunked，"
                "说明切块失败或内容为空。"
            ),
        )

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


async def _embed_document_or_400(db, document):
    """执行嵌入，失败时转成 4xx 并说明原因。

    与上传时的处理不同：这里是用户**主动重试**，
    因此失败必须显式报错 —— 静默失败会让用户以为已经索引好了。
    """

    embedding_pipeline = EmbeddingPipelineService(db)

    try:
        return await embedding_pipeline.embed_document(document.id)

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"嵌入失败：{exc}",
        ) from exc

    except Exception as exc:
        # Milvus 未就绪、嵌入服务不可用、网络问题等
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"嵌入失败（{type(exc).__name__}）：{exc}。"
                "请确认 Milvus 与嵌入服务可用后重试。"
            ),
        ) from exc


@router.post(
    "/{project_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
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

    # 9. 写入向量索引
    #
    # 这一步是**必须**的：不嵌入就检索不到，面试只会出通用题，
    # "资料依据"整条链路落不了地。
    # 此前嵌入只存在于开发脚本里，上传接口从不触发它 ——
    # 于是文档永远停在 chunked，是个静默的断点。
    #
    # 但嵌入**不能是致命的**：Milvus 未就绪或嵌入服务抖动时，
    # 让上传整体失败会丢掉用户刚传的文件与已完成的切块。
    # 因此失败时保留 chunked 状态并如实告知，
    # 用户可在资料页点"重试索引"（走 embed 端点）。
    embedded_count = 0
    embedding_error: str | None = None

    try:
        embedding_pipeline = EmbeddingPipelineService(db)
        embedded_count = await embedding_pipeline.embed_document(
            document.id
        )

        document.status = "embedded"
        await db.commit()
        await db.refresh(document)

    except Exception as exc:
        embedding_error = f"{type(exc).__name__}: {exc}"

    if embedding_error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"文档已解析并切块（{embedded_count} 段已嵌入），"
                f"但向量索引失败：{embedding_error}。"
                "文件已保留，可在资料页点「重试索引」。"
            ),
        )

    return document
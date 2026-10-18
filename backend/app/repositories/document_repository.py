from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document


class DocumentRepository:
    """文档数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(
        self,
        document_id: int,
    ) -> Document | None:
        """根据 ID 获取文档"""

        result = await self.session.execute(
            select(Document).where(
                Document.id == document_id
            )
        )

        return result.scalar_one_or_none()

    async def get_by_ids(
        self,
        document_ids: list[int],
    ) -> dict[int, Document]:
        """按 id 批量取文档，返回 {id: document}。

        用于"资料依据"要显示来源文件名：把按 chunk 收集到的
        document_id 一次性取回，避免逐条查询。
        """

        if not document_ids:
            return {}

        result = await self.session.execute(
            select(Document).where(Document.id.in_(document_ids))
        )

        return {
            document.id: document
            for document in result.scalars().all()
        }

    async def get_by_project(
        self,
        project_id: int,
    ) -> list[Document]:
        """获取项目下的所有文档"""

        result = await self.session.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.desc())
        )

        return list(result.scalars().all())

    async def get_indexed_document_types(
        self,
        project_id: int,
    ) -> list[str]:
        """返回项目下**有已索引资料**的文档类型（去重）。

        为什么只算 `status == 'embedded'`：那表示向量已写入 Milvus，
        检索才可能命中。只看"有文件"会把"上传了但索引失败"的类型
        也算进去 —— 于是面试计划里会安排一个**检索不到任何东西**的
        主题，出题退化成通用题，而用户看起来像"检索坏了"。

        空项目返回空列表，调用方据此走通用题的兜底计划。
        """

        result = await self.session.execute(
            select(Document.document_type)
            .where(
                Document.project_id == project_id,
                Document.status == "embedded",
            )
            .distinct()
        )

        return [row for row in result.scalars().all() if row]

    async def create(
        self,
        name: str,
        original_filename: str,
        file_type: str,
        document_type: str,
        file_path: str,
        project_id: int,
        content: str | None = None,
    ) -> Document:
        """创建文档"""

        document = Document(
            name=name,
            original_filename=original_filename,
            file_type=file_type,
            document_type=document_type,
            file_path=file_path,
            project_id=project_id,
            status="parsed" if content else "pending",
            content=content,
        )

        self.session.add(document)

        await self.session.commit()
        await self.session.refresh(document)

        return document

    async def delete(self, document: Document) -> None:
        """删除文档记录。

        对应的 chunk 由外键 `ON DELETE CASCADE` 一并删除；
        但 **Milvus 里的向量不会** —— 那需要调用方显式删除
        （见 routers/documents.py 的 delete_document）。
        漏删会留下孤儿向量，检索时占用 top-k 名额却取不到正文。
        """

        await self.session.delete(document)
        await self.session.commit()
import asyncio

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.document import Document


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document).where(Document.id == 6)
        )

        document = result.scalar_one_or_none()

        if document is None:
            print("Document 6 不存在")
            return

        print("Document:")
        print(f"ID: {document.id}")
        print(f"Name: {document.name}")
        print(f"Document Type: {document.document_type}")
        print(f"Project ID: {document.project_id}")


if __name__ == "__main__":
    asyncio.run(main())
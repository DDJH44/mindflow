import asyncio

from app.database.session import AsyncSessionLocal
from app.repositories.document_repository import DocumentRepository


async def main():
    async with AsyncSessionLocal() as db:
        repository = DocumentRepository(db)

        document = await repository.get_by_id(8)

        if document is None:
            print("Document 8 不存在")
            return

        print(
            {
                "id": document.id,
                "document_type": document.document_type,
                "status": document.status,
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
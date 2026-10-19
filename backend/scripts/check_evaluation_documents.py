import asyncio

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.document import Document


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document)
            .order_by(Document.id)
        )

        documents = result.scalars().all()

        print("\n========== Evaluation Documents ==========\n")

        if not documents:
            print("当前没有 Document")
            return

        for document in documents:
            print(
                f"id={document.id} | "
                f"project_id={document.project_id} | "
                f"type={document.document_type} | "
                f"name={document.name} | "
                f"status={document.status}"
            )


if __name__ == "__main__":
    asyncio.run(main())
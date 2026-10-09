from abc import ABC, abstractmethod


class VectorStore(ABC):
    """向量数据库抽象接口"""

    @abstractmethod
    async def create_collection(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def insert(
        self,
        ids: list[int],
        vectors: list[list[float]],
        metadata: list[dict],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        vector: list[float],
        limit: int = 5,
        filters: str | None = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, ids: list[int]) -> None:
        raise NotImplementedError
from pymilvus import DataType, MilvusClient

from app.services.vector_store import VectorStore


class MilvusVectorStore(VectorStore):
    """Milvus 向量数据库实现"""

    COLLECTION_NAME = "mindflow_chunks_v2"
    VECTOR_DIMENSION = 1024

    def __init__(
        self,
        uri: str = "http://localhost:19530",
        token: str = "root:Milvus",
    ):
        self.client = MilvusClient(
            uri=uri,
            token=token,
        )

    async def create_collection(self) -> None:
        if self.client.has_collection(
            collection_name=self.COLLECTION_NAME
        ):
            return

        schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
        )

        schema.add_field(
            field_name="id",
            datatype=DataType.INT64,
            is_primary=True,
        )

        schema.add_field(
            field_name="document_id",
            datatype=DataType.INT64,
        )

        schema.add_field(
            field_name="project_id",
            datatype=DataType.INT64,
        )

        schema.add_field(
            field_name="document_type",
            datatype=DataType.VARCHAR,
            max_length=20,
        )

        schema.add_field(
            field_name="vector",
            datatype=DataType.FLOAT_VECTOR,
            dim=self.VECTOR_DIMENSION,
        )

        index_params = self.client.prepare_index_params()

        index_params.add_index(
            field_name="vector",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )

        # 一致性级别必须是 **Strong**。
        #
        # 默认的 Bounded 不保证"插入后立刻可检索"，而本项目对这一点
        # 有硬依赖：`upload_document` 在插入后立刻把文档标成 `embedded`
        # 并返回，调用方（前端、`/start`）紧接着就可能检索。
        #
        # 实测（D53）：Bounded 下插入后立刻检索 **10 次命中 0 次**；
        # Strong 下 **10 次命中 10 次**。表现为"上传成功但面试只出
        # 通用题"，且**间歇性** —— 最难查的一类问题。
        self.client.create_collection(
            collection_name=self.COLLECTION_NAME,
            schema=schema,
            index_params=index_params,
            consistency_level="Strong",
        )

    async def insert(
        self,
        ids: list[int],
        vectors: list[list[float]],
        metadata: list[dict],
    ) -> None:
        if not ids:
            return

        if len(ids) != len(vectors):
            raise ValueError(
                "ids 数量与 vectors 数量不一致"
            )

        if len(ids) != len(metadata):
            raise ValueError(
                "ids 数量与 metadata 数量不一致"
            )

        data = []

        for chunk_id, vector, item_metadata in zip(
            ids,
            vectors,
            metadata,
        ):
            data.append(
                {
                    "id": chunk_id,
                    "document_id": item_metadata["document_id"],
                    "project_id": item_metadata["project_id"],
                    "document_type": item_metadata.get(
                        "document_type",
                        "other",
                    ),
                    "vector": vector,
                }
            )

        self.client.upsert(
            collection_name=self.COLLECTION_NAME,
            data=data,
        )

    async def search(
        self,
        vector: list[float],
        limit: int = 5,
        filters: str | None = None,
    ) -> list[dict]:

        search_result = self.client.search(
            collection_name=self.COLLECTION_NAME,
            data=[vector],
            limit=limit,
            filter=filters or "",
            output_fields=[
                "document_id",
                "project_id",
                "document_type",
            ],
            # 显式要求 Strong，而不是继承集合的设置。
            #
            # 两个理由：
            # 1. 已存在的集合是用 Bounded 建的（见 create_collection
            #    的说明），只改建集合的代码**修不好老库** ——
            #    而老库恰恰是问题正在发生的地方。
            # 2. 这里调用的语义就是"刚写完就要能读到"（面试出题依赖
            #    刚上传的资料），用强一致是**语义正确**，不是性能妥协。
            #
            # 代价是每次检索都要等数据可见，延迟略高。本项目单副本、
            # 集合规模小，这个代价可以接受；正确性不能妥协。
            consistency_level="Strong",
        )

        return search_result[0]

    async def delete(
        self,
        ids: list[int],
    ) -> None:
        if not ids:
            return

        self.client.delete(
            collection_name=self.COLLECTION_NAME,
            ids=ids,
        )
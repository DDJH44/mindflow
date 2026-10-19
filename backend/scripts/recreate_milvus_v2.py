from pymilvus import DataType, MilvusClient


COLLECTION_NAME = "mindflow_chunks_v2"


def main():
    client = MilvusClient(
        uri="http://localhost:19530",
        token="root:Milvus",
    )

    if client.has_collection(collection_name=COLLECTION_NAME):
        print(f"删除旧的 {COLLECTION_NAME}")
        client.drop_collection(collection_name=COLLECTION_NAME)

    print(f"创建新的 {COLLECTION_NAME}")

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
        dim=1024,
    )

    index_params = client.prepare_index_params()

    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )

    client.create_collection(
        collection_name=COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )

    print("V2 Collection 创建完成")
    print(
        client.describe_collection(
            collection_name=COLLECTION_NAME
        )
    )


if __name__ == "__main__":
    main()
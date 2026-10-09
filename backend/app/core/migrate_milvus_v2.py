from pymilvus import MilvusClient


OLD_COLLECTION = "mindflow_chunks"
NEW_COLLECTION = "mindflow_chunks_v2"


def main():
    client = MilvusClient(
        uri="http://localhost:19530",
        token="root:Milvus",
    )

    # 读取旧 Collection
    old_data = client.query(
        collection_name=OLD_COLLECTION,
        filter="",
        limit=1000,
        output_fields=[
            "id",
            "document_id",
            "project_id",
            "vector",
        ],
    )

    print(f"旧 Collection 数据量: {len(old_data)}")

    if not old_data:
        print("没有需要迁移的数据")
        return

    new_data = []

    for item in old_data:
        new_data.append(
            {
                "id": item["id"],
                "document_id": item["document_id"],
                "project_id": item["project_id"],
                "document_type": "other",
                "vector": item["vector"],
            }
        )

    print("准备迁移:")
    print(
        {
            "id": new_data[0]["id"],
            "document_id": new_data[0]["document_id"],
            "project_id": new_data[0]["project_id"],
            "document_type": new_data[0]["document_type"],
            "vector_dimension": len(new_data[0]["vector"]),
        }
    )

    # 插入
    result = client.insert(
        collection_name=NEW_COLLECTION,
        data=new_data,
    )

    print("Insert 结果:")
    print(result)

    # Flush，确保数据落盘
    client.flush(
        collection_name=NEW_COLLECTION,
    )

    print("Flush 完成")

    # 查询验证
    migrated = client.query(
        collection_name=NEW_COLLECTION,
        filter="",
        limit=1000,
        output_fields=[
            "id",
            "document_id",
            "project_id",
            "document_type",
        ],
    )

    print("V2 查询结果:")
    for item in migrated:
        print(item)

    # 统计
    stats = client.get_collection_stats(
        collection_name=NEW_COLLECTION,
    )

    print("V2 统计:")
    print(stats)


if __name__ == "__main__":
    main()
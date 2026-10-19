from pymilvus import MilvusClient


def main():
    client = MilvusClient(
        uri="http://localhost:19530",
        token="root:Milvus",
    )

    collection_name = "mindflow_chunks"

    if not client.has_collection(
        collection_name=collection_name
    ):
        print("Collection 不存在")
        return

    schema = client.describe_collection(
        collection_name=collection_name
    )

    print("Milvus Collection Schema:")
    print(schema)


if __name__ == "__main__":
    main()
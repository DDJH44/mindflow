import asyncio

from app.services.document_parser import DocumentParser


async def main():

    file_path = "你的 test.txt 实际路径"

    text = await DocumentParser.parse(
        file_path
    )

    print("========== 文件内容 ==========")
    print(text)
    print("========== 解析完成 ==========")


if __name__ == "__main__":
    asyncio.run(main())
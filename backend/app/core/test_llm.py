import asyncio

from app.services.openai_llm_service import OpenAILLMService


async def main():
    llm_service = OpenAILLMService()

    result = await llm_service.generate(
        prompt="请用一句话介绍 MindFlow AI 面试助手。",
        system_prompt="你是一个简洁、专业的 AI 助手。",
        temperature=0.3,
        max_tokens=500,
    )

    print("LLM 调用成功")
    print("模型返回：")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
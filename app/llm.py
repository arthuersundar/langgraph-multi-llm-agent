"""One switch for two LLM backends: managed (Bedrock) and self-hosted (vLLM)."""
import os
from dotenv import load_dotenv

load_dotenv()


def get_llm(provider: str | None = None):
    provider = provider or os.getenv("LLM_PROVIDER", "bedrock")

    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(
            model=os.getenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            temperature=0,
        )

    if provider == "vllm":
        # vLLM serves the OpenAI API, so the standard OpenAI client works against it.
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.getenv("VLLM_MODEL"),
            base_url=os.getenv("VLLM_BASE_URL"),
            api_key=os.getenv("VLLM_API_KEY", "not-needed"),
            temperature=0,
        )

    raise ValueError(f"Unknown provider '{provider}'. Use 'bedrock' or 'vllm'.")
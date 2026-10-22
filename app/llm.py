"""One switch for three LLM backends: managed (Bedrock), self-hosted (vLLM), or either one
through the team LLM gateway (API Gateway + Lambda)."""
import os
from dotenv import load_dotenv

load_dotenv()


def get_llm(provider: str | None = None, model: str | None = None):
    provider = provider or os.getenv("LLM_PROVIDER", "bedrock")

    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(
            model=model or os.getenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            temperature=0,
        )

    if provider == "vllm":
        # vLLM serves the OpenAI API, so the standard OpenAI client works against it.
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model or os.getenv("VLLM_MODEL"),
            base_url=os.getenv("VLLM_BASE_URL"),
            api_key=os.getenv("VLLM_API_KEY", "not-needed"),
            temperature=0,
        )

    if provider == "gateway":
        # The gateway is OpenAI-compatible too. The "model" field tells it which backend to
        # use ("bedrock" or "vllm"); the team API key goes in the x-api-key header.
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model or os.getenv("GATEWAY_BACKEND", "bedrock"),
            base_url=os.environ["GATEWAY_URL"],
            api_key="unused",  # the OpenAI client insists on one; the gateway ignores it
            default_headers={"x-api-key": os.environ["GATEWAY_API_KEY"]},
            temperature=0,
            max_retries=1,  # a 429 from a throttled backend should surface quickly
        )

    raise ValueError(f"Unknown provider '{provider}'. Use 'bedrock', 'vllm' or 'gateway'.")

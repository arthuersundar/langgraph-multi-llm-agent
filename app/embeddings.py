"""Embedding layer with one switch: local (Ollama nomic-embed-text) or managed (Bedrock Titan V2).

Each embedding model gets its own S3 Vectors index, because vectors from
different models are not comparable.
"""
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.getenv("EMBED_PROVIDER", "ollama")


def embedding_info() -> dict:
    """Name and vector size of the active embedding model."""
    if PROVIDER == "ollama":
        return {"name": "nomic", "dim": 768}
    if PROVIDER == "titan":
        return {"name": "titan-v2", "dim": int(os.getenv("TITAN_DIM", "1024"))}
    raise ValueError(f"Unknown EMBED_PROVIDER '{PROVIDER}'. Use 'ollama' or 'titan'.")


def embed(texts: list[str], kind: str = "document") -> list[list[float]]:
    """Embed a list of texts. kind is 'document' (ingestion) or 'query' (search)."""
    if PROVIDER == "ollama":
        # nomic-embed-text works best with these task prefixes.
        prefix = "search_document: " if kind == "document" else "search_query: "
        r = requests.post(
            f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/embed",
            json={
                "model": os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                "input": [prefix + t for t in texts],
            },
            timeout=300,
        )
        r.raise_for_status()
        return r.json()["embeddings"]

    if PROVIDER == "titan":
        import boto3

        rt = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        dim = embedding_info()["dim"]
        vectors = []
        for text in texts:
            resp = rt.invoke_model(
                modelId="amazon.titan-embed-text-v2:0",
                body=json.dumps({"inputText": text, "dimensions": dim, "normalize": True}),
            )
            vectors.append(json.loads(resp["body"].read())["embedding"])
        return vectors

    raise ValueError(f"Unknown EMBED_PROVIDER '{PROVIDER}'.")

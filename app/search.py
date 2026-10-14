"""Search the policy library in S3 Vectors. Used later as the agent's search_policies tool.

Usage:
    python app/search.py "what are the required tags?"
    python app/search.py "past cost incidents" --type postmortem
"""
import os
import sys

import boto3
from dotenv import load_dotenv

from embeddings import embed, embedding_info

load_dotenv()

BUCKET = os.environ["VECTOR_BUCKET"]
INDEX = f"policies-{embedding_info()['name']}"
_s3v = boto3.client("s3vectors", region_name=os.getenv("AWS_REGION", "us-east-1"))


def search_policies(question: str, top_k: int = 4, doc_type: str | None = None) -> list[dict]:
    """Return the top_k most relevant policy chunks, optionally filtered by doc_type
    (policy, runbook, guide, standard, directory, postmortem)."""
    query = {
        "vectorBucketName": BUCKET,
        "indexName": INDEX,
        "queryVector": {"float32": [float(x) for x in embed([question], kind="query")[0]]},
        "topK": top_k,
        "returnMetadata": True,
        "returnDistance": True,
    }
    if doc_type:
        query["filter"] = {"doc_type": {"$eq": doc_type}}

    results = []
    for v in _s3v.query_vectors(**query)["vectors"]:
        md = v.get("metadata", {})
        results.append({
            "title": md.get("title"),
            "section": md.get("section"),
            "source": md.get("source"),
            "doc_type": md.get("doc_type"),
            "distance": round(v.get("distance", 0.0), 3),
            "text": md.get("text", ""),
        })
    return results


if __name__ == "__main__":
    args = sys.argv[1:]
    doc_type = None
    if "--type" in args:
        i = args.index("--type")
        doc_type = args[i + 1]
        args = args[:i] + args[i + 2:]
    question = " ".join(args) or "What tags are required on AWS resources?"

    print(f"Q: {question}" + (f"  [type={doc_type}]" if doc_type else ""))
    for r in search_policies(question, doc_type=doc_type):
        print(f"\n[{r['distance']}] {r['title']} > {r['section']}  ({r['source']})")
        print("   " + r["text"][:250].replace("\n", " ") + "...")

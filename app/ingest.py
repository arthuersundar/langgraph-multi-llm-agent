"""Ingest the policy library: read docs -> chunk by section -> embed -> store in S3 Vectors.

Safe to re-run: vector keys are stable (file#chunk), so re-ingesting overwrites.

Usage:
    python app/ingest.py            # ingest into S3 Vectors
    python app/ingest.py --dry-run  # only show the chunks, no AWS or embedding calls
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
MAX_CHARS = 1500      # split long sections so each chunk stays focused
BATCH = 100           # vectors per put_vectors call


def parse(path: Path) -> tuple[dict, str]:
    """Split a markdown file into front-matter metadata and body."""
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta, body = {}, raw
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip('"')
        body = m.group(2)
    return meta, body


def chunk(body: str) -> list[tuple[str, str]]:
    """Split on '## ' headings; split oversized sections by paragraph. Returns (section, text)."""
    chunks = []
    for part in re.split(r"\n(?=## )", body.strip()):
        part = part.strip()
        # Skip pieces that are only the "# Title" line; the title is added to every chunk anyway.
        if not part or all(l.startswith("# ") or not l.strip() for l in part.splitlines()):
            continue
        if part.startswith("# "):
            part = "\n".join(part.splitlines()[1:]).strip()
        first = part.splitlines()[0]
        section = first[3:].strip() if first.startswith("## ") else "Overview"
        if len(part) <= MAX_CHARS:
            chunks.append((section, part))
            continue
        buf = ""
        for para in part.split("\n\n"):
            if buf and len(buf) + len(para) > MAX_CHARS:
                chunks.append((section, buf.strip()))
                buf = ""
            buf += para + "\n\n"
        if buf.strip():
            chunks.append((section, buf.strip()))
    return chunks


def build_records() -> list[dict]:
    records = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        meta, body = parse(path)
        title = meta.get("title", path.stem)
        for i, (section, text) in enumerate(chunk(body)):
            records.append({
                "key": f"{path.stem}#{i}",
                "text": f"Document: {title}\n\n{text}",
                "metadata": {
                    "source": path.name,
                    "title": title,
                    "section": section,
                    "doc_type": meta.get("doc_type", "unknown"),
                    "owner_team": meta.get("owner_team", "unknown"),
                },
            })
    return records


def ensure_bucket_and_index(s3v, bucket: str, index: str, dim: int) -> None:
    from botocore.exceptions import ClientError

    def create(label, fn, **kwargs):
        try:
            fn(**kwargs)
            print(f"created {label}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if "Conflict" in code or "AlreadyExists" in code:
                print(f"{label} already exists")
            else:
                raise

    create(f"vector bucket '{bucket}'", s3v.create_vector_bucket, vectorBucketName=bucket)
    create(
        f"index '{index}' ({dim} dims, cosine)",
        s3v.create_index,
        vectorBucketName=bucket,
        indexName=index,
        dataType="float32",
        dimension=dim,
        distanceMetric="cosine",
        # The chunk text is returned with results but never used for filtering.
        metadataConfiguration={"nonFilterableMetadataKeys": ["text"]},
    )


def main() -> None:
    records = build_records()
    print(f"{len(records)} chunks from {len({r['metadata']['source'] for r in records})} documents")

    if "--dry-run" in sys.argv:
        for r in records:
            print(f"  {r['key']:<45} [{r['metadata']['doc_type']}] {r['metadata']['section']} ({len(r['text'])} chars)")
        return

    import boto3
    from embeddings import embed, embedding_info

    info = embedding_info()
    bucket = os.environ["VECTOR_BUCKET"]
    index = f"policies-{info['name']}"
    s3v = boto3.client("s3vectors", region_name=os.getenv("AWS_REGION", "us-east-1"))

    ensure_bucket_and_index(s3v, bucket, index, info["dim"])

    for start in range(0, len(records), BATCH):
        batch = records[start:start + BATCH]
        vectors = embed([r["text"] for r in batch], kind="document")
        s3v.put_vectors(
            vectorBucketName=bucket,
            indexName=index,
            vectors=[
                {
                    "key": r["key"],
                    "data": {"float32": [float(x) for x in vec]},
                    "metadata": {**r["metadata"], "text": r["text"]},
                }
                for r, vec in zip(batch, vectors)
            ],
        )
        print(f"stored {start + len(batch)}/{len(records)}")

    print(f"done: bucket={bucket} index={index}")


if __name__ == "__main__":
    main()

"""The real policy chunks, produced by the same code the ingest step uses (app/ingest.py)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load_chunks() -> list[dict]:
    spec = importlib.util.spec_from_file_location("ingest_mod", ROOT / "app" / "ingest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ingest_mod"] = mod
    spec.loader.exec_module(mod)
    out = []
    for r in mod.build_records():
        md = r["metadata"]
        out.append({"doc": r["key"][:2], "title": md["title"], "section": md["section"],
                    "text": r["text"], "key": r["key"]})
    return out


def hit(c: dict) -> dict:
    """Shape the agent's search tool returns for one chunk."""
    return {"title": c["title"], "section": c["section"], "text": c["text"]}

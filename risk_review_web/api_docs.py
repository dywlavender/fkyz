from __future__ import annotations

import hashlib
import threading

from .paths import RAW_API_DIR, ROOT
from .utils import parse_frontmatter, read_text


API_DOC_CACHE = {"signature": None, "docs": []}
API_DOC_LOCK = threading.Lock()


def infer_api_category(rel_path: str) -> str:
    parts = str(rel_path or "").split("/")
    if len(parts) >= 3 and parts[0] == "raw" and parts[1] == "api":
        if parts[2] in {"本系统接口", "外部接口"}:
            return parts[2]
    return "其他"


def api_docs_signature():
    if not RAW_API_DIR.exists():
        return []
    return [
        (path.relative_to(ROOT).as_posix(), path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(RAW_API_DIR.glob("**/*.md"))
    ]


def load_api_docs_cached():
    signature = api_docs_signature()
    with API_DOC_LOCK:
        if API_DOC_CACHE.get("signature") == signature:
            return API_DOC_CACHE.get("docs", [])
        docs = []
        for rel, _, _ in signature:
            path = ROOT / rel
            text = read_text(path)
            meta = parse_frontmatter(text)
            category = infer_api_category(rel)
            docs.append({
                "name": meta.get("name", path.stem),
                "path": rel,
                "category": category,
                "source": meta.get("source", ""),
                "provider": meta.get("provider", ""),
                "version": meta.get("version", ""),
                "status": meta.get("status", ""),
                "method": meta.get("method", ""),
                "url": meta.get("url", ""),
                "scenes": meta.get("scenes", []),
                "keywords": meta.get("keywords", []),
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "content_size": len(text.encode("utf-8")),
                "content": text,
            })
        API_DOC_CACHE["signature"] = signature
        API_DOC_CACHE["docs"] = docs
        return docs


def list_api_docs(include_content: bool = False):
    docs = []
    for doc in load_api_docs_cached():
        item = dict(doc)
        if not include_content:
            item.pop("content", None)
        docs.append(item)
    return docs


def get_api_doc(identifier: str):
    identifier = (identifier or "").strip()
    for doc in load_api_docs_cached():
        if doc.get("name") == identifier or doc.get("path") == identifier:
            return dict(doc)
    return None

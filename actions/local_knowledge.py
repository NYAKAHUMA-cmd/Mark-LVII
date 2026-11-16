from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from core import outcome

BASE_DIR = Path(__file__).resolve().parent.parent
KB_PATH = BASE_DIR / "memory" / "local_knowledge.json"
KB_LOCK = Lock()

_SUPPORTED_SUFFIXES = {
    ".txt", ".md", ".rst", ".log", ".csv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".htm", ".pdf", ".docx",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".cs",
    ".go", ".rs", ".php", ".swift", ".kt", ".sh", ".bash", ".ps1",
}


def _load_store() -> dict[str, Any]:
    KB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KB_PATH.exists():
        return {"documents": []}
    try:
        data = json.loads(KB_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            docs = data.get("documents", [])
            return {"documents": docs if isinstance(docs, list) else []}
    except Exception:
        pass
    return {"documents": []}


def _save_store(store: dict[str, Any]) -> None:
    KB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with KB_LOCK:
        KB_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_rel_path(path: Path, root: Path | None = None) -> str:
    try:
        if root is not None:
            return str(path.resolve().relative_to(root.resolve()))
        return str(path.resolve())
    except Exception:
        return str(path.resolve())


def _stable_id(path: Path, kind: str) -> str:
    raw = f"{path.resolve()}::{kind}::{path.stat().st_mtime_ns}::{path.stat().st_size}"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)[:120]


def _chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    if not text or not text.strip():
        return []

    blocks = re.split(r"\n\s*\n+", text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current_len
        if not current:
            return
        blob = " ".join(part.strip() for part in current if part.strip())
        if blob:
            chunks.append(blob)
        current.clear()
        current_len = 0

    for block in blocks:
        clean = re.sub(r"\s+", " ", block).strip()
        if not clean:
            continue
        if current_len + len(clean) + 1 <= max_chars:
            current.append(clean)
            current_len += len(clean) + 1
        else:
            flush()
            if len(clean) <= max_chars:
                current = [clean]
                current_len = len(clean)
            else:
                for i in range(0, len(clean), max_chars):
                    piece = clean[i:i + max_chars].strip()
                    if piece:
                        chunks.append(piece)
    flush()

    if chunks:
        return chunks

    compact = re.sub(r"\s+", " ", text).strip()
    return [compact[i:i + max_chars].strip() for i in range(0, len(compact), max_chars) if compact[i:i + max_chars].strip()]


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".rst", ".log", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".html", ".htm"}:
            return path.read_text(encoding="utf-8", errors="ignore")

        if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".php", ".swift", ".kt", ".sh", ".bash", ".ps1"}:
            return path.read_text(encoding="utf-8", errors="ignore")

        if suffix == ".docx":
            try:
                from docx import Document
            except ImportError:
                return ""
            document = Document(path)
            return "\n".join(p.text for p in document.paragraphs if p.text.strip())

        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                try:
                    from PyPDF2 import PdfReader
                except ImportError:
                    return ""
            reader = PdfReader(str(path))
            pages: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text)
            return "\n\n".join(pages)

        if suffix in {".xls", ".xlsx"}:
            try:
                import pandas as pd
            except ImportError:
                return ""
            try:
                df = pd.read_excel(path)
                return df.to_string(index=False)
            except Exception:
                return ""

        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _is_supported_path(path: Path) -> bool:
    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    return suffix in _SUPPORTED_SUFFIXES


def _index_document(path: Path, root_dir: Path | None = None) -> dict[str, Any] | None:
    if not _is_supported_path(path):
        return None
    text = _extract_text(path)
    if not text or not text.strip():
        return None
    chunks = _chunk_text(text)
    if not chunks:
        return None

    root = root_dir if root_dir is not None else path.parent
    return {
        "id": _stable_id(path, "doc"),
        "name": path.name,
        "path": _safe_rel_path(path, root),
        "absolute_path": str(path.resolve()),
        "kind": path.suffix.lower().lstrip("."),
        "added_at": _now(),
        "updated_at": _now(),
        "chunks": [
            {
                "id": f"{_stable_id(path, 'chunk')}-{idx}",
                "text": chunk,
                "char_count": len(chunk),
            }
            for idx, chunk in enumerate(chunks)
        ],
    }


def _iter_supported_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if _is_supported_path(root) else []
    files: list[Path] = []
    for child in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if child.is_file() and _is_supported_path(child):
            files.append(child)
    return files


def _merge_documents(existing: list[dict[str, Any]], new_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path = {doc.get("absolute_path"): doc for doc in existing}
    for doc in new_docs:
        key = doc.get("absolute_path")
        if key in by_path:
            by_path[key] = doc
        else:
            by_path[key] = doc
    return list(by_path.values())


def _index_documents_from_paths(raw_paths: Any) -> str:
    if raw_paths is None:
        return "No files or folders were provided to index."

    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if not isinstance(raw_paths, (list, tuple)):
        return "The index input must be a file path, folder path, or list of them."

    store = _load_store()
    indexed: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in raw_paths:
        if not raw:
            continue
        target = Path(str(raw)).expanduser()
        if not target.exists():
            continue
        for file_path in _iter_supported_files(target):
            key = str(file_path.resolve())
            if key in seen:
                continue
            seen.add(key)
            doc = _index_document(file_path, root_dir=target if target.is_dir() else file_path.parent)
            if doc is not None:
                indexed.append(doc)

    if not indexed:
        return "No supported documents were found to index."

    merged = _merge_documents(store.get("documents", []), indexed)
    store["documents"] = merged
    _save_store(store)

    names = ", ".join(doc.get("name", "unknown") for doc in indexed[:5])
    extra = f" (+{len(indexed) - 5} more)" if len(indexed) > 5 else ""
    return f"Indexed {len(indexed)} document(s): {names}{extra}."


def _list_documents() -> str:
    store = _load_store()
    docs = store.get("documents", [])
    if not docs:
        return "The local knowledge base is empty. Index a file or folder first."
    lines = [f"Local knowledge base ({len(docs)} document(s)):"]
    for doc in docs[:20]:
        lines.append(f"- {doc.get('name', 'unknown')} ({doc.get('kind', 'unknown')}) :: {doc.get('path', doc.get('absolute_path', ''))}")
    if len(docs) > 20:
        lines.append(f"... and {len(docs) - 20} more")
    return "\n".join(lines)


def _stats() -> str:
    store = _load_store()
    docs = store.get("documents", [])
    total_chunks = sum(len(doc.get("chunks", [])) for doc in docs)
    total_chars = sum(sum(chunk.get("char_count", 0) for chunk in doc.get("chunks", [])) for doc in docs)
    return (
        f"Knowledge base stats: {len(docs)} document(s), {total_chunks} chunk(s), "
        f"{total_chars} characters indexed."
    )


def _search_documents(query: str, limit: int = 6) -> str:
    query = (query or "").strip()
    if not query:
        return "Please provide a search query for the local knowledge base."

    store = _load_store()
    docs = store.get("documents", [])
    if not docs:
        return "The local knowledge base is empty. Index files first."

    tokens = [token for token in re.findall(r"[a-zA-Z0-9_]+", query.lower()) if len(token) > 1]
    if not tokens:
        tokens = [query.lower()]

    scored: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for doc in docs:
        name = (doc.get("name") or "").lower()
        for chunk in doc.get("chunks", []):
            text = (chunk.get("text") or "").lower()
            if not text:
                continue
            score = 0
            for token in tokens:
                if token in text:
                    score += 6
                if token in name:
                    score += 2
                if token in (doc.get("path") or "").lower():
                    score += 2
            if score > 0:
                scored.append((score, doc, chunk))

    if not scored:
        return f"No local matches were found for: {query}"

    scored.sort(key=lambda item: (-item[0], item[1].get("name", "")))
    top = scored[: max(1, int(limit or 6))]

    lines = [f"Closest matches for: {query}"]
    for idx, (score, doc, chunk) in enumerate(top, 1):
        snippet = re.sub(r"\s+", " ", (chunk.get("text") or "")).strip()
        if len(snippet) > 260:
            snippet = snippet[:257].rstrip() + "..."
        lines.append(f"{idx}. {doc.get('name')} [{doc.get('path')}] (score={score})")
        lines.append(f"   {snippet}")
    return "\n".join(lines)


def local_knowledge(parameters: dict | None, response=None, player=None, session_memory=None, speak=None) -> str:
    params = parameters or {}
    mode = str(params.get("mode") or "search").strip().lower()

    if mode in {"index", "add", "ingest"}:
        raw = params.get("paths")
        if raw is None:
            raw = params.get("folder") or params.get("path") or params.get("file_path")
        if raw is None:
            return outcome.ask("local_knowledge", "Which file or folder should I index?")
        result = _index_documents_from_paths(raw)
        return outcome.done("local_knowledge", mode="index", result=result)

    if mode in {"list", "documents"}:
        return outcome.done("local_knowledge", mode="list", result=_list_documents())

    if mode in {"status", "stats"}:
        return outcome.done("local_knowledge", mode="status", result=_stats())

    query = str(params.get("query") or "").strip()
    if not query:
        return outcome.ask("local_knowledge", "What should I look up in the local knowledge base?")

    limit = int(params.get("limit") or 6)
    result = _search_documents(query, limit=limit)
    return outcome.untrusted("local_knowledge", result)


TOOL = {
    "name": "local_knowledge",
    "description": (
        "Searches a local document knowledge base stored on this machine. Use it to answer "
        "questions from documents that were indexed locally, without relying on web search or a "
        "remote service. Mode 'index' adds a file or folder to the corpus; 'search' looks up relevant "
        "passages; 'list' shows indexed files; 'stats' shows the store size."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {
                "type": "STRING",
                "description": "search | index | list | stats"
            },
            "query": {
                "type": "STRING",
                "description": "Search terms or a question to answer from the local knowledge base"
            },
            "paths": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Files or folders to index"
            },
            "path": {
                "type": "STRING",
                "description": "Single file or folder path to index"
            },
            "folder": {
                "type": "STRING",
                "description": "Folder to index recursively"
            },
            "file_path": {
                "type": "STRING",
                "description": "Single document path to index"
            },
            "limit": {
                "type": "INTEGER",
                "description": "Number of matching chunks to return in search mode"
            },
        },
        "required": [],
    },
    "handler": local_knowledge,
}

__all__ = ["local_knowledge", "TOOL"]

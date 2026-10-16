"""Commit-scoped code/documentation corpus using the existing Chroma and embeddings."""
import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import CHUNK_TOKENS, OVERLAP_TOKENS, AssistantError
from .documents import Chunk, Document, chunk_document, EXCLUDED
from .embeddings import Embeddings
from .index import build_index, Search, manifest
from .mcp_client import checked_call, pages

FORMAT = 1
MAX_CORPUS_BYTES = 20 * 1024 * 1024
LANGUAGES = {".py": "python", ".kt": "kotlin", ".kts": "kotlin", ".java": "java",
             ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
             ".go": "go", ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp",
             ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
             ".sql": "sql", ".sh": "shell", ".ps1": "powershell", ".xml": "xml",
             ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
             ".gradle": "groovy", ".proto": "protobuf", ".graphql": "graphql"}
DOC_EXTENSIONS = {".md", ".markdown", ".rst", ".txt", ".adoc"}
EXCLUDE_DIRS = EXCLUDED | {"generated", "artifacts", "coverage", "htmlcov", "site-packages", "Pods"}
SECRET_CONTENT = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b")


def exclusion(path):
    p = PurePosixPath(path)
    parts = p.parts
    if not parts or any(part.startswith(".") or part in EXCLUDE_DIRS for part in parts):
        return "excluded_directory_or_dotfile"
    name = p.name.lower()
    if (name in {"local.properties", "credentials", "credentials.json", "secrets.json", "secrets.yaml",
                 "secrets.yml", "id_rsa", "id_ed25519", "package-lock.json", "yarn.lock", "poetry.lock"}
            or p.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".lock"}
            or name.endswith((".min.js", ".min.css", ".map"))):
        return "secret_local_or_generated_file"
    if p.suffix.lower() not in DOC_EXTENSIONS | LANGUAGES.keys() and name not in {"readme", "contributing"}:
        return "unsupported_or_binary_format"
    return None


def content_exclusion(text):
    if SECRET_CONTENT.search(text):
        return "secret_pattern"
    if re.search(r"@generated|auto[- ]generated|do not edit", text[:1500], re.I):
        return "generated_content"
    return None


@dataclass(frozen=True)
class ReviewChunk(Chunk):
    kind: str
    language: str
    symbol: str
    commit: str

    @property
    def id(self):
        return hashlib.sha256((super().id + self.commit + str(FORMAT)).encode()).hexdigest()


def source_chunks(path, text, commit, tokenizer):
    suffix = PurePosixPath(path).suffix.lower()
    documentation = suffix in DOC_EXTENSIONS or PurePosixPath(path).name.lower() in {"readme", "contributing"}
    kind = "documentation" if documentation else "code"
    language = LANGUAGES.get(suffix, "")
    spans = []
    lines = text.splitlines(keepends=True)
    if language == "python":
        try:
            tree = ast.parse(text)
            cursor = 1
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
                    if cursor < start:
                        spans.append((cursor, start - 1, ""))
                    spans.append((start, node.end_lineno, node.name))
                    cursor = node.end_lineno + 1
            if cursor <= len(lines):
                spans.append((cursor, len(lines), ""))
        except (SyntaxError, ValueError, RecursionError):
            pass
    if not spans:
        spans = [(1, len(lines), "")]
    for start, end, symbol in spans:
        segment = "".join(lines[start - 1:end])
        if documentation:
            parts = chunk_document(Document(path, segment), tokenizer)
        else:
            parts = code_windows(path, segment, tokenizer)
        for part in parts:
            yield ReviewChunk(path, part.heading, part.start_line + start - 1,
                              part.end_line + start - 1, part.text, kind, language, symbol, commit)


def code_windows(path, text, tokenizer):
    """Token-bounded windows, aligned to lines where possible, with overlap."""
    cursor = 0
    budget = CHUNK_TOKENS - 16
    while cursor < len(text):
        offsets = tokenizer(text[cursor:], add_special_tokens=False, return_offsets_mapping=True,
                            verbose=False)["offset_mapping"]
        if not offsets:
            return
        end = len(text) if len(offsets) <= budget else cursor + offsets[budget][0]
        if end < len(text):
            newline = text.rfind("\n", cursor + (end - cursor) // 2, end)
            if newline >= 0:
                end = newline + 1
        fragment = text[cursor:end]
        if fragment.strip():
            yield Chunk(path, "", text.count("\n", 0, cursor) + 1,
                        text.count("\n", 0, end - int(fragment.endswith("\n"))) + 1, fragment)
        if end == len(text):
            return
        used = tokenizer(fragment, add_special_tokens=False, return_offsets_mapping=True,
                         verbose=False)["offset_mapping"]
        overlap = used[-OVERLAP_TOKENS][0] if len(used) > OVERLAP_TOKENS * 2 else len(fragment)
        cursor += max(1, overlap)


async def collect_corpus(session, commit, tokenizer):
    tree = await pages(session, "list_commit_files", {"commit": commit})
    chunks, skipped, size = [], [], 0
    for entry in tree["files"]:
        path = entry["path"]
        reason = exclusion(path)
        if entry["mode"] not in {"100644", "100755"}:
            reason = "symlink_or_submodule"
        if not reason:
            blob = await checked_call(session, "read_file_at_commit", {"commit": commit, "path": path})
            if blob.get("text") is None:
                reason = blob.get("reason", "binary_or_unreadable")
            elif size + blob["bytes"] > MAX_CORPUS_BYTES:
                reason = "corpus_size_limit"
            else:
                reason = content_exclusion(blob["text"])
                if not reason:
                    size += blob["bytes"]
                    chunks.extend(source_chunks(path, blob["text"], commit, tokenizer))
        if reason:
            skipped.append({"path": path, "reason": reason})
    return chunks, skipped


class ReviewSearch:
    def __init__(self, data, commit, chunks, embedder):
        snapshot = {"commit": commit, "format": FORMAT, "embedding": embedder.identity,
                    "chunking": [CHUNK_TOKENS, OVERLAP_TOKENS], "max_corpus_bytes": MAX_CORPUS_BYTES}
        build_index(data=data, embedder=embedder, corpus=chunks, snapshot=snapshot)
        if manifest(data).get("snapshot") != snapshot:
            raise AssistantError("Индекс ревью не соответствует head или настройкам")
        self.search = Search(data, embedder)
        self.records = [dict(c.metadata(), text=c.text, chunk_id=c.id) for c in chunks]

    def query(self, path, diff, limit=8):
        # Lexical candidates complement multilingual semantic retrieval for exact code identifiers.
        symbols = list(dict.fromkeys(re.findall(r"\b[A-Za-z_][A-Za-z_0-9]{3,}\b", diff)))[:60]
        query = path + " " + " ".join(symbols[:30])
        semantic = self.search.query(query, limit=limit)
        architecture = self.search.query("architecture project rules API contracts schemas " + path, limit=4)
        lexical = sorted(self.records, key=lambda c: (
            sum(1 for s in symbols if re.search(r"\b" + re.escape(s) + r"\b", c["text"])),
            c["path"] != path), reverse=True)
        candidates = architecture + lexical[:16] + semantic
        docs = [c for c in candidates if c["kind"] == "documentation"][:2]
        tests = [c for c in lexical if "test" in c["path"].lower()][:2]
        selected = {}
        for c in docs + tests + semantic[:2] + lexical + semantic:
            selected.setdefault(c["chunk_id"], c)
            if len(selected) >= limit:
                break
        return [dict(c, id="R" + c["chunk_id"][:16]) for c in selected.values()]

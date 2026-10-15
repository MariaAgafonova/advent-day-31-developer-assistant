import hashlib
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from .config import CHUNK_TOKENS, OVERLAP_TOKENS, AssistantError

EXTENSIONS = {".md", ".markdown", ".rst", ".txt"}
EXCLUDED = {"node_modules", "vendor", "venv", "build", "dist", "target", "site", "_build", "__pycache__"}
ROOT_NAMES = {"readme", "contributing", "contributibuting"}


@dataclass(frozen=True)
class Document:
    path: str
    text: str


@dataclass(frozen=True)
class Chunk:
    path: str
    heading: str
    start_line: int
    end_line: int
    text: str

    @property
    def id(self):
        value = f"{self.path}\0{self.heading}\0{self.start_line}\0{self.end_line}\0{self.text}"
        return hashlib.sha256(value.encode()).hexdigest()

    def metadata(self):
        return {k: v for k, v in asdict(self).items() if k != "text"}


def read_documents(repo: Path):
    if not repo.is_dir():
        raise AssistantError("Корневая папка проекта недоступна. Проверьте расположение приложения.")
    candidates = [p for p in repo.iterdir() if p.is_file() and p.stem.lower() in ROOT_NAMES and (p.suffix.lower() in EXTENSIONS or not p.suffix)]
    for directory, dirs, files in os.walk(repo / "docs", followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in EXCLUDED and not (Path(directory) / d).is_symlink())
        candidates.extend(Path(directory) / f for f in sorted(files))
    documents, skipped = [], []
    for path in sorted(set(candidates)):
        relative = path.relative_to(repo).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(repo.resolve()) or (path.suffix.lower() not in EXTENSIONS and path.stem.lower() not in ROOT_NAMES):
            skipped.append(relative)
            continue
        try:
            data = path.read_bytes()
            if b"\0" in data:
                raise UnicodeError("binary")
            text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError:
            skipped.append(relative)
            continue
        if text.strip():
            documents.append(Document(relative, text))
        else:
            skipped.append(relative)
    if not documents:
        raise AssistantError("Документация README/CONTRIBUTING/docs не найдена. Старый индекс сохранён.")
    return documents, skipped


def sections(text: str):
    lines = text.splitlines(keepends=True)
    first = 0
    title = ""
    if lines and lines[0].strip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if closing is not None:
            title = next((line.split(":", 1)[1].strip().strip("'\"").strip("`") for line in lines[1:closing] if line.startswith("title:")), "")
            first = closing + 1
    while first < len(lines):
        if not lines[first].strip():
            first += 1
        elif lines[first].strip().startswith("<!--"):
            end_comment = next((i for i in range(first, len(lines)) if "-->" in lines[i]), None)
            if end_comment is None:
                break
            first = end_comment + 1
        elif lines[first].strip() == "{{< details >}}":
            end_details = next((i for i in range(first, len(lines)) if lines[i].strip() == "{{< /details >}}"), None)
            if end_details is None:
                break
            first = end_details + 1
        else:
            break
    headings = {}
    fence = None
    for i, line in enumerate(lines):
        if i < first:
            continue
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            if fence is None:
                fence = marker.group()[0]
            elif marker.group()[0] == fence:
                fence = None
            continue
        if fence:
            continue
        atx = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", stripped)
        if atx:
            headings[i] = atx.group(1)
        elif i + 1 < len(lines) and stripped and len(stripped) < 240:
            underline = lines[i + 1].strip()
            if len(underline) >= 3 and len(set(underline)) == 1 and underline[0] in "=-~^\"+*#" and len(underline) >= len(stripped):
                headings[i] = stripped
    starts = sorted({first, *headings})
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        yield start + 1, headings.get(start, title), "".join(lines[start:end])


def passage(heading, text):
    return f"passage: {heading[:200]}\n{text}"


def chunk_document(document: Document, tokenizer):
    for first_line, heading, body in sections(document.text):
        prefix_tokens = len(tokenizer(passage(heading, ""), add_special_tokens=True)["input_ids"])
        budget = max(40, CHUNK_TOKENS - prefix_tokens - 4)
        cursor = 0
        while cursor < len(body):
            remaining = body[cursor:]
            offsets = tokenizer(remaining, add_special_tokens=False, return_offsets_mapping=True, verbose=False)["offset_mapping"]
            if not offsets:
                break
            end = len(body)
            if len(offsets) > budget:
                end = cursor + offsets[budget][0]
                paragraph = body.rfind("\n\n", cursor + (end - cursor) // 2, end)
                line = body.rfind("\n", cursor + (end - cursor) // 2, end)
                boundary = paragraph if paragraph >= 0 else line
                if boundary >= 0:
                    end = boundary + 1
            fragment = body[cursor:end]
            if fragment.strip():
                start_line = first_line + body.count("\n", 0, cursor)
                end_line = first_line + body.count("\n", 0, end - (1 if fragment.endswith("\n") else 0))
                yield Chunk(document.path, heading, start_line, end_line, fragment)
            if end == len(body):
                break
            taken = tokenizer(fragment, add_special_tokens=False, return_offsets_mapping=True, verbose=False)["offset_mapping"]
            overlap_at = taken[-OVERLAP_TOKENS][0] if len(taken) > OVERLAP_TOKENS * 2 else len(fragment)
            cursor = max(cursor + 1, cursor + overlap_at)

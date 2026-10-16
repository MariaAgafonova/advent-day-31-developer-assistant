import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path

from .config import DATA, PROJECT, CHUNK_TOKENS, OVERLAP_TOKENS, AssistantError
from .documents import read_documents, chunk_document
from .embeddings import Embeddings

log = logging.getLogger(__name__)


def client(data: Path):
    import chromadb
    from chromadb.config import Settings
    # The Rust backend crashes in add() on this Windows host (upstream #5937).
    return chromadb.PersistentClient(path=str(data / "chroma"), settings=Settings(
        chroma_api_impl="chromadb.api.segment.SegmentAPI", anonymized_telemetry=False,
    ))


def manifest(data=DATA):
    try:
        result = json.loads((data / "active.json").read_text(encoding="utf-8"))
        if result["embedding"] != Embeddings.identity or result["chunking"] != [CHUNK_TOKENS, OVERLAP_TOKENS]:
            raise AssistantError("Модель или формат индекса изменились. Запустите index заново.")
        return result
    except FileNotFoundError as exc:
        raise AssistantError("Индекс отсутствует. Выполните python -m developer_assistant index.") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise AssistantError("Метаданные индекса повреждены. Выполните index заново.") from exc


def build_index(repo=PROJECT, data=DATA, embedder=None, *, corpus=None, snapshot=None):
    data.mkdir(parents=True, exist_ok=True)
    lock = data / "index.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AssistantError("Индексация уже выполняется (index.lock). После аварийного завершения удалите lock, убедившись, что другого процесса index нет.") from exc
    os.close(fd)
    db = None
    collection = None
    activated = False
    try:
        docs, skipped = read_documents(repo) if corpus is None else ([], [])
        embedder = embedder or Embeddings()
        chunks = ([chunk for doc in docs for chunk in chunk_document(doc, embedder.tokenizer)]
                  if corpus is None else list(corpus))
        if not chunks:
            raise AssistantError("Нет фрагментов для индексации. Предыдущий индекс сохранён.")
        db = client(data)
        cached_vectors = {}
        try:
            previous = manifest(data)
            old_records = db.get_collection(previous["collection"], embedding_function=None).get(include=["embeddings"])
            cached_vectors = dict(zip(old_records["ids"], old_records["embeddings"].tolist()))
        except Exception:
            log.info("Нет совместимого кеша embeddings; фрагменты будут вычислены заново.")
        reused = sum(chunk.id in cached_vectors for chunk in chunks)
        # Keep this immutable snapshot in Chroma's exact-search buffer. Its vectors
        # persist in SQLite and replay on reopen; native HNSW add crashes on this host.
        buffer_size = max(10000, len(chunks) + 1)
        collection = db.create_collection("project_" + uuid.uuid4().hex, embedding_function=None,
            metadata={"hnsw:space": "cosine", "hnsw:batch_size": buffer_size,
                      "hnsw:sync_threshold": buffer_size * 2},
            configuration={"hnsw": {"space": "cosine"}})
        for offset in range(0, len(chunks), 32):
            batch = chunks[offset:offset + 32]
            missing = [c for c in batch if c.id not in cached_vectors]
            if missing:
                cached_vectors.update(zip([c.id for c in missing], embedder.documents(missing)))
            collection.add(ids=[c.id for c in batch], embeddings=[cached_vectors[c.id] for c in batch], documents=[c.text for c in batch], metadatas=[c.metadata() for c in batch])
            if offset % 320 == 0:
                log.info("Индексация: %s / %s фрагментов", min(offset + 32, len(chunks)), len(chunks))
        if collection.count() != len(chunks):
            raise AssistantError("Количество записанных фрагментов не совпало; индекс не переключён.")
        result = {
            "collection": collection.name, "embedding": embedder.identity,
            "search_mode": "chroma_exact_buffer",
            "chunking": [CHUNK_TOKENS, OVERLAP_TOKENS],
            "file_count": len({c.path for c in chunks}), "chunk_count": len(chunks),
            "reused_embeddings": reused,
            "files": ({d.path: hashlib.sha256(d.text.encode()).hexdigest() for d in docs}
                      if corpus is None else {c.path: "commit:" + snapshot["commit"] for c in chunks}),
            "skipped": skipped,
        }
        if snapshot is not None:
            result["snapshot"] = snapshot
        pending = data / "active.json.tmp"
        pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(pending, data / "active.json")
        activated = True
        for old in db.list_collections():
            if old.name.startswith(("project_", "glab_")) and old.name != collection.name:
                try:
                    db.delete_collection(old.name)
                except Exception:
                    log.warning("Не удалось удалить неактивную коллекцию %s", old.name)
        return result
    finally:
        if collection is not None and not activated:
            try:
                db.delete_collection(collection.name)
            except Exception:
                log.warning("Не удалось удалить незавершённую коллекцию")
        lock.unlink(missing_ok=True)


class Search:
    def __init__(self, data=DATA, embedder=None):
        self.data = data
        manifest(data)
        self.embedder = embedder or Embeddings()

    def query(self, question, limit=6):
        if not question.strip():
            raise AssistantError("Введите непустой вопрос.")
        info = manifest(self.data)
        try:
            collection = client(self.data).get_collection(info["collection"], embedding_function=None)
            if collection.count() == 0:
                raise AssistantError("Индекс пуст. Выполните index.")
            vector = self.embedder.query(question)
            options = {"n_results": min(limit * 4, collection.count()), "include": ["documents", "metadatas", "distances"]}
            result = collection.query(query_embeddings=vector, **options)
            overview_paths = [path for path in info["files"] if "/" not in path or path.startswith("docs/") and path.count("/") == 1]
            structure = bool(re.search(r"устроен|структур|архитектур|организован|structure|layout|architecture|entry point", question, re.IGNORECASE))
            quota = min(3 if structure else 2, limit // 2)
            overview = None
            source_outline = None
            if overview_paths and quota:
                overview_vector = self.embedder.query("Project architecture source code directory structure application entry point data storage user interface") if structure else vector
                overview = collection.query(query_embeddings=overview_vector, where={"path": {"$in": overview_paths}}, **options)
                if structure:
                    source_outline = collection.query(
                        query_embeddings=self.embedder.query("How is the Android application organized? Where are the activity, screens, ViewModels, database and build files?"),
                        where={"path": {"$in": overview_paths}}, **options)
        except AssistantError:
            raise
        except Exception as exc:
            raise AssistantError("Не удалось прочитать индекс Chroma. Выполните index заново.") from exc
        def hits(response):
            return [dict(meta, text=text, similarity=round(1 - distance, 5), chunk_id=identifier)
                    for identifier, meta, text, distance in zip(response["ids"][0], response["metadatas"][0], response["documents"][0], response["distances"][0])]
        overview_hits = hits(overview) if overview else []
        if source_outline:
            overview_hits = overview_hits[:1] + hits(source_outline)
        candidates = overview_hits[:quota] + hits(result)
        selected = {}
        paths = set()
        # A long overview must not crowd all detailed documents out of the context.
        for hit in candidates:
            if hit["path"] not in paths:
                selected.setdefault(hit["chunk_id"], hit)
                paths.add(hit["path"])
            if len(selected) == limit:
                return list(selected.values())
        for hit in candidates:
            selected.setdefault(hit["chunk_id"], hit)
        return list(selected.values())[:limit]

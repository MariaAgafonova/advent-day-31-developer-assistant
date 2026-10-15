import json
import subprocess
import sys

import pytest

from developer_assistant.config import AssistantError, CHUNK_TOKENS
from developer_assistant.documents import Document, chunk_document, passage, read_documents
from developer_assistant.index import build_index, client, manifest, Search


def test_document_selection_and_binary_exclusion(tmp_path):
    (tmp_path / "README.md").write_text("# Root\nHello", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.rst").write_text("Contribute\n==========\nHow", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.rst").write_text(".. raw:: html\n\n    <script>data only</script>", encoding="utf-8")
    (docs / "binary.md").write_bytes(b"\0broken")
    (docs / "image.png").write_bytes(b"image")
    for excluded in ["vendor", ".git", "build"]:
        (docs / excluded).mkdir()
        (docs / excluded / "README.md").write_text("excluded")
    found, skipped = read_documents(tmp_path)
    assert {d.path for d in found} == {"README.md", "CONTRIBUTING.rst", "docs/index.rst"}
    assert set(skipped) == {"docs/binary.md", "docs/image.png"}


def test_headings_ranges_and_long_line(fake_embeddings):
    text = "# Intro\nHi\n\n```python\n# not a heading\n```\n\nInstall\n=======\n" + "word " * 1200
    chunks = list(chunk_document(Document("doc.rst", text), fake_embeddings.tokenizer))
    assert {c.heading for c in chunks} == {"Intro", "Install"}
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == 10
    assert len(chunks) > 3
    for chunk in chunks:
        assert len(fake_embeddings.tokenizer(passage(chunk.heading, chunk.text), add_special_tokens=True)["input_ids"]) <= CHUNK_TOKENS
        assert chunk.text in "\n".join(text.splitlines()[chunk.start_line - 1:chunk.end_line]) + "\n"


def test_frontmatter_and_generated_comments_do_not_dominate_embeddings(fake_embeddings):
    text = "---\ntitle: '`glab mr create`'\ninfo: Technical writer\n---\n\n<!--\nRun make gen-docs.\n-->\n\nCreate a merge request.\n\n## Synopsis\nUse glab mr create.\n"
    chunks = list(chunk_document(Document("docs/create.md", text), fake_embeddings.tokenizer))
    assert chunks[0].heading == "glab mr create"
    assert chunks[0].start_line == 10
    assert "Technical writer" not in "".join(c.text for c in chunks)
    assert "make gen-docs" not in "".join(c.text for c in chunks)
    assert chunks[1].start_line == 12


def test_reindex_no_duplicates_deletion_and_failure(tmp_path, fake_embeddings):
    repo, data = tmp_path / "project", tmp_path / "data"
    repo.mkdir()
    (repo / "README.md").write_text("# Project\nRead me")
    (repo / "docs").mkdir()
    page = repo / "docs" / "guide.rst"
    page.write_text("Guide\n=====\nDetails")
    first = build_index(repo, data, fake_embeddings)
    first_ids = set(client(data).get_collection(first["collection"]).get()["ids"])
    second = build_index(repo, data, fake_embeddings)
    assert second["chunk_count"] == first["chunk_count"]
    assert second["reused_embeddings"] == first["chunk_count"]
    assert set(client(data).get_collection(second["collection"]).get()["ids"]) == first_ids
    assert len(client(data).list_collections()) == 1
    page.unlink()
    third = build_index(repo, data, fake_embeddings)
    assert third["file_count"] == 1
    assert all(m["path"] == "README.md" for m in client(data).get_collection(third["collection"]).get()["metadatas"])
    original = fake_embeddings.documents
    (repo / "README.md").write_text("# Project\nChanged text requiring a new embedding")
    def fail(chunks):
        raise RuntimeError("embedding failure")
    fake_embeddings.documents = fail
    with pytest.raises(RuntimeError):
        build_index(repo, data, fake_embeddings)
    assert manifest(data)["collection"] == third["collection"]
    assert not (data / "index.lock").exists()
    fake_embeddings.documents = original
    assert Search(data, fake_embeddings).query("read")[0]["path"] == "README.md"


def test_missing_corrupt_and_wrong_model_index(tmp_path):
    with pytest.raises(AssistantError, match="отсутствует"):
        Search(tmp_path)
    (tmp_path / "active.json").write_text("invalid")
    with pytest.raises(AssistantError, match="повреждены"):
        manifest(tmp_path)
    (tmp_path / "active.json").write_text(json.dumps({"embedding": {"model": "wrong"}}))
    with pytest.raises(AssistantError, match="Модель"):
        manifest(tmp_path)


def test_exact_search_over_native_batch_boundary_and_process_restart(tmp_path, fake_embeddings):
    repo, data = tmp_path / "project", tmp_path / "data"
    repo.mkdir()
    (repo / "README.md").write_text("\n".join(f"# Section {i}\nDocument {i}" for i in range(320)))
    result = build_index(repo, data, fake_embeddings)
    assert result["chunk_count"] == 320
    code = "from pathlib import Path; import sys; from developer_assistant.index import client,manifest; d=Path(sys.argv[1]); c=client(d).get_collection(manifest(d)['collection'],embedding_function=None); assert c.count()==320; assert c.query(query_embeddings=[[1.]+[0.]*383],n_results=1)['distances'][0][0] < 0.00001; print('reopen OK')"
    process = subprocess.run([sys.executable, "-c", code, str(data)], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert "reopen OK" in process.stdout

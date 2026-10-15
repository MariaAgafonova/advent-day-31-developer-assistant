import asyncio
import os
import subprocess

import pytest

from developer_assistant.config import PROJECT, DATA, CHUNK_TOKENS, api_settings, load_environment
from developer_assistant.documents import passage
from developer_assistant.index import Search, build_index, client, manifest
from developer_assistant.mcp_client import checkout_context
from developer_assistant.answers import make_context, generate


@pytest.fixture(scope="module")
def search():
    return Search()


@pytest.mark.integration
def test_real_corpus_reindex_and_lines(search):
    before = manifest()
    records = client(DATA).get_collection(before["collection"]).get()
    after = build_index(embedder=search.embedder)
    assert before["file_count"] == after["file_count"]
    assert before["chunk_count"] == after["chunk_count"]
    assert after["reused_embeddings"] == before["chunk_count"]
    new = client(DATA).get_collection(after["collection"]).get()
    assert set(records["ids"]) == set(new["ids"])
    paths = {m["path"] for m in new["metadatas"]}
    assert {"README.md", "docs/architecture.md", "docs/voice.md", "docs/implementation-plan.md"} <= paths
    assert not any(path.startswith("docs/source/") for path in paths)
    for text, meta in zip(new["documents"], new["metadatas"]):
        lines = (PROJECT / meta["path"]).read_text(encoding="utf-8-sig").splitlines()
        assert 1 <= meta["start_line"] <= meta["end_line"] <= len(lines)
        assert text in "\n".join(lines[meta["start_line"] - 1:meta["end_line"]]) + "\n"
        assert len(search.embedder.tokenizer(passage(meta["heading"], text))["input_ids"]) <= CHUNK_TOKENS


@pytest.mark.integration
def test_russian_retrieval(search):
    build = search.query("Как собрать Android-приложение из исходников?")
    assert any("assembleDebug" in h["text"] for h in build)
    voice = search.query("Как разделяются задачи при голосовом вводе?")
    assert any(h["path"] == "docs/voice.md" and "separator" in h["text"] for h in voice)


@pytest.mark.integration
def test_real_mcp_and_structure_context(search):
    checkout = asyncio.run(checkout_context())
    actual = subprocess.run(["git", "branch", "--show-current"], cwd=PROJECT, capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    assert checkout["branch"]["branch"] == actual
    files = checkout["files"]["files"]
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=PROJECT, capture_output=True, text=True, check=True, timeout=10).stdout.split("\0")
    assert set(files) <= set(tracked)
    assert {"README.md", "app/src/main/java/ru/natoday/app/MainActivity.kt", "docs/architecture.md"} <= set(files)
    context = make_context("Как устроен проект?", search.query("Как устроен проект?"), checkout)
    assert context["documentation"] and {f["id"] for f in context["checkout_facts"]} == {"MCP_BRANCH", "MCP_FILES"}
    assert len(context["documentation"]) <= 6
    assert any(d["path"] == "docs/architecture.md" for d in context["documentation"])
    assert any("MainActivity" in d["text"] for d in context["documentation"])


@pytest.mark.live_api
@pytest.mark.parametrize("question", ["Как устроен проект?", "Какая сейчас git-ветка?", "Как включить телепортацию физических предметов в приложении «На сегодня»?"])
def test_live_generation(question):
    load_environment()
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("OPENAI_MODEL"):
        pytest.skip("OPENAI_API_KEY/OPENAI_MODEL не заданы: реальная генерация не проверена")
    api_settings()
    context = make_context(question, Search().query(question), asyncio.run(checkout_context()))
    answer = generate(context)
    if "телепортацию" in question:
        assert "ответа не найдено" in answer
    elif "ветка" in question:
        assert context["checkout_facts"][0]["data"]["branch"] in answer
        assert "MCP:get_current_branch" in answer
    else:
        assert "MCP:list_project_files" in answer
        assert any(d["path"] + ":" in answer for d in context["documentation"])

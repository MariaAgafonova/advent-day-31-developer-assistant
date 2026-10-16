import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from developer_assistant.config import AssistantError
from developer_assistant.demo import create_demo
from developer_assistant.git_review import CommitGit, GitReviewError
from developer_assistant.llm import InvalidModelResponse
from developer_assistant.mcp_client import connect, checked_call, pages
from developer_assistant.review import run_review, ReviewLimits, generate_review, diff_parts
from developer_assistant.review_models import BatchReview, validate_findings


def git(repo, *args):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", *args],
        cwd=repo, capture_output=True, text=True, check=True, timeout=15).stdout.strip()


@pytest.fixture
def demo(tmp_path):
    return create_demo(tmp_path / "repo")


def issue(context):
    diff = next(s for s in context["sources"] if s["kind"] == "diff")
    refs = [s["id"] for s in context["sources"][:3]]
    return {"findings": [{"category": "bug", "severity": "high", "path": diff["path"],
        "side": "head", "line_start": 2, "line_end": 2, "title": "Division by zero",
        "explanation": "Guard was removed", "scenario": "quantity=0 raises ZeroDivisionError",
        "suggestion": "Restore the nonpositive quantity guard", "confidence": 0.99,
        "evidence_level": "confirmed", "references": refs}]}


def test_real_mcp_commit_changes_and_merge_base(demo):
    repo = Path(demo["repo"])
    git(repo, "checkout", demo["base"])
    (repo / "deleted.txt").write_text("old\n", encoding="utf-8")
    (repo / "old name.py").write_text("def old():\n    return 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base files")
    ancestor = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "feature")
    git(repo, "mv", "old name.py", "new name.py")
    git(repo, "rm", "deleted.txt")
    (repo / "added.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "data.bin").write_bytes(b"\0\x01\x02")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "changes")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "target", ancestor)
    (repo / "target_only.py").write_text("value = 3\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "unrelated target change")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "feature", "--no-edit")  # checkout is a synthetic merge, not head

    async def probe():
        async with connect(repo) as session:
            changed = await pages(session, "get_changed_files", {"base": base, "head": head})
            assert changed["merge_base"] == ancestor
            entries = {f["path"]: f for f in changed["files"]}
            assert set(entries) == {"new name.py", "deleted.txt", "added.py", "data.bin"}
            assert entries["new name.py"]["old_path"] == "old name.py"
            assert entries["new name.py"]["change_type"] == "renamed"
            assert entries["deleted.txt"]["change_type"] == "deleted"
            assert entries["added.py"]["change_type"] == "added"
            d = await checked_call(session, "get_diff", {"base": base, "head": head, "path": "added.py"})
            assert "@@ -0,0 +1 @@" in d["diff"] and "+value = 2" in d["diff"]
            deleted = await checked_call(session, "read_file_at_commit", {"commit": ancestor, "path": "deleted.txt"})
            assert deleted["text"] == "old\n"
            assert (await checked_call(session, "read_file_at_commit", {"commit": head, "path": "deleted.txt"}))["status"] == "missing"
            binary = await checked_call(session, "get_diff", {"base": base, "head": head, "path": "data.bin"})
            assert binary["binary"]
            page = await checked_call(session, "get_changed_files", {"base": base, "head": head, "limit": 1})
            assert page["next_offset"] == 1 and page["truncated"]
    asyncio.run(probe())


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:/secret", "a/../../secret", "a\\b", "./pricing.py"])
def test_reject_paths(demo, path):
    with pytest.raises(GitReviewError, match="invalid_path"):
        CommitGit(demo["repo"]).read_file(demo["head"], path)


def test_reject_sha_shallow_symlink_and_size(demo, tmp_path):
    reader = CommitGit(demo["repo"])
    with pytest.raises(GitReviewError, match="invalid_sha"):
        reader.commit("HEAD")
    with pytest.raises(GitReviewError, match="missing_commit"):
        reader.commit("f" * 40)
    assert reader.read_file(demo["head"], "pricing.py", max_bytes=5)["truncated"]
    clone = tmp_path / "shallow"
    git(tmp_path, "clone", "--depth", "1", Path(demo["repo"]).as_uri(), str(clone))
    with pytest.raises(GitReviewError, match="shallow_history"):
        CommitGit(clone).comparison(demo["head"], demo["head"])
    # Insert a symlink object without requiring Windows symlink privileges.
    repo = Path(demo["repo"])
    oid = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="../outside",
                         text=True, capture_output=True, check=True).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{oid},link.py")
    git(repo, "commit", "-m", "symlink")
    assert reader.read_file(git(repo, "rev-parse", "HEAD"), "link.py")["status"] == "unsupported"


def test_end_to_end_rag_diff_json_report(demo, tmp_path, fake_embeddings):
    seen = []
    def model(context):
        seen.append(context)
        return issue(context)
    output = tmp_path / "artifacts" / "review.md"
    code, report = asyncio.run(run_review(**demo, output=output, data=tmp_path / "index",
        embedder=fake_embeddings, model_call=model))
    assert code == 0, report
    assert report["status"] == "completed" and report["scope"]["reviewed_files"] == 1
    assert report["scope"]["mcp"] and report["scope"]["rag"] and report["scope"]["llm"]
    kinds = {s["kind"] for s in seen[0]["sources"]}
    assert {"documentation", "code", "diff"} <= kinds
    assert any("-    if quantity <= 0:" in s["text"] for s in seen[0]["sources"])
    assert all(s["commit"] == demo["head"] for s in seen[0]["sources"] if s["kind"] != "diff")
    text = output.read_text(encoding="utf-8")
    assert all(title in text for title in ["Потенциальные баги", "Архитектурные проблемы", "Рекомендации"])
    assert json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))["findings"][0]["reference_ids"]


def test_api_failure_and_invalid_json_are_errors(demo, tmp_path, fake_embeddings):
    def fail(context):
        raise AssistantError("API unavailable")
    for model in [fail, lambda context: {"findings": [{"category": "made_up"}]}]:
        code, report = asyncio.run(run_review(**demo, output=tmp_path / "review.md", data=tmp_path / "idx",
            embedder=fake_embeddings, model_call=model))
        assert code == 1 and report["status"] == "error" and report.get("error")
        assert "Обоснованных замечаний" not in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_missing_key_is_explicit_skip(demo, tmp_path, monkeypatch):
    for name in ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    code, report = asyncio.run(run_review(**demo, output=tmp_path / "review.md"))
    assert code == 3 and report["status"] == "skipped" and not report["scope"]["llm"]


def test_schema_paths_lines_and_reference_validation():
    context = {"files": [{"head_path": "pricing.py", "base_path": "pricing.py", "head_lines": 2}],
               "sources": [{"kind": "diff", "id": "D1", "path": "pricing.py", "head_path": "pricing.py", "head_ranges": [[1, 2]]}]}
    good = issue(context)
    assert validate_findings(good, context).findings
    for field, value in [("path", "missing.py"), ("line_end", 99), ("references", ["D999"]),
                         ("confidence", 2.0), ("side", "invented"), ("severity", "critical")]:
        payload = issue(context)
        payload["findings"][0][field] = value
        with pytest.raises(InvalidModelResponse):
            validate_findings(payload, context)
    calls = []
    def invalid(_):
        calls.append(1)
        return {}
    with pytest.raises(InvalidModelResponse):
        generate_review(context, None, ReviewLimits(), invalid)
    assert len(calls) == 2


def test_truncation_batch_limits_and_exclusions(demo, tmp_path, fake_embeddings):
    repo = Path(demo["repo"])
    (repo / "big.py").write_text("".join(f"value_{i} = {i}\n" for i in range(250)), encoding="utf-8")
    (repo / ".env").write_text("SECRET=do-not-send", encoding="utf-8")
    (repo / "secrets.json").write_text('{"token":"private"}', encoding="utf-8")
    git(repo, "add", "-f", ".env", "big.py", "secrets.json")
    git(repo, "commit", "-m", "large change and secrets")
    demo["head"] = git(repo, "rev-parse", "HEAD")
    calls = []
    def model(context):
        calls.append(context)
        assert "do-not-send" not in json.dumps(context)
        return {"findings": []}
    code, report = asyncio.run(run_review(**demo, output=tmp_path / "review.md", data=tmp_path / "index",
        embedder=fake_embeddings, model_call=model,
        limits=ReviewLimits(diff_bytes=256, files_per_request=1, requests=1)))
    assert code == 0 and report["status"] == "partial"
    assert any(s["reason"] == "diff_truncated" for s in report["skipped"])
    assert len(calls) == 1 and report["scope"]["partial_files"] == 1
    assert {s["path"] for s in report["index_skipped"]} >= {".env", "secrets.json"}


def test_hunk_coordinates():
    diff = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -10,2 +10,2 @@\n keep\n-old\n+new\n"
    parts = list(diff_parts(diff, max_bytes=10))
    assert parts[0]["base_ranges"] == [[10, 10]]
    assert parts[1]["base_ranges"] == [[11, 11]]
    assert parts[1]["head_ranges"] == [[11, 11]]


@pytest.mark.live_api
def test_live_review(demo, tmp_path):
    from developer_assistant.config import load_environment
    load_environment()
    if not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("No API key; real LLM was not checked")
    code, report = asyncio.run(run_review(**demo, output=tmp_path / "review.md", data=tmp_path / "index"))
    assert code == 0, report
    assert report["scope"]["llm"] and report["scope"]["rag"]
    assert any(f["category"] == "bug" and "pricing.py" == f["path"] for f in report["findings"])

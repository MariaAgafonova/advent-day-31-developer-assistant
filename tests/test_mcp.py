import asyncio
import json
import shutil
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from developer_assistant.config import ROOT, AssistantError
from developer_assistant.mcp_client import call
from developer_assistant.mcp_server import GitContext


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=10).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-b", "study")
    (repo / "README.md").write_text("Test")
    (repo / "space name.txt").write_text("Test")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    return repo


def test_git_branch_detached_missing_and_limits(repository, tmp_path):
    context = GitContext(repository)
    assert context.branch() == {"status": "ok", "branch": "study"}
    files = context.files(limit=1)
    assert files["returned"] == 1 and files["truncated"]
    assert context.files(prefix="space")["files"] == ["space name.txt"]
    assert context.files(limit=501)["status"] == "error"
    assert context.files(prefix="../")["status"] == "error"
    git(repository, "checkout", "--detach")
    assert context.branch() == {"status": "detached", "branch": None}
    assert GitContext(tmp_path).branch()["error"] == "not_repository"
    assert GitContext(repository / "child").branch()["error"] == "not_repository"


def test_git_timeout_and_fixed_arguments(repository, monkeypatch):
    captured = []
    def timeout(args, **kwargs):
        captured.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, 10)
    monkeypatch.setattr(subprocess, "run", timeout)
    assert GitContext(repository).branch()["error"] == "timeout"
    args, kwargs = captured[0]
    assert isinstance(args, list) and kwargs["shell"] is False and kwargs["timeout"] == 10
    assert kwargs["cwd"] == repository.resolve()


def test_real_stdio_session(repository):
    package = repository / "developer_assistant"
    package.mkdir()
    for name in ["__init__.py", "config.py", "git_review.py", "mcp_server.py"]:
        shutil.copyfile(ROOT / "developer_assistant" / name, package / name)
    async def probe(expected):
        params = StdioServerParameters(command=sys.executable, args=["-m", "developer_assistant.mcp_server"], cwd=str(repository))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {"get_current_branch", "list_project_files", "get_changed_files", "get_diff",
                        "read_file_at_commit", "list_commit_files"} == {t.name for t in tools.tools}
                assert (await call(session, "get_current_branch"))["status"] == expected
                files = await call(session, "list_project_files", {"limit": 1})
                if expected != "error":
                    assert files["total"] == 2 and files["truncated"]
                    assert len(json.dumps(files).encode()) < 32768
    asyncio.run(probe("ok"))
    git(repository, "checkout", "--detach")
    asyncio.run(probe("detached"))
    (repository / ".git").rename(repository / ".git-backup")
    asyncio.run(probe("error"))


def test_server_start_failure(monkeypatch):
    from developer_assistant import mcp_client
    monkeypatch.setattr(mcp_client.sys, "executable", "missing-python-day31.exe")
    with pytest.raises(AssistantError, match="MCP"):
        asyncio.run(mcp_client.checkout_context())

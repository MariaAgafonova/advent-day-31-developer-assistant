import json
import logging
import os
import subprocess
from collections import defaultdict, deque
from pathlib import Path

from mcp.server import MCPServer

from .config import PROJECT


class GitFailure(Exception):
    def __init__(self, code):
        self.code = code


class GitContext:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()

    def _git(self, args):
        try:
            result = subprocess.run(
                ["git", *args], cwd=self.repo, shell=False, capture_output=True,
                timeout=10, env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            )
        except subprocess.TimeoutExpired as exc:
            raise GitFailure("timeout") from exc
        except FileNotFoundError as exc:
            raise GitFailure("missing_git_or_directory") from exc
        except OSError as exc:
            raise GitFailure("git_unavailable") from exc
        if result.returncode:
            raise GitFailure("git_error")
        return result.stdout.decode("utf-8", errors="replace")

    def _check_repo(self):
        if not self.repo.is_dir() or not (self.repo / ".git").exists():
            raise GitFailure("not_repository")
        top = Path(self._git(["rev-parse", "--show-toplevel"]).strip()).resolve()
        if top != self.repo:
            raise GitFailure("not_repository")

    def branch(self):
        try:
            self._check_repo()
            branch = self._git(["branch", "--show-current"]).strip()
            return {"status": "ok" if branch else "detached", "branch": branch or None}
        except GitFailure as exc:
            return {"status": "error", "error": exc.code, "branch": None}

    def files(self, prefix="", limit=200):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            return {"status": "error", "error": "limit_must_be_1_to_500"}
        if len(prefix) > 256 or "\0" in prefix or ".." in prefix.split("/"):
            return {"status": "error", "error": "invalid_prefix"}
        try:
            self._check_repo()
            all_files = sorted(set(p for p in self._git(["ls-files", "-z"]).split("\0") if p))
        except GitFailure as exc:
            return {"status": "error", "error": exc.code}
        matched = [p for p in all_files if p.startswith(prefix)]
        groups = defaultdict(list)
        for path in matched:
            groups[path.split("/")[0] if "/" in path else ""].append(path)
        queues = [deque(sorted(paths, key=lambda p: (p.count("/"), p))) for _, paths in sorted(groups.items())]
        selected = []
        size = 0
        while queues and len(selected) < limit:
            for queue in queues[:]:
                path = queue.popleft()
                cost = len(json.dumps(path, ensure_ascii=False).encode("utf-8")) + 2
                if size + cost <= 30000 and len(selected) < limit:
                    selected.append(path)
                    size += cost
                if not queue:
                    queues.remove(queue)
                if len(selected) >= limit:
                    break
        return {
            "status": "ok", "files": selected, "total": len(all_files),
            "matched": len(matched), "returned": len(selected), "truncated": len(selected) < len(matched),
        }


server = MCPServer("natoday-checkout")
git_context = GitContext(PROJECT)


@server.tool()
def get_current_branch() -> dict:
    """Read the current checkout branch; detached HEAD and Git errors are explicit."""
    return git_context.branch()


@server.tool()
def list_project_files(prefix: str = "", limit: int = 200) -> dict:
    """List tracked Git paths, balanced across top-level directories, with bounded output."""
    return git_context.files(prefix, limit)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    server.run(transport="stdio")

"""Bounded, read-only access to commit objects. No checkout or repository code execution."""
import os
import re
import subprocess
import threading
from pathlib import Path, PurePosixPath

from .config import AssistantError


class GitReviewError(AssistantError):
    pass


def valid_path(path):
    if (not isinstance(path, str) or not path or len(path) > 4096 or
            "\\" in path or ":" in path or "\0" in path or
            PurePosixPath(path).is_absolute() or
            any(p in {"", ".", ".."} for p in path.split("/"))):
        raise GitReviewError("invalid_path")
    return path


class CommitGit:
    def __init__(self, repo):
        self.repo = Path(repo).resolve()

    def run(self, args, limit=8 * 1024 * 1024, allow_truncate=False):
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
        command = ["git", "--no-pager", "--literal-pathspecs", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "core.quotePath=false", *args]
        output = bytearray()
        overflow = threading.Event()
        try:
            with subprocess.Popen(command, cwd=self.repo, env=env, shell=False,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                def read():
                    while block := process.stdout.read(65536):
                        remaining = limit - len(output)
                        output.extend(block[:max(remaining, 0)])
                        if len(block) > remaining:
                            overflow.set()
                            process.kill()
                            break
                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                try:
                    process.wait(timeout=15)
                    reader.join(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.wait()
                    reader.join(timeout=5)
                    raise GitReviewError("git_timeout") from exc
                if reader.is_alive():
                    raise GitReviewError("git_read_timeout")
                if overflow.is_set() and not allow_truncate:
                    raise GitReviewError("git_output_limit")
                if process.returncode and not overflow.is_set():
                    raise GitReviewError("git_error: проверьте репозиторий и наличие Git-объектов")
        except OSError as exc:
            raise GitReviewError("git_unavailable") from exc
        return bytes(output), overflow.is_set()

    def text(self, args):
        return self.run(args)[0].decode("utf-8", errors="strict").strip()

    def commit(self, sha):
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", sha):
            raise GitReviewError("invalid_sha: требуется полный SHA commit")
        try:
            if self.text(["cat-file", "-t", sha]) != "commit":
                raise GitReviewError("not_commit")
        except GitReviewError as exc:
            raise GitReviewError("missing_commit: загрузите base/head и историю Git") from exc
        return sha.lower()

    def comparison(self, base, head):
        base, head = self.commit(base), self.commit(head)
        # Even an apparently available merge-base can be wrong with shallow boundaries.
        if self.text(["rev-parse", "--is-shallow-repository"]) == "true":
            raise GitReviewError("shallow_history: выполните git fetch --unshallow origin")
        bases = self.text(["merge-base", "--all", base, head]).splitlines()
        if len(bases) != 1:
            raise GitReviewError("ambiguous_merge_base: требуется единственный общий предок")
        return {"base": base, "head": head, "merge_base": bases[0]}

    @staticmethod
    def page(items, offset, limit):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise GitReviewError("invalid_pagination")
        selected, size = [], 0
        import json
        for item in items[offset:offset + limit]:
            cost = len(json.dumps(item).encode())
            if size + cost > 24000 and selected:
                break
            selected.append(item)
            size += cost
        next_offset = offset + len(selected)
        return {"files": selected, "total": len(items), "truncated": next_offset < len(items),
                "next_offset": next_offset if next_offset < len(items) else None}

    def changed_files(self, base, head, offset=0, limit=100):
        comparison = self.comparison(base, head)
        raw, _ = self.run(["diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
                           "--find-renames=50%", comparison["merge_base"], comparison["head"], "--"])
        tokens = iter(raw.decode("utf-8", errors="strict").rstrip("\0").split("\0") if raw else [])
        files = []
        for status in tokens:
            old = next(tokens)
            renamed = status.startswith("R")
            path = next(tokens) if renamed else old
            kind = "renamed" if renamed else {"A": "added", "D": "deleted"}.get(status, "modified")
            files.append({"path": path, "change_type": kind, "old_path": old if renamed else None})
        return {"status": "ok", **comparison, **self.page(files, offset, limit)}

    def tree(self, commit, offset=0, limit=100):
        commit = self.commit(commit)
        raw, _ = self.run(["ls-tree", "-rz", "--full-tree", commit])
        files = []
        for record in raw.split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split()
            files.append({"path": path.decode("utf-8", errors="strict"), "mode": mode, "type": kind})
        return {"status": "ok", "commit": commit, **self.page(files, offset, limit)}

    def read_file(self, commit, path, max_bytes=512 * 1024):
        commit, path = self.commit(commit), valid_path(path)
        if type(max_bytes) is not int or not 1 <= max_bytes <= 512 * 1024:
            raise GitReviewError("invalid_file_limit")
        raw, _ = self.run(["ls-tree", "-z", commit, "--", path])
        if not raw:
            return {"status": "missing", "path": path, "commit": commit}
        meta, actual = raw.rstrip(b"\0").split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        if actual.decode("utf-8") != path or kind != "blob" or mode not in {"100644", "100755"}:
            return {"status": "unsupported", "path": path, "reason": "symlink_submodule_or_directory"}
        size = int(self.text(["cat-file", "-s", oid]))
        result = {"status": "ok", "path": path, "commit": commit, "bytes": size, "truncated": size > max_bytes}
        if size > max_bytes:
            return {**result, "text": None, "line_count": None, "reason": "file_size_limit"}
        data, _ = self.run(["cat-file", "blob", oid], limit=max_bytes)
        try:
            if b"\0" in data:
                raise UnicodeError()
            text = data.decode("utf-8-sig")
        except UnicodeError:
            return {**result, "binary": True, "text": None, "line_count": None}
        return {**result, "binary": False, "text": text, "line_count": len(text.splitlines())}

    def diff(self, base, head, path, old_path=None, max_bytes=65536):
        comparison = self.comparison(base, head)
        paths = [valid_path(path)]
        if old_path and old_path != path:
            paths.append(valid_path(old_path))
        if type(max_bytes) is not int or not 256 <= max_bytes <= 65536:
            raise GitReviewError("invalid_diff_limit")
        raw, truncated = self.run(["diff", "--no-ext-diff", "--no-textconv", "--no-color",
                                  "--find-renames=50%", "--unified=3", comparison["merge_base"],
                                  comparison["head"], "--", *paths], max_bytes, allow_truncate=True)
        if truncated:
            raw = raw.rsplit(b"\n", 1)[0] + b"\n"
        text = raw.decode("utf-8", errors="replace")
        return {"status": "ok", **comparison, "path": path, "old_path": old_path,
                "diff": text, "truncated": truncated, "binary": bool(re.search(r"^Binary files .* differ$", text, re.M))}

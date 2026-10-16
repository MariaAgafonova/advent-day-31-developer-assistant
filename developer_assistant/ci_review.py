"""GitHub workflow_run adapter. All downloaded inputs are bounded JSON, never code."""
import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MAX_JSON = 16384


class SkipReview(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bounded_read(response, maximum):
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("GitHub response exceeds limit")
    return data


def github(endpoint, token, *, archive=False):
    if not endpoint.startswith("/repos/"):
        raise ValueError("Invalid API endpoint")
    request = urllib.request.Request("https://api.github.com" + endpoint,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "User-Agent": "natoday-review", "X-GitHub-Api-Version": "2022-11-28"})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            data = bounded_read(response, 1024 * 1024)
    except urllib.error.HTTPError as exc:
        if not archive or exc.code != 302:
            raise RuntimeError(f"GitHub HTTP {exc.code}") from None
        location = exc.headers.get("Location", "")
        url = urllib.parse.urlsplit(location)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Invalid artifact redirect")
        # Signed download URL: never forward the GitHub authorization header.
        with urllib.request.urlopen(location, timeout=30) as response:
            data = bounded_read(response, 1024 * 1024)
    return data if archive else json.loads(data)


def read_request(archive):
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        if len(entries) != 1 or entries[0].filename != "request.json" or entries[0].file_size > MAX_JSON:
            raise ValueError("Invalid metadata artifact")
        # Do not extract archive members to the filesystem.
        with zipped.open(entries[0]) as source:
            value = json.loads(bounded_read(source, MAX_JSON))
    if set(value) != {"number", "base", "head", "repository"}:
        raise ValueError("Invalid PR metadata fields")
    if type(value["number"]) is not int or value["number"] <= 0:
        raise ValueError("Invalid PR number")
    for key in ["base", "head"]:
        if not isinstance(value[key], str) or not re.fullmatch("[0-9a-f]{40}", value[key]):
            raise ValueError("Invalid commit SHA")
    return value


def validate_request(request, run, pr, repository):
    if request["repository"] != repository or pr["base"]["repo"]["full_name"] != repository:
        raise ValueError("Repository mismatch")
    if run["event"] != "pull_request" or run["path"].split("@")[0] != ".github/workflows/review-request.yml":
        raise ValueError("Unexpected upstream workflow")
    if run["conclusion"] != "success":
        raise SkipReview("Первый workflow не завершился успешно; ревью пропущено.")
    if not pr.get("head", {}).get("repo") or pr["head"]["repo"]["full_name"] != repository:
        raise SkipReview("PR из форка: генерация с секретом отключена; выполните локальное ревью.")
    associations = run.get("pull_requests", [])
    associated = any(p["number"] == request["number"] and p["head"]["sha"] == request["head"] for p in associations)
    if not associated:
        raise ValueError("PR is not associated with this workflow run and head SHA")
    if pr["state"] != "open" or pr["base"]["sha"] != request["base"] or pr["head"]["sha"] != request["head"]:
        raise SkipReview("Устаревшее событие: PR закрыт либо base/head уже изменились.")


def status_artifact(output, status, reason, request=None):
    """Stdlib-only fallback, including dependency installation failures."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    scope = {"base": (request or {}).get("base"), "head": (request or {}).get("head"),
             "merge_base": None, "changed_files": None, "reviewed_files": 0, "partial_files": 0,
             "mcp": False, "rag": False, "llm": False}
    report = {"schema_version": 1, "status": status, "scope": scope, "findings": [],
              "skipped": [], "limitations": [reason], "error": reason if status == "error" else None}
    text = ("# AI Code Review\n\n## Область проверки\n\n"
            f"Статус: **{status}**.\n\n{reason}\n\n"
            f"Base: {scope['base']}; head: {scope['head']}.\n\n"
            "Проверено файлов: 0. MCP, RAG и LLM не вызывались.\n\n")
    for title in ["Потенциальные баги", "Архитектурные проблемы", "Рекомендации"]:
        text += f"## {title}\n\nРевью не выполнялось; отсутствие замечаний не является результатом проверки.\n\n"
    text += "## Пропущенные файлы и ограничения\n\n" + reason + "\n"
    output.write_text(text, encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(output, target, prepared):
    request = None
    status_artifact(output, "error", "Подготовка ревью ещё не завершена.")
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        run_id = event["workflow_run"]["id"]
        if type(run_id) is not int or run_id <= 0:
            raise ValueError("Invalid run ID")
        token = os.environ["GH_TOKEN"]
        run = github(f"/repos/{repository}/actions/runs/{run_id}", token)
        if run["repository"]["full_name"] != repository or run["id"] != run_id:
            raise ValueError("Run identity mismatch")
        if run["conclusion"] != "success":
            raise SkipReview("Первый workflow не завершился успешно; генерация пропущена.")
        artifacts = github(f"/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100", token)
        selected = [a for a in artifacts["artifacts"] if a["name"] == "review-request" and not a["expired"]]
        if len(selected) != 1 or selected[0]["size_in_bytes"] > 1024 * 1024:
            raise ValueError("Missing or oversized request artifact")
        archive = github(f"/repos/{repository}/actions/artifacts/{int(selected[0]['id'])}/zip", token, archive=True)
        request = read_request(archive)
        pr = github(f"/repos/{repository}/pulls/{request['number']}", token)
        validate_request(request, run, pr, repository)
        target = Path(target).resolve()
        target.mkdir(parents=True, exist_ok=False)
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP"}}
        auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                   GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + auth)
        def git(*args):
            result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, *args],
                cwd=target, env=env, shell=False, capture_output=True, timeout=180)
            if result.returncode:
                raise RuntimeError("Git object fetch failed")
        git("init", "--bare")
        url = "https://github.com/" + repository + ".git"
        git("fetch", "--no-tags", url, request["base"], request["head"])
        # No working tree exists. PR scripts, attributes filters and hooks cannot execute.
        Path(prepared).write_text(json.dumps({**request, "repo": str(target)}), encoding="utf-8")
        status_artifact(output, "error", "Подготовка завершена; установка зависимостей или генерация ещё не завершена.", request)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write("ready=true\n")
        return 0
    except SkipReview as exc:
        status_artifact(output, "skipped", str(exc), request)
        print(str(exc))
        return 0
    except Exception as exc:
        # Avoid URLs, headers, raw API errors or exception messages containing credentials.
        status_artifact(output, "error", f"Подготовка CI не завершена ({type(exc).__name__}).", request)
        print("CI preparation failed: " + type(exc).__name__, file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--output", default="artifacts/review.md")
    parser.add_argument("--prepared", default=".data/review-request.json")
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.command == "prepare":
        Path(args.prepared).parent.mkdir(parents=True, exist_ok=True)
        return prepare(args.output, args.target, args.prepared)
    request = json.loads(Path(args.prepared).read_text(encoding="utf-8"))
    from .cli import main as cli_main
    sys.argv = ["developer_assistant", "review", "--repo", request["repo"], "--base", request["base"],
                "--head", request["head"], "--output", args.output]
    code = cli_main()
    return 0 if code == 3 else code


if __name__ == "__main__":
    raise SystemExit(main())

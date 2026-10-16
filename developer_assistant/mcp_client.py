import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROOT, AssistantError


@asynccontextmanager
async def connect(repo=None):
    args = ["-m", "developer_assistant.mcp_server"]
    if repo is not None:
        args.extend(["--repo", str(repo)])
    params = StdioServerParameters(
        command=sys.executable, args=args,
        cwd=str(ROOT), env={"PYTHONUTF8": "1"},
    )
    async with stdio_client(params, errlog=sys.stderr) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=20) as session:
            async with asyncio.timeout(20):
                await session.initialize()
            yield session


async def call(session, tool, arguments=None):
    result = await session.call_tool(tool, arguments or {}, read_timeout_seconds=20)
    if result.is_error:
        raise AssistantError(f"MCP-инструмент {tool} вернул ошибку.")
    data = result.structured_content
    if data is None:
        for block in result.content:
            if block.type == "text":
                data = json.loads(block.text)
                break
    if not isinstance(data, dict):
        raise AssistantError(f"Некорректный ответ MCP-инструмента {tool}.")
    return data


async def checked_call(session, tool, arguments=None):
    data = await call(session, tool, arguments)
    if data.get("status") == "error":
        raise AssistantError(f"MCP {tool}: {data.get('error', 'unknown_error')}")
    return data


async def pages(session, tool, arguments):
    files, offset, metadata = [], 0, None
    while True:
        result = await checked_call(session, tool, {**arguments, "offset": offset})
        metadata = result
        files.extend(result["files"])
        next_offset = result.get("next_offset")
        if next_offset is None:
            break
        if next_offset <= offset:
            raise AssistantError("MCP: некорректная пагинация")
        offset = next_offset
    return {**metadata, "files": files, "truncated": False}


async def checkout_context(include_files=True):
    try:
        async with connect() as session:
            branch = await call(session, "get_current_branch")
            files = await call(session, "list_project_files") if include_files else None
            return {"branch": branch, "files": files}
    except Exception as exc:
        logging.getLogger(__name__).exception("Ошибка MCP stdio")
        raise AssistantError("Не удалось запустить MCP-сервер или завершить обмен по stdio. Проверьте зависимости и mcp-check; подробности сервера выводятся в stderr.") from exc

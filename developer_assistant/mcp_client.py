import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROOT, AssistantError


@asynccontextmanager
async def connect():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "developer_assistant.mcp_server"],
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


async def checkout_context(include_files=True):
    try:
        async with connect() as session:
            branch = await call(session, "get_current_branch")
            files = await call(session, "list_project_files") if include_files else None
            return {"branch": branch, "files": files}
    except Exception as exc:
        logging.getLogger(__name__).exception("Ошибка MCP stdio")
        raise AssistantError("Не удалось запустить MCP-сервер или завершить обмен по stdio. Проверьте зависимости и mcp-check; подробности сервера выводятся в stderr.") from exc

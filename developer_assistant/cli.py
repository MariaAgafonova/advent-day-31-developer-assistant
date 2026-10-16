import argparse
import asyncio
import json
import logging
import sys

from .config import AssistantError, api_settings, load_environment


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


class Assistant:
    def __init__(self):
        self.search = None

    async def help(self):
        from .mcp_client import checkout_context
        print("/help <вопрос> — ответ о приложении «На сегодня» по документации и Git-контексту\n/exit — выход\nПримеры:\n/help Как устроен проект?\n/help Как собрать Android-приложение?\n/help Какая сейчас git-ветка?")
        checkout = await checkout_context(include_files=False)
        print("Текущий checkout (MCP): " + json.dumps(checkout["branch"], ensure_ascii=False))

    async def context(self, question):
        from .index import Search
        from .mcp_client import checkout_context
        from .answers import make_context
        if not question.strip():
            raise AssistantError("Введите вопрос после /help.")
        checkout = await checkout_context()
        if self.search is None:
            self.search = await asyncio.to_thread(Search)
        hits = await asyncio.to_thread(self.search.query, question)
        return make_context(question, hits, checkout)

    async def ask(self, question, context_only=False):
        from .answers import generate
        if not context_only:
            api_settings()
        context = await self.context(question)
        if context_only:
            print_json(context)
        else:
            print(await asyncio.to_thread(generate, context))

    async def chat(self):
        print("Ассистент «На сегодня». /help — возможности, /exit — выход.")
        while True:
            try:
                line = (await asyncio.to_thread(input, "> ")).strip()
            except EOFError:
                return
            if line == "/exit":
                return
            try:
                if line == "/help":
                    await self.help()
                elif line.startswith("/help "):
                    await self.ask(line[6:].strip())
                elif line:
                    print("Используйте /help <вопрос>, /help или /exit.")
            except AssistantError as exc:
                print(str(exc), file=sys.stderr)


def main():
    for stream in [sys.stdout, sys.stderr]:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    load_environment()
    parser = argparse.ArgumentParser(description="RAG + MCP ассистент документации «На сегодня»")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("index", help="Построить или полностью переиндексировать документацию")
    commands.add_parser("chat", help="Интерактивный режим /help")
    commands.add_parser("mcp-check", help="Проверить настоящий MCP без API модели")
    search_parser = commands.add_parser("search", help="Поиск без API модели")
    search_parser.add_argument("question")
    ask_parser = commands.add_parser("ask", help="Один вопрос (эквивалент /help <вопрос>)")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--context-only", action="store_true", help="Показать реальный RAG/MCP-контекст без генерации")
    args = parser.parse_args()
    try:
        if args.command == "index":
            from .index import build_index
            result = build_index()
            print_json({key: result[key] for key in ["file_count", "chunk_count", "reused_embeddings", "embedding"]} | {"skipped_count": len(result["skipped"])})
        elif args.command == "search":
            from .index import Search
            print_json(Search().query(args.question))
        elif args.command == "mcp-check":
            from .mcp_client import checkout_context
            result = asyncio.run(checkout_context())
            print_json(result)
            return 0 if all(v["status"] in {"ok", "detached"} for v in result.values()) else 1
        elif args.command == "chat":
            asyncio.run(Assistant().chat())
        elif args.command == "ask":
            asyncio.run(Assistant().ask(args.question, args.context_only))
        return 0
    except AssistantError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nРабота завершена.", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"Операция не завершена ({type(exc).__name__}). Проверьте установку зависимостей, доступ к файлам и повторите команду.", file=sys.stderr)
        logging.debug("Техническая причина", exc_info=True)
        return 1

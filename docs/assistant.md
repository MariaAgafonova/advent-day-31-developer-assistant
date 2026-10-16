# RAG/MCP-ассистент разработчика

Дополнительный терминальный Python-инструмент изучает документацию «На сегодня» и Git-контекст того же репозитория. Android-приложение работает самостоятельно: ему не нужны Python и API модели.

## Установка и запуск в PowerShell

Из корня репозитория, Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

Для DeepSeek заполните `.env` (настоящий ключ не включайте в Git):

```dotenv
DEEPSEEK_API_KEY=ваш-ключ
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT_SECONDS=90
DEEPSEEK_MAX_TOKENS=1024
```

Непустой `DEEPSEEK_API_KEY` выбирает DeepSeek, даже если заданы настройки OpenAI. Переменные текущего процесса имеют приоритет над `.env`. Для OpenAI оставьте `DEEPSEEK_API_KEY` пустым и задайте `OPENAI_API_KEY` и `OPENAI_MODEL`.

Запуск:

```powershell
.\.venv\Scripts\python.exe -m developer_assistant index
.\.venv\Scripts\python.exe -m developer_assistant mcp-check
.\.venv\Scripts\python.exe -m developer_assistant chat
```

Команды чата: `/help`, `/help Как устроен проект?`, `/help Какая сейчас git-ветка?`, `/exit`.

Без API:

```powershell
.\.venv\Scripts\python.exe -m developer_assistant search "Как добавлять задачи голосом?"
.\.venv\Scripts\python.exe -m developer_assistant ask "Как устроен проект?" --context-only
.\.venv\Scripts\python.exe -m pytest -q -o addopts=
```

Первый запуск скачивает multilingual-e5-small. После загрузки при недоступном Hugging Face можно задать `$env:HF_HUB_OFFLINE = "1"`. Три live_api теста пропускаются без API-ключа. С ключом они выполняют настоящие платные запросы выбранного провайдера. Локальные тесты без API: `python -m pytest -q -o addopts= -m "not live_api"`.

## Поток данных

README/документы docs → фрагменты по разделам с путём и строками → multilingual sentence-transformers embeddings → локальный Chroma → до 6 фрагментов по вопросу → DeepSeek Chat Completions либо OpenAI Responses API → русский ответ с источниками путь:строки. Переиндексация меняет снимок, переиспользует неизменённые embeddings и убирает удалённые документы.

DeepSeek получает JSON schema и пример формата в системной инструкции; JSON Output проверяется локально через Pydantic, затем проверяются ID источников. Для ответа о структуре при доступных документах и MCP_FILES обязательны ссылки на обе группы источников; ответ без них отклоняется. Неполный или обрезанный ответ не показывается как готовый: увеличьте `DEEPSEEK_MAX_TOKENS`, если 1024 не хватает. Thinking отключён для коротких ответов. Таймаут относится к одной попытке; SDK допускает один повтор запроса.

По [официальной документации DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/) имя `deepseek-v4-flash` остаётся допустимым устаревшим псевдонимом и обслуживается текущей Flash-моделью; рекомендованное имя — `deepseek-flash`. Настройка пользователя не заменяется автоматически. Проверены [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) и [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 15.10.2026.

MCP SDK запускает отдельный stdio-процесс. `get_current_branch` и `list_project_files` читают фиксированный корень этого репозитория через Git со списком аргументов и таймаутом. В контексте модели документация и факты checkout разделены. Текст документов считается недоверенными данными.

## Ограничения

На этой Windows-машине нативный HNSW вызывал сбой, поэтому используется сохраняемый в SQLite точный буфер Chroma. Поиск линейный и подходит для небольшого учебного корпуса. Не выполняйте переиндексацию одновременно с работающим чатом. Индекс и кеш модели не включаются в Git.

Наличие цитат и проверка ID источников не гарантируют семантическую точность модели. Отсутствие документации не доказывает отсутствия функции. При незаданном ключе приложение не подменяет ответы заготовками.

# RAG/MCP-ассистент разработчика

Дополнительный терминальный Python-инструмент изучает документацию «На сегодня» и Git-контекст того же репозитория. Android-приложение работает самостоятельно: ему не нужны Python и OpenAI.

## Установка и запуск в PowerShell

Из корня репозитория, Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

Для генерации задайте `OPENAI_API_KEY` и `OPENAI_MODEL` в .env либо в окружении:

```powershell
$env:OPENAI_API_KEY = "ваш-ключ"
$env:OPENAI_MODEL = "модель-из-вашего-аккаунта"
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

Первый запуск скачивает multilingual-e5-small. После загрузки при недоступном Hugging Face можно задать `$env:HF_HUB_OFFLINE = "1"`. Три live_api теста пропускаются без API-ключа и модели.

## Поток данных

README/документы docs → фрагменты по разделам с путём и строками → multilingual sentence-transformers embeddings → локальный Chroma → до 6 фрагментов по вопросу → OpenAI Responses API → русский ответ с источниками путь:строки. Переиндексация меняет снимок, переиспользует неизменённые embeddings и убирает удалённые документы.

MCP SDK запускает отдельный stdio-процесс. `get_current_branch` и `list_project_files` читают фиксированный корень этого репозитория через Git со списком аргументов и таймаутом. В контексте модели документация и факты checkout разделены. Текст документов считается недоверенными данными.

## Ограничения

На этой Windows-машине нативный HNSW вызывал сбой, поэтому используется сохраняемый в SQLite точный буфер Chroma. Поиск линейный и подходит для небольшого учебного корпуса. Не выполняйте переиндексацию одновременно с работающим чатом. Индекс и кеш модели не включаются в Git.

Наличие цитат и проверка ID источников не гарантируют семантическую точность модели. Отсутствие документации не доказывает отсутствия функции. При незаданном ключе приложение не подменяет ответы заготовками.

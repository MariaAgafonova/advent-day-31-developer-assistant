# День 31 — «На сегодня»

Android-приложение и дополнительный RAG/MCP-ассистент находятся в одном Git-репозитории, непосредственно в этой папке. Клон glab удалён с разрешения пользователя.

Основные материалы:

- [README.md](README.md) — возможности, запуск, сборка, голос, уведомления и GitLab.
- [docs/implementation-plan.md](docs/implementation-plan.md) — этапы, архитектура и критерии.
- [docs/verification.md](docs/verification.md) — фактически выполненные проверки и ограничения.
- [docs/assistant.md](docs/assistant.md) — отдельный Python RAG/MCP-инструмент.

APK: `app/build/outputs/apk/debug/app-debug.apk`.

Android-приложение не требует OpenAI API. Для Python-генерации ключ/модель не заданы, поэтому соответствующие live API проверки пропущены.

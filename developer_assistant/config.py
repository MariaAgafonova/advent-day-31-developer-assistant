import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT
DATA = ROOT / ".data"
MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_DIMENSION = 384
CHUNK_TOKENS = 450
OVERLAP_TOKENS = 40


class AssistantError(Exception):
    pass


def load_environment():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)


def api_settings():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "").strip()
    missing = [name for name, value in [("OPENAI_API_KEY", key), ("OPENAI_MODEL", model)] if not value]
    if missing:
        raise AssistantError("Не заданы " + ", ".join(missing) + ". Заполните .env или переменные окружения. Индексация, search и mcp-check доступны без API.")
    return key, model

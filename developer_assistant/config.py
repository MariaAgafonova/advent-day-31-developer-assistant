import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

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


@dataclass(frozen=True)
class ApiSettings:
    provider: str
    api_key: str = field(repr=False)
    model: str
    base_url: str
    timeout: float
    max_tokens: int


def api_settings():
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    provider = "DEEPSEEK" if deepseek_key else "OPENAI"
    key = deepseek_key or os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get(provider + "_MODEL", "").strip()
    missing = [name for name, value in [(provider + "_API_KEY", key), (provider + "_MODEL", model)] if not value]
    if missing:
        raise AssistantError("Не заданы " + ", ".join(missing) + ". Заполните .env или переменные окружения. Для DeepSeek используйте DEEPSEEK_API_KEY и DEEPSEEK_MODEL. Индексация, search и mcp-check доступны без API.")
    if provider == "OPENAI":
        return ApiSettings("OpenAI", key, model, "https://api.openai.com/v1", 60, 2000)
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
    try:
        url = urlsplit(base_url)
        valid_url = url.scheme == "https" and url.hostname and not (url.username or url.password or url.query or url.fragment)
    except ValueError:
        valid_url = False
    if not valid_url:
        raise AssistantError("DEEPSEEK_BASE_URL должен быть HTTPS URL без логина, пароля, query и fragment.")
    try:
        timeout = float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "90"))
        max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "1024"))
        if not math.isfinite(timeout) or timeout <= 0 or max_tokens <= 0:
            raise ValueError
    except ValueError as exc:
        raise AssistantError("DEEPSEEK_TIMEOUT_SECONDS должен быть положительным числом, DEEPSEEK_MAX_TOKENS — положительным целым.") from exc
    return ApiSettings("DeepSeek", key, model, base_url, timeout, max_tokens)

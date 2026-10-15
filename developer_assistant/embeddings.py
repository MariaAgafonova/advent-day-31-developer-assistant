import os
import logging

from .config import ROOT, MODEL_ID, MODEL_REVISION, MODEL_DIMENSION, AssistantError
from .documents import passage


class Embeddings:
    identity = {"model": MODEL_ID, "revision": MODEL_REVISION, "dimension": MODEL_DIMENSION}

    def __init__(self):
        os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        try:
            from sentence_transformers import SentenceTransformer
            import torch
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            self.model = SentenceTransformer(MODEL_ID, revision=MODEL_REVISION, device="cpu", trust_remote_code=False)
            self.tokenizer = self.model.tokenizer
        except Exception as exc:
            logging.getLogger(__name__).exception("Ошибка загрузки embeddings")
            raise AssistantError("Не удалось загрузить embedding-модель. Проверьте зависимости из lock-файла и доступ к Hugging Face; после первого скачивания используется локальный кеш.") from exc

    def documents(self, chunks):
        return self.model.encode([passage(c.heading, c.text) for c in chunks], normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()

    def query(self, question):
        return self.model.encode(["query: " + question], normalize_embeddings=True, show_progress_bar=False).tolist()

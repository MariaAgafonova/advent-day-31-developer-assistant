import re

import pytest

from developer_assistant.embeddings import Embeddings


class WordTokenizer:
    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=False, **kwargs):
        matches = list(re.finditer(r"\S+", text))
        result = {"input_ids": list(range(len(matches) + (2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result["offset_mapping"] = [(m.start(), m.end()) for m in matches]
        return result


class FakeEmbeddings:
    identity = Embeddings.identity
    tokenizer = WordTokenizer()

    def documents(self, chunks):
        return [[1.0] + [0.0] * 383 for _ in chunks]

    def query(self, question):
        return [[1.0] + [0.0] * 383]


@pytest.fixture
def fake_embeddings():
    return FakeEmbeddings()

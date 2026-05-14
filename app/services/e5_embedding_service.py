from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Literal

from app.core.config import get_settings

MODEL_NAME = "intfloat/multilingual-e5-base"
MODEL_SHORT = "multilingual-e5-base"
EMBEDDING_DIMENSIONS = 768


def normalize_embedding_text(text: str | None) -> str:
    value = unicodedata.normalize("NFC", str(text or ""))
    return " ".join(value.split())


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    model_name = settings.e5_model_name or MODEL_NAME
    return SentenceTransformer(model_name)


def _embed(texts: list[str], *, kind: Literal["query", "passage"]) -> list[list[float]]:
    prefix = "query: " if kind == "query" else "passage: "
    inputs = [prefix + normalize_embedding_text(text) for text in texts]
    embeddings = _model().encode(inputs, normalize_embeddings=True)
    return embeddings.astype("float32").tolist()


def embed_passage(text: str | None) -> list[float]:
    normalized = normalize_embedding_text(text)
    if not normalized:
        return []
    return _embed([normalized], kind="passage")[0]


def embed_query(text: str | None) -> list[float]:
    normalized = normalize_embedding_text(text)
    if not normalized:
        return []
    return _embed([normalized], kind="query")[0]

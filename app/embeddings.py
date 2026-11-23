
"""Local embedding model for financial-document search."""

from functools import lru_cache

from sentence_transformers import SentenceTransformer


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_model():
    return SentenceTransformer(
        MODEL_NAME,
        device="cpu",
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    vectors = get_model().encode(
        texts,
        batch_size=16,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 16,
    )

    return vectors.tolist()

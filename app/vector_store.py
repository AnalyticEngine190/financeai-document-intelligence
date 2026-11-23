
"""Persistent vector storage and semantic retrieval."""

from pathlib import Path

import chromadb

from app.embeddings import MODEL_NAME, embed_texts


# Keep the local database outside OneDrive.
DB_PATH = Path.home() / "FinanceAI_Local" / "chroma_db"
COLLECTION_NAME = "financial_reports_minilm_v1"


def get_collection():
    client = chromadb.PersistentClient(path=str(DB_PATH))

    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        metadata={"embedding_model": MODEL_NAME},
    )


def index_chunks(chunks: list[dict]) -> int:
    if not chunks:
        return 0

    collection = get_collection()

    for start in range(0, len(chunks), 32):
        batch = chunks[start:start + 32]
        texts = [chunk["text"] for chunk in batch]

        collection.upsert(
            ids=[chunk["id"] for chunk in batch],
            documents=texts,
            embeddings=embed_texts(texts),
            metadatas=[
                {
                    "document_id": chunk["document_id"],
                    "filename": chunk["filename"],
                    "page_number": chunk["page_number"],
                    "chunk_index": chunk["chunk_index"],
                }
                for chunk in batch
            ],
        )

    return len(chunks)


def search_chunks(
    question: str,
    top_k: int = 5,
    document_id: str | None = None,
) -> list[dict]:
    if not question.strip():
        raise ValueError("Question must not be empty")

    if top_k < 1:
        raise ValueError("top_k must be positive")

    collection = get_collection()

    if collection.count() == 0:
        return []

    where = (
        {"document_id": document_id}
        if document_id
        else None
    )

    result = collection.query(
        query_embeddings=embed_texts([question]),
        n_results=min(top_k, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    matches = []

    for text, metadata, distance in zip(
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0],
    ):
        matches.append({
            "text": text,
            "document": metadata["filename"],
            "document_id": metadata["document_id"],
            "page": metadata["page_number"],
            "distance": distance,
        })

    return matches

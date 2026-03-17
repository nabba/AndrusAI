import chromadb
from sentence_transformers import SentenceTransformer
from pathlib import Path
import uuid

PERSIST_DIR = Path("/app/workspace/memory")

_model = SentenceTransformer("all-MiniLM-L6-v2")  # Runs locally, no API


def get_client():
    client = chromadb.PersistentClient(path=str(PERSIST_DIR))
    return client


def store(collection_name: str, text: str, metadata: dict = None):
    client = get_client()
    col = client.get_or_create_collection(collection_name)
    embedding = _model.encode(text).tolist()
    col.add(
        documents=[text],
        embeddings=[embedding],
        metadatas=[metadata or {}],
        ids=[str(uuid.uuid4())],
    )


def retrieve(collection_name: str, query: str, n: int = 5) -> list[str]:
    client = get_client()
    col = client.get_or_create_collection(collection_name)
    if col.count() == 0:
        return []
    embedding = _model.encode(query).tolist()
    results = col.query(
        query_embeddings=[embedding], n_results=min(n, col.count())
    )
    return results["documents"][0]

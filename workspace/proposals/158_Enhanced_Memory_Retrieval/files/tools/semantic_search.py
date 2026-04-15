from sentence_transformers import SentenceTransformer, util
from typing import List, Dict
import numpy as np

model = SentenceTransformer('all-MiniLM-L6-v2')

def semantic_search(query: str, documents: List[str], top_k: int = 3) -> List[Dict]:
    """
    Perform semantic search on documents
    """
    query_embedding = model.encode(query)
    doc_embeddings = model.encode(documents)
    
    cos_similarities = util.cos_sim(query_embedding, doc_embeddings)[0]
    top_indices = np.argsort(-cos_similarities)[:top_k]

    results = []
    for idx in top_indices:
        results.append({
            'document': documents[idx],
            'score': float(cos_similarities[idx])
        })
    return results
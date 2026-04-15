from sentence_transformers import SentenceTransformer, util
import numpy as np

class SemanticSearch:
    def __init__(self):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')

    def search(self, query, documents):
        query_embedding = self.model.encode(query)
        doc_embeddings = self.model.encode(documents)
        similarities = util.dot_score(query_embedding, doc_embeddings)
        return np.argsort(similarities[0].numpy())[::-1]

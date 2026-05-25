import numpy as np
import json
import os

from core.Ollama_ipex import OllamaIPEX

get_inquery_schema = {
    "type": "function",
    "function": {
        "name": "get_inquery_info",
        "description": "Search the knowledge base for information relevant to the user's query. Use this when the user asks a question that requires looking up stored documents or data.",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "The search query to look up in the knowledge base"
                }
            },
            "required": ["search"]
        }
    }
}

embed_client = OllamaIPEX(
    model_name="nomic-embed-text:137m-v1.5-fp16",
    ollama_url="http://192.168.1.3:11435"
)
def get_most_similar(query_text, original_data, doc_vectors, doc_norms):
    """Return the most similar original_data item using pre‑normalised doc vectors."""
    prefixed_query = f"search_query: {query_text}"
    query_vector = np.array(embed_client.embed(prefixed_query)[0])
    query_norm = query_vector / np.linalg.norm(query_vector)
    similarities = np.dot(doc_norms, query_norm)
    best_index = np.argmax(similarities)
    return original_data[best_index]

def get_inquery_info(search: str):
    """Instant, no‑I/O lookup using pre‑computed embeddings."""
    with open("my_knowledge_base/output.json", "r") as f:
        dogmas = json.load(f)
    raw_texts = [d["content"] for d in dogmas]
    prefixed_docs = [f"search_document: {t}" for t in raw_texts]
    doc_vectors = np.array(embed_client.embed(prefixed_docs))
    doc_norms = doc_vectors / np.linalg.norm(doc_vectors, axis=1, keepdims=True)

    return get_most_similar(search, dogmas, doc_vectors, doc_norms)
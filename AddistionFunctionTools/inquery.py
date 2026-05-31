import numpy as np
import json
import os
import docx
import fitz
from typing import List
from core.Ollama_ipex_2v import OllamaIPEX

# --- 1. SCHEMAS (Updated descriptions for multiple context) ---
get_inquery_schema = [
    {
        "type": "function",
        "function": {
            "name": "get_inquery_info",
            "description": "Retrieves the top 3 most relevant context snippets for general inquiries, business rules, and policies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "The inquiry query"}
                },
                "required": ["search"]
            }
        }
    }
]

get_appointment_schema = [
    {
        "type": "function",
        "function": {
            "name": "get_appointment_info",
            "description": "Retrieves the top 3 most relevant snippets regarding scheduling, booking, or existing appointments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "The appointment query"}
                },
                "required": ["search"]
            }
        }
    }
]

get_navigation_schema = [
    {
        "type": "function",
        "function": {
            "name": "get_navigation_info",
            "description": "Retrieves the top 3 most relevant snippets for locations, directions, or room layouts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "The location query"}
                },
                "required": ["search"]
            }
        }
    }
]

class RAGManager:
    def __init__(self):
        self.embed_client = OllamaIPEX(
            model_name="nomic-embed-text:137m-v1.5-fp16",
            ollama_url="http://192.168.1.3:11435"
        )
        self.knowledge_data = []
        self.doc_norms = None

    def build_index(self, folders: List[str]):
        all_chunks = []
        for folder in folders:
            if not os.path.exists(folder): continue
            for file in os.listdir(folder):
                path = os.path.join(folder, file)
                try:
                    if file.endswith(".pdf"):
                        with fitz.open(path) as doc:
                            text = "".join([page.get_text() for page in doc])
                    elif file.endswith(".docx"):
                        doc = docx.Document(path)
                        text = '\n'.join([p.text for p in doc.paragraphs])
                    elif file.endswith((".txt", ".md")):
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                    else: continue
                    
                    # Split into 700-character chunks with 100-character overlap
                    # This ensures "topic-closeness" isn't lost if a sentence is cut in half
                    overlap = 100
                    step = 600 
                    for i in range(0, len(text), step):
                        chunk = text[i : i + 700].strip()
                        if len(chunk) > 20: # Ignore tiny fragments
                            all_chunks.append({"content": chunk, "source": file})
                except Exception as e:
                    print(f"Error indexing {file}: {e}")

        if all_chunks:
            self.knowledge_data = all_chunks
            print(f"Indexing {len(all_chunks)} chunks...")
            texts = [f"search_document: {c['content']}" for c in all_chunks]
            vectors = np.array(self.embed_client.embed(texts))
            self.doc_norms = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def search_top_k(self, query_text: str, k=3):
        """Returns the Top K most similar items formatted for the AI."""
        if self.doc_norms is None: return "Knowledge base is empty."
        
        # Embed query
        q_vec = np.array(self.embed_client.embed(f"search_query: {query_text}")[0])
        q_norm = q_vec / np.linalg.norm(q_vec)
        
        # Calculate scores and get top K indices
        scores = np.dot(self.doc_norms, q_norm)
        top_indices = np.argsort(scores)[-k:][::-1] # Sort ascending, take last k, then reverse
        
        formatted_results = []
        for i, idx in enumerate(top_indices):
            item = self.knowledge_data[idx]
            score = scores[idx]
            # We provide the source and the content to the AI
            formatted_results.append(f"--- Context {i+1} (Source: {item['source']}, Relevance: {score:.2f}) ---\n{item['content']}")
        
        return "\n\n".join(formatted_results)

# --- 3. EXPORTED TOOLS ---
rag = RAGManager()
rag.build_index([
    os.path.join("database", "Source", "Inquery"),
    os.path.join("database", "Source", "Appointment"),
    os.path.join("database", "Source", "Navigation")
])

def get_inquery_info(search: str):
    return rag.search_top_k(search, k=3)

def get_appointment_info(search: str):
    return rag.search_top_k(search, k=3)

def get_navigation_info(search: str):
    return rag.search_top_k(search, k=3)
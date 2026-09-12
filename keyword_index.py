import os
import re
import math
import json
from collections import Counter
from typing import List, Dict, Any, Optional

def tokenize_code(text: str) -> List[str]:
    """
    Tokenizes code text by handling camelCase, snake_case, identifiers, and keywords.
    Converts to lowercase and returns a list of alphanumeric tokens.
    """
    if not text:
        return []
    # Split camelCase e.g. parsePythonAST -> parse Python AST
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    # Extract all word identifiers
    tokens = re.findall(r'[a-zA-Z0-9_]+', text)
    refined_tokens = []
    for token in tokens:
        # Split snake_case e.g. line_start -> line, start
        parts = token.split('_')
        for p in parts:
            p_lower = p.lower()
            if len(p_lower) > 1:  # filter single-char noise
                refined_tokens.append(p_lower)
    return refined_tokens


class BM25KeywordIndex:
    """
    In-memory and file-persisted BM25Okapi keyword index for chat-scoped code chunks.
    BM25 parameters: k1 = 1.5, b = 0.75.
    """

    def __init__(self, storage_dir: str = ".bm25_store", k1: float = 1.5, b: float = 0.75):
        self.storage_dir = os.path.abspath(storage_dir)
        os.makedirs(self.storage_dir, exist_ok=True)
        self.k1 = k1
        self.b = b
        # In-memory index cache per session_id: { session_id: [ {id, text, metadata, tokens, doc_len} ] }
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}

    def _get_session_file(self, session_id: str) -> str:
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', session_id or "default_session")
        return os.path.join(self.storage_dir, f"bm25_{safe_id}.json")

    def _load_session(self, session_id: str) -> List[Dict[str, Any]]:
        session_key = session_id or "default_session"
        if session_key in self.sessions:
            return self.sessions[session_key]

        filepath = self._get_session_file(session_key)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    docs = json.load(f)
                    # Recompute tokens for loaded docs
                    for doc in docs:
                        if "tokens" not in doc:
                            doc["tokens"] = tokenize_code(doc.get("text", ""))
                            doc["doc_len"] = len(doc["tokens"])
                    self.sessions[session_key] = docs
                    return docs
            except Exception as e:
                print(f"[BM25KeywordIndex] Error loading index file for '{session_key}': {e}")

        self.sessions[session_key] = []
        return self.sessions[session_key]

    def _save_session(self, session_id: str):
        session_key = session_id or "default_session"
        filepath = self._get_session_file(session_key)
        docs = self.sessions.get(session_key, [])
        try:
            # Strip runtime token fields before dumping to JSON to keep file compact
            serializable_docs = []
            for doc in docs:
                s_doc = {
                    "id": doc.get("id"),
                    "text": doc.get("text"),
                    "metadata": doc.get("metadata", {})
                }
                serializable_docs.append(s_doc)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(serializable_docs, f, indent=2)
        except Exception as e:
            print(f"[BM25KeywordIndex] Error saving index file for '{session_key}': {e}")

    def add_chunks(self, chunks: List[Dict[str, Any]], session_id: str = "default_session"):
        """Ingests new code chunks into the chat-scoped BM25 keyword index."""
        if not chunks:
            return

        docs = self._load_session(session_id)
        existing_ids = {d["id"] for d in docs}

        for chunk in chunks:
            chunk_id = chunk.get("id")
            if not chunk_id:
                continue

            text = chunk.get("text", "")
            tokens = tokenize_code(text)
            doc_entry = {
                "id": chunk_id,
                "text": text,
                "metadata": chunk.get("metadata", {}),
                "tokens": tokens,
                "doc_len": len(tokens)
            }

            if chunk_id in existing_ids:
                # Update existing chunk
                docs = [d if d["id"] != chunk_id else doc_entry for d in docs]
            else:
                docs.append(doc_entry)
                existing_ids.add(chunk_id)

        self.sessions[session_id or "default_session"] = docs
        self._save_session(session_id)

    def purge_file(self, rel_path: str, session_id: str = "default_session"):
        """Purges stale file chunk entries from the chat-scoped BM25 keyword index."""
        docs = self._load_session(session_id)
        filtered = [d for d in docs if d.get("metadata", {}).get("file_path") != rel_path]
        self.sessions[session_id or "default_session"] = filtered
        self._save_session(session_id)

    def clear(self, session_id: str = "default_session"):
        """Clears all keyword index entries for a specific chat session."""
        session_key = session_id or "default_session"
        self.sessions[session_key] = []
        filepath = self._get_session_file(session_key)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                print(f"[BM25KeywordIndex] Error removing index file: {e}")

    def count(self, session_id: str = "default_session") -> int:
        """Returns total chunk count in BM25 index for a session."""
        docs = self._load_session(session_id)
        return len(docs)

    def search(self, query: str, top_k: int = 5, session_id: str = "default_session") -> List[Dict[str, Any]]:
        """
        Executes BM25Okapi search against the chat-scoped index.
        Returns candidate chunks with calculated BM25 score.
        """
        docs = self._load_session(session_id)
        if not docs:
            return []

        query_tokens = tokenize_code(query)
        if not query_tokens:
            return []

        N = len(docs)
        avgdl = sum(d["doc_len"] for d in docs) / max(1, N)

        # Document Frequency (DF) for each query term
        df = Counter()
        for q_term in set(query_tokens):
            for d in docs:
                if q_term in d["tokens"]:
                    df[q_term] += 1

        scored_results = []
        for d in docs:
            tf_counter = Counter(d["tokens"])
            score = 0.0
            for q_term in query_tokens:
                n_q = df.get(q_term, 0)
                if n_q == 0:
                    continue

                # IDF calculation with smoothing
                idf = math.log((N - n_q + 0.5) / (n_q + 0.5) + 1.0)
                f_q = tf_counter.get(q_term, 0)

                # BM25 term score
                numerator = f_q * (self.k1 + 1.0)
                denominator = f_q + self.k1 * (1.0 - self.b + self.b * (d["doc_len"] / max(1.0, avgdl)))
                score += idf * (numerator / denominator)

            if score > 0.0:
                scored_results.append({
                    "code": d["text"],
                    "metadata": d["metadata"],
                    "bm25_score": score
                })

        scored_results.sort(key=lambda x: x["bm25_score"], reverse=True)
        return scored_results[:top_k]

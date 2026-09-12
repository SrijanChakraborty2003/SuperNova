import re
import json
import urllib.request
from typing import List, Dict, Any, Optional
from okf_extractor import Neo4jGraphManager, ChromaManager
from langchain_ollama import ChatOllama
from redis_chat_buffer import RedisChatBuffer
from keyword_index import BM25KeywordIndex


def get_best_ollama_model(preferred: str = "gemma4:31b-cloud", base_url: str = "http://localhost:11434") -> str:
    """Discovers available Ollama models dynamically and falls back to installed models."""
    try:
        url = f"{base_url.rstrip('/')}/api/tags"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            models = [m.get("name") for m in data.get("models", [])]
            if preferred in models:
                return preferred
            # Match without tag e.g. gpt-oss vs gpt-oss:120b
            pref_base = preferred.split(":")[0]
            for m in models:
                if m.startswith(pref_base):
                    return m
            if models:
                print(f"[Ollama] Preferred model '{preferred}' not found. Auto-selecting available model: '{models[0]}'")
                return models[0]
    except Exception:
        pass
    return preferred


class CodeGraphRAGPipeline:
    """Retrieval & Code Generation Engine integrating Dual-Brain storage (ChromaDB + BM25 + OKF) with Ollama LLM."""

    def __init__(self, 
                 neo4j_uri: str = "bolt://localhost:7687", 
                 neo4j_auth: tuple = ("neo4j", "test123456"),
                 chroma_host: str = "localhost", 
                 chroma_port: int = 8000,
                 redis_host: str = "localhost",
                 redis_port: int = 6379,
                 model_name: str = "gemma4:31b-cloud",
                 ollama_url: str = "http://localhost:11434"):
        
        self.neo4j_mgr = Neo4jGraphManager(uri=neo4j_uri, auth=neo4j_auth)
        self.chroma_mgr = ChromaManager(host=chroma_host, port=chroma_port)
        self.collection = self.chroma_mgr.get_or_create_collection("code_semantic_chunks")
        self.chat_buffer = RedisChatBuffer(host=redis_host, port=redis_port)
        self.keyword_index = BM25KeywordIndex()
        
        self.ollama_url = ollama_url
        self.model_name = get_best_ollama_model(preferred=model_name, base_url=ollama_url)
        
        try:
            print(f"[CodeGraphRAGPipeline] Initializing ChatOllama with model: '{self.model_name}'")
            self.llm = ChatOllama(model=self.model_name, base_url=ollama_url, temperature=0.1)
        except Exception as e:
            print(f"[CodeGraphRAGPipeline] Warning initializing ChatOllama: {e}")
            self.llm = None

    def get_collection_for_chat(self, session_id: Optional[str] = None):
        """Returns isolated ChromaDB collection dedicated to a specific chat session ID."""
        if session_id and session_id != "default_session":
            safe_name = f"chat_{re.sub(r'[^a-zA-Z0-9_-]', '_', session_id)}"
            return self.chroma_mgr.get_or_create_collection(safe_name)
        return self.collection

    def search_vector(self, query: str, n_results: int = 5, session_id: Optional[str] = None, repo_url: Optional[str] = None) -> List[Dict[str, Any]]:
        """1. Vector Search: Finds semantic code blocks using BAAI/bge-small-en-v1.5 in isolated ChromaDB collection."""
        try:
            target_collection = self.get_collection_for_chat(session_id)
            doc_count = target_collection.count()
            if doc_count == 0:
                print(f"[CodeGraphRAGPipeline] Collection for chat '{session_id}' is empty (0 chunks). Returning 0 vector results.")
                return []

            res = target_collection.query(query_texts=[query], n_results=min(n_results, doc_count))
            chunks = []
            if res and res.get("documents"):
                docs = res["documents"][0]
                metas = res["metadatas"][0] if res.get("metadatas") else [{}] * len(docs)
                for d, m in zip(docs, metas):
                    chunks.append({"code": d, "metadata": m})
            return chunks
        except Exception as e:
            print(f"[CodeGraphRAGPipeline] Vector search notice: {e}")
            return []

    def search_keyword(self, query: str, n_results: int = 5, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """2. Keyword Search: Finds lexical code blocks using BM25Okapi index."""
        try:
            return self.keyword_index.search(query=query, top_k=n_results, session_id=session_id or "default_session")
        except Exception as e:
            print(f"[CodeGraphRAGPipeline] Keyword search notice: {e}")
            return []

    def search_hybrid(self, query: str, top_k: int = 2, session_id: Optional[str] = None, repo_url: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Hybrid Search combining Vector similarity (BGE) + Sparse Keyword Search (BM25) via Reciprocal Rank Fusion (RRF).
        Returns top_k (default 2) candidate chunks.
        """
        vector_candidates = self.search_vector(query, n_results=5, session_id=session_id, repo_url=repo_url)
        keyword_candidates = self.search_keyword(query, n_results=5, session_id=session_id)

        doc_map = {}  # key -> {"code": ..., "metadata": ...}
        rrf_scores = {}
        rrf_k = 60.0

        # RRF for Vector candidates
        for rank, item in enumerate(vector_candidates, start=1):
            key = item["metadata"].get("name", "") + ":" + item["metadata"].get("file_path", "") + ":" + str(item["metadata"].get("line_start", 0))
            if not key or key == "::0":
                key = item["code"][:100]
            doc_map[key] = item
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (rrf_k + rank))

        # RRF for Keyword candidates
        for rank, item in enumerate(keyword_candidates, start=1):
            key = item["metadata"].get("name", "") + ":" + item["metadata"].get("file_path", "") + ":" + str(item["metadata"].get("line_start", 0))
            if not key or key == "::0":
                key = item["code"][:100]
            doc_map[key] = item
            rrf_scores[key] = rrf_scores.get(key, 0.0) + (1.0 / (rrf_k + rank))

        # Sort by RRF score
        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)
        hybrid_chunks = [doc_map[k] for k in sorted_keys]

        # Strictly return Top K (default 2)
        return hybrid_chunks[:top_k]

    def traverse_graph_blast_radius(self, targets: List[str]) -> Dict[str, Any]:
        """3. Graph Traversal: Pulls structural locations, line numbers, callers, and blast radius for target components."""
        graph_info = {
            "targets": targets,
            "locations": [],
            "callers": [],
            "callees": [],
            "blast_radius_files": []
        }

        if not targets:
            return graph_info

        for function_or_file in targets:
            if not function_or_file:
                continue

            if self.neo4j_mgr.is_connected and self.neo4j_mgr.driver:
                try:
                    with self.neo4j_mgr.driver.session() as session:
                        loc_res = session.run("""
                            MATCH (f:File)-[:DEFINES]->(fn:Function)
                            WHERE fn.name CONTAINS $target OR fn.id CONTAINS $target OR f.path CONTAINS $target
                            RETURN f.path AS file_path, fn.name AS func_name, fn.line_start AS line_start, fn.line_end AS line_end, fn.signature AS signature
                        """, target=function_or_file)
                        for r in loc_res:
                            loc_item = {
                                "file_path": r["file_path"],
                                "func_name": r["func_name"],
                                "line_start": r["line_start"],
                                "line_end": r["line_end"],
                                "signature": r["signature"]
                            }
                            if loc_item not in graph_info["locations"]:
                                graph_info["locations"].append(loc_item)

                        caller_res = session.run("""
                            MATCH (caller)-[r:CALLS]->(target:Function)
                            WHERE target.name CONTAINS $target OR target.id CONTAINS $target
                            RETURN caller.name AS caller_name, labels(caller)[0] AS caller_type, r.line AS line
                        """, target=function_or_file)
                        for r in caller_res:
                            caller_item = {
                                "caller_name": r["caller_name"],
                                "caller_type": r["caller_type"],
                                "line": r["line"]
                            }
                            if caller_item not in graph_info["callers"]:
                                graph_info["callers"].append(caller_item)

                        blast_res = session.run("""
                            MATCH (start {name: $target})-[*1..2]-(connected)
                            RETURN DISTINCT labels(connected)[0] AS node_type, connected.name AS name, connected.path AS path
                        """, target=function_or_file)
                        for r in blast_res:
                            node_name = r["name"] or r["path"] or "Unknown"
                            entry = f"{r['node_type']}: {node_name}"
                            if entry not in graph_info["blast_radius_files"]:
                                graph_info["blast_radius_files"].append(entry)
                except Exception as e:
                    print(f"[CodeGraphRAGPipeline] Graph traversal notice: {e}")

            # In-memory OKF triple fallback lookup
            for t in self.neo4j_mgr._in_memory_triples:
                if function_or_file.lower() in t.get("object", "").lower() or function_or_file.lower() in t.get("subject", "").lower():
                    if t.get("line_start"):
                        loc_item = {
                            "file_path": t.get("file_path", t.get("subject")),
                            "func_name": t.get("object"),
                            "line_start": t.get("line_start"),
                            "line_end": t.get("line_end")
                        }
                        if loc_item not in graph_info["locations"]:
                            graph_info["locations"].append(loc_item)
                    else:
                        entry = f"{t.get('relationship')}: {t.get('subject')} -> {t.get('object')}"
                        if entry not in graph_info["blast_radius_files"]:
                            graph_info["blast_radius_files"].append(entry)

        return graph_info

    def query_code_graph_rag(self, user_prompt: str, session_id: str = "default_session", repo_url: str = "", max_history: int = 8) -> Dict[str, Any]:
        """Full Code-GraphRAG Query Pipeline using Top 2 Hybrid Chunks & Dual OKF Blast Radius."""
        print(f"\n[CodeGraphRAG] Processing query (session: '{session_id}'): '{user_prompt}'")

        # Step 0: Fetch past N (default 8) conversation messages from Redis log buffer
        recent_history = self.chat_buffer.get_history(session_id=session_id, max_messages=max_history)
        if recent_history:
            history_lines = [f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}" for msg in recent_history]
            formatted_history = "\n".join(history_lines)
        else:
            formatted_history = "No prior conversation history for this session."

        # Step 1: Hybrid Retrieval (Dense Vector + BM25 Keyword Search) -> TOP 2 CHUNKS ONLY
        top_2_chunks = self.search_hybrid(user_prompt, top_k=2, session_id=session_id, repo_url=repo_url)
        print(f"[CodeGraphRAG] Hybrid Search retrieved {len(top_2_chunks)} top candidate code blocks.")

        # Extract target keywords for BOTH top 2 chunks for Blast Radius Traversal
        target_keywords = []
        for chunk in top_2_chunks:
            meta_name = chunk["metadata"].get("name", "")
            if meta_name:
                target_keywords.append(meta_name)
            else:
                words = [w for w in user_prompt.split() if len(w) > 3 and w.lower() not in ["update", "file", "function", "code", "show", "change"]]
                kw = words[0] if words else "main"
                if kw not in target_keywords:
                    target_keywords.append(kw)

        if not target_keywords:
            target_keywords = ["main"]

        # Step 2: Graph Traversal & Dual Blast Radius Analysis via OKF for BOTH top 2 chunks
        graph_context = self.traverse_graph_blast_radius(target_keywords)

        # Clean code text for prompt context (unescape literal \n)
        cleaned_chunks_text = []
        for c in top_2_chunks:
            chunk_code = c['code'].replace('\\n', '\n').replace('\\t', '    ')
            cleaned_chunks_text.append(chunk_code)

        joined_chunks_text = "\n---\n".join(cleaned_chunks_text) if cleaned_chunks_text else "No relevant code blocks retrieved."

        # Step 3: Format Context Prompt for Ollama LLM using ONLY TOP 2 CHUNKS & DUAL BLAST RADIUS
        formatted_prompt = f"""You are an expert Software Architect using a Code-GraphRAG System.
User Query: "{user_prompt}"

=== RECENT CONVERSATION HISTORY (Last {len(recent_history)} Messages Log Context) ===
{formatted_history}

=== 1. RETRIEVED SEMANTIC CODE BLOCKS (Top 2 Hybrid Vector + BM25 Chunks Only) ===
{joined_chunks_text}

=== 2. OPEN KNOWLEDGE FORMAT (OKF) RELATIONSHIPS (Dual Blast Radius Context) ===
Target Components: {', '.join(graph_context['targets'])}
Locations & Line Spans: {json.dumps(graph_context['locations'], indent=2)}
Inbound Callers: {json.dumps(graph_context['callers'], indent=2)}
Outbound Calls: {json.dumps(graph_context['callees'], indent=2)}
Blast Radius Impact: {json.dumps(graph_context['blast_radius_files'], indent=2)}

=== INSTRUCTIONS ===
Provide a clear, precise, and architect-level response detailing:
1. **Target File Path**: Specify the exact relative file path where modifications are needed.
2. **Line Numbers**: Specify the exact 1-based start line and end line numbers (e.g. `Lines 25 to 42`).
3. **Original Code Snippet**: Show the existing code snippet at those line numbers.
4. **Updated Code Snippet**: Provide the exact updated code block to replace it with inside a ```python ``` or ```javascript ``` code block.
5. **OKF Blast Radius Analysis**: Detail any dependent caller functions, imported modules, or connected files impacted.

Format response clearly using GitHub Markdown.
"""

        # Step 4: Model Inference
        answer = ""
        if self.llm:
            try:
                response = self.llm.invoke(formatted_prompt)
                answer = response.content
            except Exception as e:
                print(f"[CodeGraphRAGPipeline] LLM query warning ({self.model_name}): {e}")
                answer = self._format_fallback_response(user_prompt, top_2_chunks, graph_context)
        else:
            answer = self._format_fallback_response(user_prompt, top_2_chunks, graph_context)

        # Step 5: Save interaction to Redis chat buffer
        self.chat_buffer.add_message(session_id, "user", user_prompt, max_messages=max_history)
        self.chat_buffer.add_message(session_id, "assistant", answer, max_messages=max_history)

        return {
            "user_prompt": user_prompt,
            "session_id": session_id,
            "vector_chunks": top_2_chunks,  # Returns top 2 chunks
            "graph_context": graph_context,
            "chat_history": recent_history,
            "answer": answer
        }

    def _format_fallback_response(self, user_prompt: str, vector_chunks: list, graph_context: dict) -> str:
        """Formats clean markdown response when LLM model is offline or invoking."""
        if not vector_chunks:
            return "No matching code blocks found in vector database."

        top_chunk = vector_chunks[0]
        meta = top_chunk.get("metadata", {})
        file_path = meta.get("file_path", "unknown_file.py")
        s_line = meta.get("line_start", 1)
        e_line = meta.get("line_end", 20)
        code_content = top_chunk.get("code", "").replace('\\n', '\n').replace('\\t', '    ')

        output = f"""### Code-RAG Search Results

**Target File**: `{file_path}`  
**Line Numbers**: Lines {s_line} to {e_line}

#### Code Snippet:
```python
{code_content}
```

#### OKF Relationship Context:
- **Component**: `{graph_context.get('target', '')}`
- **Locations**: `{json.dumps(graph_context.get('locations', []))}`
- **Blast Radius**: `{json.dumps(graph_context.get('blast_radius_files', []))}`
"""
        return output

    def close(self):
        self.neo4j_mgr.close()

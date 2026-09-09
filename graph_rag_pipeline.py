import json
import urllib.request
from typing import List, Dict, Any, Optional
from okf_extractor import Neo4jGraphManager, ChromaManager
from langchain_ollama import ChatOllama


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
    """Retrieval & Code Generation Engine integrating Dual-Brain storage (ChromaDB + OKF) with Ollama LLM."""

    def __init__(self, 
                 neo4j_uri: str = "bolt://localhost:7687", 
                 neo4j_auth: tuple = ("neo4j", "test123456"),
                 chroma_host: str = "localhost", 
                 chroma_port: int = 8000,
                 model_name: str = "gemma4:31b-cloud",
                 ollama_url: str = "http://localhost:11434"):
        
        self.neo4j_mgr = Neo4jGraphManager(uri=neo4j_uri, auth=neo4j_auth)
        self.chroma_mgr = ChromaManager(host=chroma_host, port=chroma_port)
        self.collection = self.chroma_mgr.get_or_create_collection("code_semantic_chunks")
        
        self.ollama_url = ollama_url
        self.model_name = get_best_ollama_model(preferred=model_name, base_url=ollama_url)
        
        try:
            print(f"[CodeGraphRAGPipeline] Initializing ChatOllama with model: '{self.model_name}'")
            self.llm = ChatOllama(model=self.model_name, base_url=ollama_url, temperature=0.1)
        except Exception as e:
            print(f"[CodeGraphRAGPipeline] Warning initializing ChatOllama: {e}")
            self.llm = None

    def search_vector(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """1. Vector Search: Finds semantic code blocks using BAAI/bge-small-en-v1.5 in ChromaDB."""
        try:
            res = self.collection.query(query_texts=[query], n_results=n_results)
            chunks = []
            if res and res.get("documents"):
                docs = res["documents"][0]
                metas = res["metadatas"][0] if res.get("metadatas") else [{}] * len(docs)
                for d, m in zip(docs, metas):
                    chunks.append({"code": d, "metadata": m})
            return chunks
        except Exception as e:
            print(f"[CodeGraphRAGPipeline] Vector search warning: {e}")
            return []

    def traverse_graph_blast_radius(self, function_or_file: str) -> Dict[str, Any]:
        """2. Graph Traversal: Pulls structural locations, line numbers, callers, and blast radius."""
        graph_info = {
            "target": function_or_file,
            "locations": [],
            "callers": [],
            "callees": [],
            "blast_radius_files": []
        }

        if self.neo4j_mgr.is_connected and self.neo4j_mgr.driver:
            try:
                with self.neo4j_mgr.driver.session() as session:
                    loc_res = session.run("""
                        MATCH (f:File)-[:DEFINES]->(fn:Function)
                        WHERE fn.name CONTAINS $target OR fn.id CONTAINS $target OR f.path CONTAINS $target
                        RETURN f.path AS file_path, fn.name AS func_name, fn.line_start AS line_start, fn.line_end AS line_end, fn.signature AS signature
                    """, target=function_or_file)
                    for r in loc_res:
                        graph_info["locations"].append({
                            "file_path": r["file_path"],
                            "func_name": r["func_name"],
                            "line_start": r["line_start"],
                            "line_end": r["line_end"],
                            "signature": r["signature"]
                        })

                    caller_res = session.run("""
                        MATCH (caller)-[r:CALLS]->(target:Function)
                        WHERE target.name CONTAINS $target OR target.id CONTAINS $target
                        RETURN caller.name AS caller_name, labels(caller)[0] AS caller_type, r.line AS line
                    """, target=function_or_file)
                    for r in caller_res:
                        graph_info["callers"].append({
                            "caller_name": r["caller_name"],
                            "caller_type": r["caller_type"],
                            "line": r["line"]
                        })

                    blast_res = session.run("""
                        MATCH (start {name: $target})-[*1..2]-(connected)
                        RETURN DISTINCT labels(connected)[0] AS node_type, connected.name AS name, connected.path AS path
                    """, target=function_or_file)
                    for r in blast_res:
                        node_name = r["name"] or r["path"] or "Unknown"
                        graph_info["blast_radius_files"].append(f"{r['node_type']}: {node_name}")
            except Exception as e:
                print(f"[CodeGraphRAGPipeline] Graph traversal notice: {e}")

        # In-memory OKF triple fallback lookup
        if not graph_info["locations"]:
            for t in self.neo4j_mgr._in_memory_triples:
                if function_or_file.lower() in t.get("object", "").lower() or function_or_file.lower() in t.get("subject", "").lower():
                    if t.get("line_start"):
                        graph_info["locations"].append({
                            "file_path": t.get("file_path", t.get("subject")),
                            "func_name": t.get("object"),
                            "line_start": t.get("line_start"),
                            "line_end": t.get("line_end")
                        })
                    else:
                        graph_info["blast_radius_files"].append(f"{t.get('relationship')}: {t.get('subject')} -> {t.get('object')}")

        return graph_info

    def query_code_graph_rag(self, user_prompt: str) -> Dict[str, Any]:
        """Full Code-GraphRAG Query Pipeline: Vector -> Graph Traversal -> LLM Generation."""
        print(f"\n[CodeGraphRAG] Processing query: '{user_prompt}'")

        # Step 1: Semantic Vector Search via BAAI/bge-small-en-v1.5
        vector_chunks = self.search_vector(user_prompt, n_results=5)
        print(f"[CodeGraphRAG] Vector Search retrieved {len(vector_chunks)} candidate code blocks.")

        # Identify key target keyword for Graph Traversal
        target_keyword = ""
        if vector_chunks and vector_chunks[0]["metadata"].get("name"):
            target_keyword = vector_chunks[0]["metadata"]["name"]
        else:
            words = [w for w in user_prompt.split() if len(w) > 3 and w.lower() not in ["update", "file", "function", "code", "show", "change"]]
            target_keyword = words[0] if words else "main"

        # Step 2: Graph Traversal & Blast Radius Analysis via OKF
        graph_context = self.traverse_graph_blast_radius(target_keyword)

        # Clean code text for prompt context (unescape literal \n)
        cleaned_chunks_text = []
        for c in vector_chunks:
            chunk_code = c['code'].replace('\\n', '\n').replace('\\t', '    ')
            cleaned_chunks_text.append(chunk_code)

        joined_chunks_text = "\n---\n".join(cleaned_chunks_text)

        # Step 3: Format Context Prompt for Ollama LLM
        formatted_prompt = f"""You are an expert Software Architect using a Code-GraphRAG System.
User Query: "{user_prompt}"

=== 1. RETRIEVED SEMANTIC CODE BLOCKS (Vector DB - BAAI/bge-small-en-v1.5) ===
{joined_chunks_text}

=== 2. OPEN KNOWLEDGE FORMAT (OKF) RELATIONSHIPS ===
Target Component: {graph_context['target']}
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
                answer = self._format_fallback_response(user_prompt, vector_chunks, graph_context)
        else:
            answer = self._format_fallback_response(user_prompt, vector_chunks, graph_context)

        return {
            "user_prompt": user_prompt,
            "vector_chunks": vector_chunks,
            "graph_context": graph_context,
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

import json
from typing import List, Dict, Any, Optional
from okf_extractor import Neo4jGraphManager, ChromaManager
from langchain_ollama import ChatOllama


class CodeGraphRAGPipeline:
    """Advanced Retrieval & Code Generation Engine integrating Dual-Brain storage with Gemma 4 30b."""

    def __init__(self, 
                 neo4j_uri: str = "bolt://localhost:7687", 
                 neo4j_auth: tuple = ("neo4j", "test123456"),
                 chroma_host: str = "localhost", 
                 chroma_port: int = 8000,
                 model_name: str = "gemma4:31b-cloud"):
        
        self.neo4j_mgr = Neo4jGraphManager(uri=neo4j_uri, auth=neo4j_auth)
        self.chroma_mgr = ChromaManager(host=chroma_host, port=chroma_port)
        self.collection = self.chroma_mgr.get_or_create_collection("code_semantic_chunks")
        self.llm = ChatOllama(model=model_name, temperature=0.1)

    def search_vector(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """1. Vector Search: Finds semantic code blocks in ChromaDB."""
        res = self.collection.query(query_texts=[query], n_results=n_results)
        
        chunks = []
        if res and res.get("documents"):
            docs = res["documents"][0]
            metas = res["metadatas"][0] if res.get("metadatas") else [{}] * len(docs)
            for d, m in zip(docs, metas):
                chunks.append({"code": d, "metadata": m})
        return chunks

    def traverse_graph_blast_radius(self, function_or_file: str) -> Dict[str, Any]:
        """2. Graph Traversal: Pulls structural locations, line numbers, callers, and blast radius from Neo4j."""
        graph_info = {
            "target": function_or_file,
            "locations": [],
            "callers": [],
            "callees": [],
            "blast_radius_files": []
        }

        with self.neo4j_mgr.driver.session() as session:
            # Query 1: Location & Line numbers
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

            # Query 2: Inbound Callers (Who calls this function?)
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

            # Query 3: Outbound Callees (What functions does this call?)
            callee_res = session.run("""
                MATCH (fn:Function)-[r:CALLS]->(callee:Function)
                WHERE fn.name CONTAINS $target OR fn.id CONTAINS $target
                RETURN callee.name AS callee_name, r.line AS line
            """, target=function_or_file)
            for r in callee_res:
                graph_info["callees"].append({
                    "callee_name": r["callee_name"],
                    "line": r["line"]
                })

            # Query 4: Blast Radius Traversal (Connected files/nodes within 2 hops)
            blast_res = session.run("""
                MATCH (start {name: $target})-[*1..2]-(connected)
                RETURN DISTINCT labels(connected)[0] AS node_type, connected.name AS name, connected.path AS path
            """, target=function_or_file)
            for r in blast_res:
                node_name = r["name"] or r["path"] or "Unknown"
                graph_info["blast_radius_files"].append(f"{r['node_type']}: {node_name}")

        return graph_info

    def query_code_graph_rag(self, user_prompt: str) -> Dict[str, Any]:
        """Full Code-GraphRAG Query Pipeline: Vector -> Graph Blast Radius -> Gemma 4 30b Generation."""
        print(f"\n[CodeGraphRAG] Processing query: '{user_prompt}'")

        # Step 1: Semantic Vector Search
        vector_chunks = self.search_vector(user_prompt, n_results=3)
        print(f"[CodeGraphRAG] Vector Search retrieved {len(vector_chunks)} code blocks.")

        # Identify key target keyword for Graph Traversal
        target_keyword = ""
        if vector_chunks and vector_chunks[0]["metadata"].get("name"):
            target_keyword = vector_chunks[0]["metadata"]["name"]
        else:
            # Fallback search words
            words = [w for w in user_prompt.split() if len(w) > 3 and w.lower() not in ["update", "file", "function", "code"]]
            target_keyword = words[0] if words else "login"

        # Step 2: Graph Traversal & Blast Radius Analysis
        graph_context = self.traverse_graph_blast_radius(target_keyword)
        print(f"[CodeGraphRAG] Graph Traversal retrieved {len(graph_context['locations'])} locations and {len(graph_context['callers'])} callers.")

        # Step 3: Package Context into Structured Prompt for Gemma 4 30b
        formatted_prompt = f"""You are an elite Software Architect using a Code-GraphRAG System.
User Query: "{user_prompt}"

=== 1. RETRIEVED SEMANTIC CODE BLOCKS (Vector DB) ===
{json.dumps([c['code'] for c in vector_chunks], indent=2)}

=== 2. ONTOLOGICAL KNOWLEDGE GRAPH (Neo4j DB) ===
Target Component: {graph_context['target']}
Exact Locations & Line Spans: {json.dumps(graph_context['locations'], indent=2)}
Inbound Callers: {json.dumps(graph_context['callers'], indent=2)}
Outbound Calls: {json.dumps(graph_context['callees'], indent=2)}
Blast Radius (Potentially Impacted Systems): {json.dumps(graph_context['blast_radius_files'], indent=2)}

=== INSTRUCTIONS ===
Formulate a professional, architect-level response detailing:
1. **Target File & Exact Line Numbers**: Which file to open and exact line numbers to edit.
2. **Code Rewrite**: The exact code replace block.
3. **Blast Radius Impact Warning**: Explicitly state which caller functions, connected files, or modules will be impacted if this change is made.

Format your response cleanly with Markdown.
"""

        # Step 4: Model Inference
        response = self.llm.invoke(formatted_prompt)

        return {
            "user_prompt": user_prompt,
            "vector_chunks": vector_chunks,
            "graph_context": graph_context,
            "answer": response.content
        }

    def close(self):
        self.neo4j_mgr.close()

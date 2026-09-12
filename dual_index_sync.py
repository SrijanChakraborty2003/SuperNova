import os
import re
import json
from typing import List, Dict, Any, Optional
from okf_extractor import RepoManager, ASTParser, OKFExtractor, Neo4jGraphManager, ChromaManager
from keyword_index import BM25KeywordIndex


def create_sliding_window_chunks(
    cues: List[Dict[str, Any]],
    video_metadata: Dict[str, Any],
    chunk_seconds: float = 60.0,
    overlap_seconds: float = 5.0
) -> List[Dict[str, Any]]:
    """
    Groups SRT cues into sliding window chunks.
    Default: chunk_seconds = 60.0, overlap_seconds = 5.0 (step_seconds = 55.0).
    """
    if not cues:
        return []

    title = video_metadata.get("title", "Untitled Video")
    video_url = video_metadata.get("url", "")

    step_seconds = chunk_seconds - overlap_seconds
    if step_seconds <= 0:
        raise ValueError("chunk_seconds must be greater than overlap_seconds")

    max_end_time = max(cue["end"] for cue in cues)
    chunks = []
    chunk_index = 0

    win_start = 0.0
    while win_start < max_end_time:
        win_end = win_start + chunk_seconds
        matching_cues = [
            cue for cue in cues
            if cue["end"] > win_start and cue["start"] < win_end
        ]
        if matching_cues:
            chunk_text = " ".join([c["text"] for c in matching_cues])
            chunk_index += 1
            chunks.append({
                "id": f"{title}_chunk_{chunk_index}",
                "text": chunk_text,
                "metadata": {
                    "title": title,
                    "url": video_url,
                    "start": win_start,
                    "end": win_end
                }
            })
        win_start += step_seconds

    return chunks


class SemanticChunker:
    """Uses ASTParser metadata to chunk source files into semantic function/class/file blocks."""

    def __init__(self, ast_parser: ASTParser):
        self.ast_parser = ast_parser

    def chunk_file(self, file_path: str, repo_root: str, repo_name: str = "", chat_id: str = "") -> List[Dict[str, Any]]:
        rel_path = os.path.relpath(file_path, repo_root).replace("\\", "/")
        file_meta = self.ast_parser.parse_file(file_path, repo_root)

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                code_lines = f.readlines()
        except Exception:
            return []

        chunks = []

        # 1. Function Chunks
        for fn in file_meta.get("functions", []):
            s_line = max(1, fn["line_start"])
            e_line = min(len(code_lines), fn["line_end"])
            snippet = "".join(code_lines[s_line - 1:e_line])
            if snippet.strip():
                chunk_id = f"{rel_path}:fn:{fn['name']}:{s_line}"
                chunks.append({
                    "id": chunk_id,
                    "text": f"# Repository: {repo_name}\n# File: {rel_path} (Lines {s_line}-{e_line})\n# Function: {fn['name']}\n{snippet}",
                    "metadata": {
                        "file_path": rel_path,
                        "repo_name": repo_name,
                        "chat_id": chat_id,
                        "type": "function",
                        "name": fn["name"],
                        "line_start": s_line,
                        "line_end": e_line
                    }
                })

        # 2. Class Chunks
        for cls in file_meta.get("classes", []):
            s_line = max(1, cls["line_start"])
            e_line = min(len(code_lines), cls["line_end"])
            snippet = "".join(code_lines[s_line - 1:e_line])
            if snippet.strip():
                chunk_id = f"{rel_path}:class:{cls['name']}:{s_line}"
                chunks.append({
                    "id": chunk_id,
                    "text": f"# Repository: {repo_name}\n# File: {rel_path} (Lines {s_line}-{e_line})\n# Class: {cls['name']}\n{snippet}",
                    "metadata": {
                        "file_path": rel_path,
                        "repo_name": repo_name,
                        "chat_id": chat_id,
                        "type": "class",
                        "name": cls["name"],
                        "line_start": s_line,
                        "line_end": e_line
                    }
                })

        # 3. Whole-file / Line Window Fallback Chunking (if file has no functions/classes)
        if not chunks:
            chunk_size = 60
            for i in range(0, max(1, len(code_lines)), chunk_size):
                sub_lines = code_lines[i:i + chunk_size]
                snippet = "".join(sub_lines)
                if snippet.strip():
                    s_line = i + 1
                    e_line = min(len(code_lines), i + chunk_size)
                    chunk_id = f"{rel_path}:file_chunk:{s_line}"
                    chunks.append({
                        "id": chunk_id,
                        "text": f"# Repository: {repo_name}\n# File: {rel_path} (Lines {s_line}-{e_line})\n{snippet}",
                        "metadata": {
                            "file_path": rel_path,
                            "repo_name": repo_name,
                            "chat_id": chat_id,
                            "type": "file_chunk",
                            "name": os.path.basename(rel_path),
                            "line_start": s_line,
                            "line_end": e_line
                        }
                    })

        return chunks


class DualIndexSync:
    """Coordinates simultaneous graph (Neo4j / OKF), vector (ChromaDB + BGE), and keyword (BM25) indexing & purging."""

    def __init__(self, 
                 neo4j_uri: str = "bolt://localhost:7687", 
                 neo4j_auth: tuple = ("neo4j", "test123456"),
                 chroma_host: str = "localhost", 
                 chroma_port: int = 8000,
                 model_name: str = "gemma4:31b-cloud",
                 ollama_url: str = "http://localhost:11434"):
        
        self.neo4j_mgr = Neo4jGraphManager(uri=neo4j_uri, auth=neo4j_auth)
        self.neo4j_mgr.setup_schema()
        
        self.chroma_mgr = ChromaManager(host=chroma_host, port=chroma_port)
        self.collection = self.chroma_mgr.get_or_create_collection("code_semantic_chunks")
        
        self.keyword_index = BM25KeywordIndex()
        self.ast_parser = ASTParser()
        self.chunker = SemanticChunker(self.ast_parser)
        self.okf_extractor = OKFExtractor(model_name=model_name, ollama_url=ollama_url)

    def get_collection_for_chat(self, chat_id: Optional[str] = None):
        """Returns isolated ChromaDB collection dedicated to a specific chat session ID."""
        if chat_id:
            safe_name = f"chat_{re.sub(r'[^a-zA-Z0-9_-]', '_', chat_id)}"
            return self.chroma_mgr.get_or_create_collection(safe_name)
        return self.collection

    def purge_file(self, rel_path: str, chat_id: Optional[str] = None):
        """Surgically purges stale graph nodes, vector embeddings, and BM25 keyword entries for a modified file."""
        print(f"[DualIndexSync] Purging stale data for: {rel_path} (chat_id: {chat_id})")
        
        # 1. Purge from Neo4j Graph DB if connected
        if self.neo4j_mgr.is_connected and self.neo4j_mgr.driver:
            try:
                with self.neo4j_mgr.driver.session() as session:
                    session.run("""
                        MATCH (f:File {path: $path})
                        OPTIONAL MATCH (f)-[:DEFINES]->(child)
                        DETACH DELETE child, f
                    """, path=rel_path)
            except Exception as e:
                print(f"[DualIndexSync] Neo4j purge notice for {rel_path}: {e}")

        # 2. Purge from chat-scoped ChromaDB Vector DB
        try:
            target_collection = self.get_collection_for_chat(chat_id)
            target_collection.delete(where={"file_path": rel_path})
        except Exception as e:
            print(f"[DualIndexSync] Chroma purge notice for {rel_path}: {e}")

        # 3. Purge from BM25 Keyword Index
        try:
            self.keyword_index.purge_file(rel_path, session_id=chat_id or "default_session")
        except Exception as e:
            print(f"[DualIndexSync] BM25 purge notice for {rel_path}: {e}")

    def sync_file(self, file_path: str, repo_root: str, repo_name: str, chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Surgically updates a single modified source file across Neo4j/OKF, ChromaDB, and BM25 Keyword Index."""
        rel_path = os.path.relpath(file_path, repo_root).replace("\\", "/")
        target_collection = self.get_collection_for_chat(chat_id)

        # Step 1: Purge stale state
        self.purge_file(rel_path, chat_id=chat_id)

        # Step 2: Parse AST & extract metadata
        file_meta = self.ast_parser.parse_file(file_path, repo_root)

        # Step 3: Chunk & upsert into ChromaDB and BM25 Keyword Index
        chunks = self.chunker.chunk_file(file_path, repo_root, repo_name=repo_name, chat_id=chat_id or "")
        if chunks:
            ids = [c["id"] for c in chunks]
            documents = [c["text"] for c in chunks]
            metadatas = [c["metadata"] for c in chunks]
            target_collection.add(ids=ids, documents=documents, metadatas=metadatas)

            # BM25 Keyword Indexing
            self.keyword_index.add_chunks(chunks, session_id=chat_id or "default_session")

        # Step 4: Extract OKF Triples & upsert into Graph
        triples = self.okf_extractor.extract_okf_triples(file_meta)
        self.neo4j_mgr.ingest_okf_graph(
            repo_name=repo_name,
            file_metas=[file_meta],
            llm_triples=triples
        )

        print(f"[DualIndexSync] Successfully synced '{rel_path}': {len(chunks)} vector/keyword chunks, {len(triples)} OKF triples.")
        return {"file": rel_path, "chunks": len(chunks), "triples": len(triples)}

    def sync_repository(self, repo_target: str, chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Performs initial or full dual-brain synchronization for an entire repository."""
        repo_mgr = RepoManager(repo_target=repo_target)
        repo_root = repo_mgr.prepare_repo()
        source_files = repo_mgr.get_source_files()

        print(f"[DualIndexSync] Starting full sync for repo '{repo_mgr.repo_name}' (chat_id: {chat_id}, {len(source_files)} files)...")

        total_chunks = 0
        total_triples = 0

        for sf in source_files:
            res = self.sync_file(file_path=sf, repo_root=repo_root, repo_name=repo_mgr.repo_name, chat_id=chat_id)
            total_chunks += res["chunks"]
            total_triples += res["triples"]

        stats = self.neo4j_mgr.get_summary_stats()
        kw_count = self.keyword_index.count(session_id=chat_id or "default_session")

        print(f"[DualIndexSync] Full Repository Sync Complete for '{repo_mgr.repo_name}'!")
        return {
            "repository": repo_mgr.repo_name,
            "processed_files": len(source_files),
            "total_vector_chunks": total_chunks,
            "total_keyword_chunks": kw_count,
            "graph_stats": stats
        }

    def close(self):
        self.neo4j_mgr.close()


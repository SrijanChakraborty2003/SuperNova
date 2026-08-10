import os
import json
from typing import List, Dict, Any, Optional
from okf_extractor import RepoManager, ASTParser, OKFExtractor, Neo4jGraphManager, ChromaManager


class SemanticChunker:
    """Chunks source code by AST logical blocks (functions, classes) with exact line number metadata."""

    def __init__(self, ast_parser: ASTParser):
        self.parser = ast_parser

    def chunk_file(self, file_path: str, repo_root: str) -> List[Dict[str, Any]]:
        rel_path = os.path.relpath(file_path, repo_root).replace("\\", "/")
        file_meta = self.parser.parse_file(file_path, repo_root)

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        chunks = []

        # 1. Chunk Functions
        for fn in file_meta["functions"]:
            s_line = max(1, fn["line_start"])
            e_line = min(len(lines), fn["line_end"])
            chunk_code = "".join(lines[s_line - 1:e_line])
            chunk_id = f"{rel_path}:func:{fn['name']}:{s_line}"

            chunks.append({
                "id": chunk_id,
                "text": f"# File: {rel_path} (Lines {s_line}-{e_line})\n# Function: {fn['name']}\n{chunk_code}",
                "metadata": {
                    "file_path": rel_path,
                    "chunk_type": "function",
                    "name": fn["name"],
                    "line_start": s_line,
                    "line_end": e_line,
                    "language": file_meta["language"]
                }
            })

        # 2. Chunk Classes
        for cls in file_meta["classes"]:
            s_line = max(1, cls["line_start"])
            e_line = min(len(lines), cls["line_end"])
            chunk_code = "".join(lines[s_line - 1:e_line])
            chunk_id = f"{rel_path}:class:{cls['name']}:{s_line}"

            chunks.append({
                "id": chunk_id,
                "text": f"# File: {rel_path} (Lines {s_line}-{e_line})\n# Class: {cls['name']}\n{chunk_code}",
                "metadata": {
                    "file_path": rel_path,
                    "chunk_type": "class",
                    "name": cls["name"],
                    "line_start": s_line,
                    "line_end": e_line,
                    "language": file_meta["language"]
                }
            })

        # 3. Fallback Header Chunk if file has no functions/classes
        if not chunks and lines:
            chunks.append({
                "id": f"{rel_path}:file_header:1",
                "text": f"# File: {rel_path}\n" + "".join(lines[:50]),
                "metadata": {
                    "file_path": rel_path,
                    "chunk_type": "file_header",
                    "name": os.path.basename(rel_path),
                    "line_start": 1,
                    "line_end": min(50, len(lines)),
                    "language": file_meta["language"]
                }
            })

        return chunks


class DualIndexSync:
    """Coordinates simultaneous graph (Neo4j) and vector (ChromaDB) indexing & purging."""

    def __init__(self, 
                 neo4j_uri: str = "bolt://localhost:7687", 
                 neo4j_auth: tuple = ("neo4j", "test123456"),
                 chroma_host: str = "localhost", 
                 chroma_port: int = 8000,
                 model_name: str = "gemma4:31b-cloud"):
        
        self.neo4j_mgr = Neo4jGraphManager(uri=neo4j_uri, auth=neo4j_auth)
        self.neo4j_mgr.setup_schema()
        
        self.chroma_mgr = ChromaManager(host=chroma_host, port=chroma_port)
        self.collection = self.chroma_mgr.get_or_create_collection("code_semantic_chunks")
        
        self.ast_parser = ASTParser()
        self.chunker = SemanticChunker(self.ast_parser)
        self.okf_extractor = OKFExtractor(model_name=model_name)

    def purge_file(self, rel_path: str):
        """Surgically purges stale graph nodes and vector embeddings for a modified file."""
        print(f"[DualIndexSync] Purging stale data for: {rel_path}")
        
        # 1. Purge from Neo4j Graph DB
        with self.neo4j_mgr.driver.session() as session:
            session.run("""
                MATCH (f:File {path: $path})
                OPTIONAL MATCH (f)-[:DEFINES]->(child)
                DETACH DELETE child, f
            """, path=rel_path)

        # 2. Purge from ChromaDB Vector DB
        try:
            self.collection.delete(where={"file_path": rel_path})
        except Exception as e:
            print(f"[DualIndexSync] Chroma purge warning for {rel_path}: {e}")

    def sync_file(self, file_path: str, repo_root: str, repo_name: str) -> Dict[str, Any]:
        """Surgically updates a single modified source file across Neo4j and ChromaDB."""
        rel_path = os.path.relpath(file_path, repo_root).replace("\\", "/")
        
        # Step 1: Purge stale state
        self.purge_file(rel_path)

        # Step 2: Parse AST & extract metadata
        file_meta = self.ast_parser.parse_file(file_path, repo_root)

        # Step 3: Chunk & upsert into ChromaDB
        chunks = self.chunker.chunk_file(file_path, repo_root)
        if chunks:
            ids = [c["id"] for c in chunks]
            documents = [c["text"] for c in chunks]
            metadatas = [c["metadata"] for c in chunks]
            self.collection.add(ids=ids, documents=documents, metadatas=metadatas)

        # Step 4: Extract OKF Triples & upsert into Neo4j
        triples = self.okf_extractor.extract_okf_triples(file_meta)
        self.neo4j_mgr.ingest_okf_graph(
            repo_name=repo_name,
            file_metas=[file_meta],
            llm_triples=triples
        )

        print(f"[DualIndexSync] Successfully synced {rel_path}: {len(chunks)} vector chunks, {len(triples)} OKF triples.")
        return {"file": rel_path, "chunks": len(chunks), "triples": len(triples)}

    def sync_repository(self, repo_target: str) -> Dict[str, Any]:
        """Performs initial or full dual-brain synchronization for an entire repository."""
        repo_mgr = RepoManager(repo_target=repo_target)
        repo_root = repo_mgr.prepare_repo()
        source_files = repo_mgr.get_source_files()

        print(f"[DualIndexSync] Starting full sync for repository '{repo_mgr.repo_name}' ({len(source_files)} files)...")

        total_chunks = 0
        total_triples = 0

        for sf in source_files:
            res = self.sync_file(file_path=sf, repo_root=repo_root, repo_name=repo_mgr.repo_name)
            total_chunks += res["chunks"]
            total_triples += res["triples"]

        stats = self.neo4j_mgr.get_summary_stats()
        print(f"[DualIndexSync] Full Repository Sync Complete for '{repo_mgr.repo_name}'!")
        return {
            "repository": repo_mgr.repo_name,
            "processed_files": len(source_files),
            "total_vector_chunks": total_chunks,
            "graph_stats": stats
        }

    def close(self):
        self.neo4j_mgr.close()

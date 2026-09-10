import os
import json
from typing import List, Dict, Any, Optional
from okf_extractor import RepoManager, ASTParser, OKFExtractor, Neo4jGraphManager, ChromaManager


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
    video_id = extract_video_id(video_url) or "video"

    step_seconds = chunk_seconds - overlap_seconds
    if step_seconds <= 0:
        raise ValueError("chunk_seconds must be greater than overlap_seconds")

    max_end_time = max(cue["end"] for cue in cues)
    chunks = []
    chunk_index = 0

    win_start = 0.0
    while win_start < max_end_time:
        win_end = win_start + chunk_seconds

        # Select cues overlapping with current window
        matching_cues = [
            cue for cue in cues
            if cue["end"] > win_start and cue["start"] < win_end
        ]
        # ... (rest of the logic for building chunk_obj)


class DualIndexSync:
    """Coordinates simultaneous graph (Neo4j / OKF) and vector (ChromaDB + BGE-small-v1.5) indexing & purging."""

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
        
        self.ast_parser = ASTParser()
        self.chunker = SemanticChunker(self.ast_parser)
        self.okf_extractor = OKFExtractor(model_name=model_name, ollama_url=ollama_url)

    def purge_file(self, rel_path: str):
        """Surgically purges stale graph nodes and vector embeddings for a modified file."""
        print(f"[DualIndexSync] Purging stale data for: {rel_path}")
        
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

        # 2. Purge from ChromaDB Vector DB
        try:
            self.collection.delete(where={"file_path": rel_path})
        except Exception as e:
            print(f"[DualIndexSync] Chroma purge notice for {rel_path}: {e}")

    def sync_file(self, file_path: str, repo_root: str, repo_name: str) -> Dict[str, Any]:
        """Surgically updates a single modified source file across Neo4j/OKF and ChromaDB."""
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

        # Step 4: Extract OKF Triples & upsert into Graph
        triples = self.okf_extractor.extract_okf_triples(file_meta)
        self.neo4j_mgr.ingest_okf_graph(
            repo_name=repo_name,
            file_metas=[file_meta],
            llm_triples=triples
        )

        print(f"[DualIndexSync] Successfully synced '{rel_path}': {len(chunks)} vector chunks, {len(triples)} OKF triples.")
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

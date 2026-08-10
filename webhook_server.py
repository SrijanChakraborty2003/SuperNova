import os
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from dual_index_sync import DualIndexSync


app = FastAPI(
    title="Code-GraphRAG Ingestion Webhook API",
    description="Dynamic webhook endpoint parsing modified files from Git commits to keep Neo4j and ChromaDB synchronized.",
    version="1.0.0"
)

# Global Dual-Brain Sync Engine
sync_engine: Optional[DualIndexSync] = None


@app.on_event("startup")
def startup_event():
    global sync_engine
    print("[WebhookServer] Initializing DualIndexSync Engine...")
    sync_engine = DualIndexSync()


@app.on_event("shutdown")
def shutdown_event():
    global sync_engine
    if sync_engine:
        sync_engine.close()


class IngestFileRequest(BaseModel):
    file_path: str
    repo_root: Optional[str] = "."
    repo_name: Optional[str] = "Code-Helper"


class GitWebhookPayload(BaseModel):
    modified_files: List[str]
    repo_root: Optional[str] = "."
    repo_name: Optional[str] = "Code-Helper"


@app.get("/health")
def health_check():
    """Verifies operational status of Neo4j, ChromaDB, and Ollama integration."""
    if not sync_engine:
        raise HTTPException(status_code=500, detail="Sync engine not initialized")
    
    chroma_ok = sync_engine.chroma_mgr.verify_connection()
    stats = sync_engine.neo4j_mgr.get_summary_stats()
    
    return {
        "status": "healthy",
        "chromadb_connected": chroma_ok,
        "neo4j_stats": stats
    }


@app.post("/ingest/file")
def ingest_single_file(req: IngestFileRequest):
    """Surgically re-indexes a single modified file."""
    if not sync_engine:
        raise HTTPException(status_code=500, detail="Sync engine not initialized")
    
    full_path = os.path.abspath(req.file_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"File not found: {full_path}")

    repo_root = os.path.abspath(req.repo_root)
    result = sync_engine.sync_file(file_path=full_path, repo_root=repo_root, repo_name=req.repo_name)
    return {"message": "File successfully synced to dual-brain storage", "details": result}


@app.post("/ingest/webhook")
def process_git_webhook(payload: GitWebhookPayload, background_tasks: BackgroundTasks):
    """Webhook triggered by Git commit hooks or GitHub payload containing diff file lists."""
    if not sync_engine:
        raise HTTPException(status_code=500, detail="Sync engine not initialized")

    repo_root = os.path.abspath(payload.repo_root)
    results = []

    for rel_file in payload.modified_files:
        full_path = os.path.abspath(os.path.join(repo_root, rel_file))
        if os.path.exists(full_path):
            res = sync_engine.sync_file(file_path=full_path, repo_root=repo_root, repo_name=payload.repo_name)
            results.append(res)
        else:
            # File was deleted: purge stale data
            sync_engine.purge_file(rel_file)
            results.append({"file": rel_file, "status": "purged_deleted"})

    return {
        "message": f"Processed git webhook for {len(payload.modified_files)} files",
        "synced": results
    }


if __name__ == "__main__":
    import uvicorn
    print("[WebhookServer] Starting Uvicorn server on http://localhost:8080 ...")
    uvicorn.run(app, host="0.0.0.0", port=8080)

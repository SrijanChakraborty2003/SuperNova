import sys
import os
import argparse

# Force UTF-8 output encoding for Windows PowerShell/CMD console compatibility
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from dual_index_sync import DualIndexSync
from graph_rag_pipeline import CodeGraphRAGPipeline


def main():
    parser = argparse.ArgumentParser(description="SuperNova Code-RAG Web & CLI Server (Tree-Sitter + ChromaDB BGE + OKF + GPT-OSS 120B)")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command 1: Sync Repository (Remote Git URL or Local Path)
    sync_parser = subparsers.add_parser("sync", help="Synchronize a repository into ChromaDB (BGE-small-v1.5) and OKF Graph")
    sync_parser.add_argument("--repo", type=str, default=".", help="Git repository URL or local repository path")
    sync_parser.add_argument("--model", type=str, default="gemma4:31b-cloud", help="LLM model name")

    # Command 2: Query Code-RAG
    query_parser = subparsers.add_parser("query", help="Query Code-RAG for explanations, line numbers, and code rewrites")
    query_parser.add_argument("prompt", type=str, help="User prompt e.g. 'Update login function to use JWT'")
    query_parser.add_argument("--repo", type=str, default=".", help="Repository path to auto-sync if index is empty")
    query_parser.add_argument("--model", type=str, default="gemma4:31b-cloud", help="LLM model name")

    # Command 3: Surgical Update File Re-indexing
    update_parser = subparsers.add_parser("update", help="Surgically re-index a modified file in ChromaDB and OKF graph")
    update_parser.add_argument("--file", type=str, required=True, help="Relative or full path of file modified")
    update_parser.add_argument("--repo", type=str, default=".", help="Repository root path")

    # Command 4: Webhook Server
    webhook_parser = subparsers.add_parser("webhook", help="Launch FastAPI dynamic ingestion webhook server")
    webhook_parser.add_argument("--port", type=int, default=8080, help="Port to run Uvicorn server on")

    args = parser.parse_args()

    if args.command == "sync":
        print(f"=== Starting Repository Ingestion & Dual-Brain Sync for: {args.repo} ===")
        sync = DualIndexSync(model_name=getattr(args, 'model', 'gpt-oss:120b'))
        res = sync.sync_repository(args.repo)
        print("\nSync Summary:", res)
        sync.close()

    elif args.command == "query":
        pipeline = CodeGraphRAGPipeline(model_name=getattr(args, 'model', 'gpt-oss:120b'))
        if pipeline.collection.count() == 0:
            print("[main] Vector database is empty. Triggering initial repository ingestion & indexing...")
            sync = DualIndexSync(model_name=getattr(args, 'model', 'gpt-oss:120b'))
            sync.sync_repository(args.repo)
            sync.close()
            pipeline = CodeGraphRAGPipeline(model_name=getattr(args, 'model', 'gpt-oss:120b'))

        print(f"=== Executing Code-RAG Pipeline (Query: '{args.prompt}') ===")
        res = pipeline.query_code_graph_rag(args.prompt)
        print("\n==========================================")
        print("CODE-RAG RESPONSE & REWRITE PROPOSAL:")
        print("==========================================")
        print(res["answer"])
        pipeline.close()

    elif args.command == "update":
        repo_root = os.path.abspath(args.repo)
        full_file_path = os.path.abspath(args.file) if not os.path.isabs(args.file) else args.file
        repo_name = os.path.basename(repo_root.rstrip("/\\"))

        print(f"=== Surgically Re-indexing Modified File: {full_file_path} ===")
        sync = DualIndexSync()
        res = sync.sync_file(file_path=full_file_path, repo_root=repo_root, repo_name=repo_name)
        print("Update Result:", res)
        sync.close()

    elif args.command == "webhook":
        import uvicorn
        from webhook_server import app as webhook_app
        print(f"=== Launching Webhook API Server on http://localhost:{args.port} ===")
        uvicorn.run(webhook_app, host="0.0.0.0", port=args.port)

    else:
        # Default behavior: Launch Flask Web Dashboard on Port 5000
        from app import app
        print("\n" + "="*70)
        print(" 🚀 Launching SuperNova Code-RAG Web Interface on http://localhost:5000")
        print("="*70 + "\n")
        app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()

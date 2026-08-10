import sys
import os
import argparse

# Force UTF-8 output encoding for Windows PowerShell/CMD console compatibility
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from dual_index_sync import DualIndexSync
from graph_rag_pipeline import CodeGraphRAGPipeline


def main():
    parser = argparse.ArgumentParser(description="Code-GraphRAG System CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command 1: Sync Repository
    sync_parser = subparsers.add_parser("sync", help="Synchronize a repository into Dual-Brain storage (Neo4j + ChromaDB)")
    sync_parser.add_argument("--repo", type=str, default=".", help="Git repo URL or local repository path")

    # Command 2: Query Code-GraphRAG
    query_parser = subparsers.add_parser("query", help="Query the Code-GraphRAG system for code rewrites & blast radius analysis")
    query_parser.add_argument("prompt", type=str, help="User prompt e.g. 'Update login function to use JWT'")
    query_parser.add_argument("--repo", type=str, default=".", help="Repository path to auto-sync if index is empty")

    # Command 3: Launch Webhook Server
    server_parser = subparsers.add_parser("server", help="Launch FastAPI dynamic ingestion webhook server")
    server_parser.add_argument("--port", type=int, default=8080, help="Port to run Uvicorn server on")

    args = parser.parse_args()

    if args.command == "sync":
        print(f"=== Starting Dual-Brain Sync for target: {args.repo} ===")
        sync = DualIndexSync()
        res = sync.sync_repository(args.repo)
        print("Sync Summary:", res)
        sync.close()

    elif args.command == "query":
        # Check if vector index is populated, auto-sync if empty
        pipeline = CodeGraphRAGPipeline()
        if pipeline.collection.count() == 0:
            print("[main] ChromaDB vector store is empty. Performing initial Dual-Brain Sync...")
            sync = DualIndexSync()
            sync.sync_repository(args.repo)
            sync.close()
            # Refresh collection reference
            pipeline = CodeGraphRAGPipeline()

        print(f"=== Executing Code-GraphRAG Pipeline ===")
        res = pipeline.query_code_graph_rag(args.prompt)
        print("\n==========================================")
        print("ANSWER & CODE REWRITE OUTPUT:")
        print("==========================================")
        print(res["answer"])
        pipeline.close()

    elif args.command == "server":
        import uvicorn
        from webhook_server import app
        print(f"=== Launching Webhook API Server on http://localhost:{args.port} ===")
        uvicorn.run(app, host="0.0.0.0", port=args.port)

    else:
        # Default run if no subcommand specified
        print("No command specified. Usage examples:")
        print("  python main.py sync --repo .")
        print("  python main.py query \"Update login function to use JWT\"")
        print("  python main.py server")


if __name__ == "__main__":
    main()

import os
import re
import json
import time
from typing import List, Dict, Any, Optional
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from dual_index_sync import DualIndexSync
from okf_extractor import RepoManager
from graph_rag_pipeline import CodeGraphRAGPipeline

app = Flask(__name__, template_folder="templates", static_folder="static")

# Shared instances
sync_engine = None
pipeline_engine = None


def get_sync_engine():
    global sync_engine
    if sync_engine is None:
        sync_engine = DualIndexSync()
    return sync_engine


def get_pipeline_engine():
    global pipeline_engine
    if pipeline_engine is None:
        pipeline_engine = CodeGraphRAGPipeline(model_name="gemma4:31b-cloud")
    return pipeline_engine


@app.route("/")
def index():
    """Serves the main SuperNova Code-RAG web dashboard."""
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_status():
    """Returns database connection status and chunk stats."""
    try:
        pipeline = get_pipeline_engine()
        count = pipeline.collection.count()
        stats = pipeline.neo4j_mgr.get_summary_stats()
        return jsonify({
            "status": "online",
            "vector_chunks_count": count,
            "graph_stats": stats
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/stream-sync", methods=["GET"])
def stream_sync():
    """Server-Sent Events (SSE) endpoint streaming real-time repository ingestion & indexing progress."""
    repo_target = request.args.get("repo", ".").strip()

    def generate():
        try:
            yield f"data: {json.dumps({'status': 'starting', 'progress': 5, 'message': f'Initializing ingestion for target: {repo_target}'})}\n\n"
            time.sleep(0.3)

            # Step 1: Clone / Prepare Repo
            yield f"data: {json.dumps({'status': 'cloning', 'progress': 15, 'message': 'Cloning Git repository or preparing local path...'})}\n\n"
            repo_mgr = RepoManager(repo_target=repo_target)
            repo_root = repo_mgr.prepare_repo()
            source_files = repo_mgr.get_source_files()

            total_files = len(source_files)
            yield f"data: {json.dumps({'status': 'discovered', 'progress': 30, 'message': f'Discovered {total_files} source files for AST parsing.'})}\n\n"
            time.sleep(0.3)

            # Step 2: Indexing Files
            sync = get_sync_engine()
            total_chunks = 0
            total_triples = 0

            for idx, sf in enumerate(source_files, start=1):
                rel_file = os.path.relpath(sf, repo_root).replace("\\", "/")
                prog = 30 + int((idx / max(1, total_files)) * 60)
                yield f"data: {json.dumps({'status': 'indexing', 'progress': prog, 'message': f'Parsing AST & embedding [{idx}/{total_files}]: {rel_file}'})}\n\n"
                
                res = sync.sync_file(file_path=sf, repo_root=repo_root, repo_name=repo_mgr.repo_name)
                total_chunks += res.get("chunks", 0)
                total_triples += res.get("triples", 0)

            stats = sync.neo4j_mgr.get_summary_stats()
            msg_text = f"Successfully ingested repository '{repo_mgr.repo_name}'!"
            yield f"data: {json.dumps({'status': 'complete', 'progress': 100, 'message': msg_text, 'summary': {'processed_files': total_files, 'total_vector_chunks': total_chunks, 'graph_stats': stats}})}\n\n"
        
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'progress': 0, 'message': f'Error during sync: {str(e)}'})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/query", methods=["POST"])
def query_rag():
    """Handles Code-RAG chat queries with Redis log-based context buffer."""
    data = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    session_id = data.get("session_id", "default_session").strip()
    if not prompt:
        return jsonify({"error": "Prompt cannot be empty"}), 400

    try:
        pipeline = get_pipeline_engine()
        # If DB is empty, auto sync current directory
        if pipeline.collection.count() == 0:
            sync = get_sync_engine()
            sync.sync_repository(".")

        res = pipeline.query_code_graph_rag(prompt, session_id=session_id, max_history=8)
        answer_text = res.get("answer", "")

        # Detect code update proposals in answer text
        proposal = parse_code_update_proposal(answer_text, res.get("vector_chunks", []))

        return jsonify({
            "user_prompt": prompt,
            "session_id": session_id,
            "answer": answer_text,
            "vector_chunks": res.get("vector_chunks", []),
            "graph_context": res.get("graph_context", {}),
            "chat_history": res.get("chat_history", []),
            "code_update_proposal": proposal
        })

    except Exception as e:
        return jsonify({"error": f"Failed to execute query: {str(e)}"}), 500


@app.route("/api/chat-history", methods=["GET", "DELETE"])
def handle_chat_history():
    """Gets or clears recent Redis chat log history (past 8 messages context)."""
    session_id = request.args.get("session_id", "default_session").strip()
    try:
        pipeline = get_pipeline_engine()
        if request.method == "DELETE":
            pipeline.chat_buffer.clear_history(session_id)
            return jsonify({"status": "cleared", "session_id": session_id})
        
        history = pipeline.chat_buffer.get_history(session_id, max_messages=8)
        return jsonify({"session_id": session_id, "chat_history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/apply-update", methods=["POST"])
def apply_update():
    """Applies a proposed code snippet update to a file and surgically re-indexes database."""
    data = request.get_json() or {}
    rel_file_path = data.get("file_path", "").strip()
    line_start = int(data.get("line_start", 1))
    line_end = int(data.get("line_end", 1))
    updated_code = data.get("updated_code", "")

    if not rel_file_path or not updated_code:
        return jsonify({"error": "Missing file_path or updated_code"}), 400

    # Resolve full path
    full_path = os.path.abspath(rel_file_path)
    if not os.path.exists(full_path):
        # Fallback search relative to workspace
        full_path = os.path.abspath(os.path.join(".", rel_file_path))
        if not os.path.exists(full_path):
            return jsonify({"error": f"Target file not found: {rel_file_path}"}), 404

    try:
        # Read existing file content
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total_lines = len(lines)
        s_idx = max(0, line_start - 1)
        e_idx = min(total_lines, line_end)

        # Prepare new code lines
        new_lines = updated_code.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        # Apply patch
        lines[s_idx:e_idx] = new_lines

        # Write updated file to disk
        with open(full_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        # Surgically re-index modified file in ChromaDB & OKF graph
        sync = get_sync_engine()
        repo_root = os.path.abspath(".")
        repo_name = os.path.basename(repo_root.rstrip("/\\"))
        sync_result = sync.sync_file(file_path=full_path, repo_root=repo_root, repo_name=repo_name)

        return jsonify({
            "status": "success",
            "message": f"Successfully updated '{rel_file_path}' (Lines {line_start}-{line_end}) and surgically re-indexed database!",
            "file_path": rel_file_path,
            "surgical_sync": sync_result
        })

    except Exception as e:
        return jsonify({"error": f"Failed to apply code update: {str(e)}"}), 500


def parse_code_update_proposal(answer_text: str, vector_chunks: list) -> Optional[dict]:
    """Parses target file, line bounds, and code block from answer output or vector metadata."""
    try:
        # 1. Regex match for target file and line spans
        file_match = re.search(r'(?:Target File|File Path|file):\s*`?([a-zA-Z0-9_\-/\\]+\.[a-zA-Z0-9]+)`?', answer_text, re.IGNORECASE)
        lines_match = re.search(r'(?:Lines?|Line numbers?):\s*`?(\d+)\s*(?:to|-)\s*(\d+)`?', answer_text, re.IGNORECASE)
        code_match = re.search(r'```(?:python|javascript|typescript|js|ts|json|code)?\s*\n(.*?)```', answer_text, re.DOTALL)

        file_path = file_match.group(1).replace("\\", "/") if file_match else ""
        line_start = int(lines_match.group(1)) if lines_match else 1
        line_end = int(lines_match.group(2)) if lines_match else 1
        code_snippet = code_match.group(1).strip() if code_match else ""
        if code_snippet:
            code_snippet = code_snippet.replace('\\n', '\n').replace('\\t', '    ')

        # Fallback to top vector chunk metadata if regex file_path not found
        if not file_path and vector_chunks and vector_chunks[0].get("metadata"):
            meta = vector_chunks[0]["metadata"]
            file_path = meta.get("file_path", "")
            if not lines_match and meta.get("line_start"):
                line_start = meta.get("line_start", 1)
                line_end = meta.get("line_end", 1)

        if file_path and code_snippet:
            return {
                "file_path": file_path,
                "line_start": line_start,
                "line_end": line_end,
                "updated_code": code_snippet
            }
    except Exception as e:
        print(f"[parse_code_update_proposal] Notice: {e}")

    return None


if __name__ == "__main__":
    print("=== Launching SuperNova Code-RAG Flask Web Server on http://localhost:5000 ===")
    app.run(host="0.0.0.0", port=5000, debug=False)

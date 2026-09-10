import os
import re
import json
import time
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Any, Optional
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from dual_index_sync import DualIndexSync
from okf_extractor import RepoManager
from graph_rag_pipeline import CodeGraphRAGPipeline

app = Flask(__name__, template_folder="templates", static_folder="static")

# Shared instances
sync_engine = None
pipeline_engine = None

# In-memory session & OTP stores
otp_store: Dict[str, Dict[str, Any]] = {}  # email -> {"otp": "123456", "expires_at": timestamp}
chats_store: Dict[str, List[Dict[str, Any]]] = {}  # email -> list of chat sessions


def load_env_config() -> Dict[str, str]:
    """Reads configuration parameters from local .env file."""
    env_path = os.path.abspath(".env")
    config = {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "srijan.chakraborty.office@gmail.com",
        "SMTP_PASS": "jaok bptl pyev hfny",
        "SMTP_FROM": "srijan.chakraborty.office@gmail.com"
    }
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip()
    return config


def send_gmail_otp(to_email: str, otp_code: str) -> bool:
    """Sends 6-digit OTP verification code via Gmail SMTP."""
    cfg = load_env_config()
    smtp_host = cfg.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(cfg.get("SMTP_PORT", 587))
    smtp_user = cfg.get("SMTP_USER", "")
    smtp_pass = cfg.get("SMTP_PASS", "")
    smtp_from = cfg.get("SMTP_FROM", smtp_user)

    msg = MIMEMultipart()
    msg['From'] = f"SuperNova Code-RAG <{smtp_from}>"
    msg['To'] = to_email
    msg['Subject'] = f"Your SuperNova Access Verification Code: {otp_code}"

    body = f"""Hello,

Your 6-digit OTP verification code for SuperNova Code-RAG is:

    🔑 {otp_code}

This code is valid for 10 minutes. Enter this code on the SuperNova login portal to access your repository chats.

Best regards,
SuperNova Code-RAG Team
"""
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, [to_email], msg.as_string())
        server.quit()
        print(f"[SMTP Success] 6-digit OTP sent to {to_email}")
        return True
    except Exception as e:
        print(f"[SMTP Error] Failed to send OTP email to {to_email}: {e}")
        return False


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


@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    """Generates and sends 6-digit OTP via Gmail SMTP."""
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Please enter a valid Gmail / Email address"}), 400

    otp_code = f"{random.randint(100000, 999999)}"
    expires_at = time.time() + 600  # 10 minutes expiry
    otp_store[email] = {"otp": otp_code, "expires_at": expires_at}

    success = send_gmail_otp(email, otp_code)
    if success:
        return jsonify({
            "status": "sent",
            "message": f"6-digit OTP sent successfully to {email}. Check your inbox!"
        })
    else:
        return jsonify({
            "status": "sent_fallback",
            "message": f"OTP email notice: Check server log or use verification code below.",
            "dev_otp": otp_code
        })


@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    """Verifies 6-digit OTP code submitted by user."""
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    user_otp = data.get("otp", "").strip()

    if not email or not user_otp:
        return jsonify({"error": "Email and 6-digit OTP code are required"}), 400

    record = otp_store.get(email)
    if not record:
        return jsonify({"error": "No active OTP request found for this email. Click Send OTP."}), 400

    if time.time() > record["expires_at"]:
        otp_store.pop(email, None)
        return jsonify({"error": "OTP verification code has expired. Please request a new code."}), 400

    if record["otp"] != user_otp:
        return jsonify({"error": "Invalid 6-digit OTP code. Please check your inbox and try again."}), 400

    # Verification successful!
    otp_store.pop(email, None)
    if email not in chats_store:
        chats_store[email] = []

    return jsonify({
        "status": "verified",
        "email": email,
        "message": "Authentication successful! Welcome to SuperNova Code-RAG."
    })


@app.route("/api/chats", methods=["GET", "POST", "DELETE"])
def handle_chats():
    """Manages isolated chat sessions per user email so context never leaks between chats."""
    email = request.headers.get("X-User-Email", "user@supernova.local").strip().lower()
    if email not in chats_store:
        chats_store[email] = []

    if request.method == "GET":
        return jsonify({"email": email, "chats": chats_store[email]})

    elif request.method == "POST":
        data = request.get_json() or {}
        repo_url = data.get("repo_url", "").strip()
        title = data.get("title", "").strip()

        if not title:
            if repo_url:
                repo_basename = repo_url.rstrip("/\\").split("/")[-1].split("\\")[-1].replace(".git", "")
                title = f"Project: {repo_basename}"
            else:
                title = f"New Chat #{len(chats_store[email]) + 1}"

        chat_id = f"chat_{int(time.time()*1000)}"
        new_chat = {
            "chat_id": chat_id,
            "title": title,
            "repo_url": repo_url,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "messages": []
        }
        chats_store[email].insert(0, new_chat)
        return jsonify({"status": "created", "chat": new_chat})

    elif request.method == "DELETE":
        chat_id = request.args.get("chat_id", "").strip()
        if chat_id:
            chats_store[email] = [c for c in chats_store[email] if c["chat_id"] != chat_id]
            pipeline = get_pipeline_engine()
            pipeline.chat_buffer.clear_history(chat_id)
        return jsonify({"status": "deleted", "chat_id": chat_id})


@app.route("/api/chats/<chat_id>", methods=["GET"])
def get_chat_detail(chat_id):
    """Retrieves full conversation history and project scope for a specific chat ID."""
    email = request.headers.get("X-User-Email", "user@supernova.local").strip().lower()
    user_chats = chats_store.get(email, [])
    for c in user_chats:
        if c["chat_id"] == chat_id:
            return jsonify({"status": "success", "chat": c})
    return jsonify({"error": "Chat session not found"}), 404


@app.route("/api/stream-sync", methods=["GET"])
def stream_sync():
    """Server-Sent Events (SSE) endpoint streaming real-time repository ingestion & indexing progress."""
    repo_target = request.args.get("repo", "").strip()
    chat_id = request.args.get("chat_id", "").strip()
    if not repo_target:
        repo_target = "."

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

            # Step 2: Indexing Files into isolated ChromaDB collection for this chat_id
            sync = get_sync_engine()
            total_chunks = 0
            total_triples = 0

            for idx, sf in enumerate(source_files, start=1):
                rel_file = os.path.relpath(sf, repo_root).replace("\\", "/")
                prog = 30 + int((idx / max(1, total_files)) * 60)
                yield f"data: {json.dumps({'status': 'indexing', 'progress': prog, 'message': f'Parsing AST & embedding [{idx}/{total_files}]: {rel_file}'})}\n\n"

                res = sync.sync_file(file_path=sf, repo_root=repo_root, repo_name=repo_mgr.repo_name, chat_id=chat_id)
                total_chunks += res.get("chunks", 0)
                total_triples += res.get("triples", 0)

            # Update chat session metadata in chats_store
            if chat_id:
                for email_key, user_chats in chats_store.items():
                    for c in user_chats:
                        if c["chat_id"] == chat_id:
                            c["repo_url"] = repo_target
                            c["title"] = f"Project: {repo_mgr.repo_name}"
                            break

            stats = sync.neo4j_mgr.get_summary_stats()
            msg_text = f"Successfully ingested repository '{repo_mgr.repo_name}'!"
            yield f"data: {json.dumps({'status': 'complete', 'progress': 100, 'message': msg_text, 'summary': {'processed_files': total_files, 'total_vector_chunks': total_chunks, 'graph_stats': stats}})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'progress': 0, 'message': f'Error during sync: {str(e)}'})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/query", methods=["POST"])
def query_rag():
    """Handles Code-RAG chat queries strictly isolated by chat_id so no context leaks across chats."""
    data = request.get_json() or {}
    prompt = data.get("prompt", "").strip()
    chat_id = data.get("chat_id", "default_session").strip()
    repo_url = data.get("repo_url", "").strip()
    email = request.headers.get("X-User-Email", "user@supernova.local").strip().lower()

    if not prompt:
        return jsonify({"error": "Prompt cannot be empty"}), 400

    try:
        pipeline = get_pipeline_engine()

        # Execute query using chat_id session key (Redis history strictly isolated for this chat_id)
        res = pipeline.query_code_graph_rag(prompt, session_id=chat_id, repo_url=repo_url, max_history=8)
        answer_text = res.get("answer", "")

        # Save to chat history in memory store
        if email in chats_store:
            for c in chats_store[email]:
                if c["chat_id"] == chat_id:
                    c["messages"].append({"role": "user", "content": prompt})
                    c["messages"].append({"role": "assistant", "content": answer_text})
                    if repo_url:
                        c["repo_url"] = repo_url
                    break

        # Detect code update proposals in answer text
        proposal = parse_code_update_proposal(answer_text, res.get("vector_chunks", []))

        return jsonify({
            "user_prompt": prompt,
            "chat_id": chat_id,
            "repo_url": repo_url,
            "answer": answer_text,
            "vector_chunks": res.get("vector_chunks", []),
            "graph_context": res.get("graph_context", {}),
            "chat_history": res.get("chat_history", []),
            "code_update_proposal": proposal
        })

    except Exception as e:
        return jsonify({"error": f"Failed to execute query: {str(e)}"}), 500


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
        full_path = os.path.abspath(os.path.join(".", rel_file_path))
        if not os.path.exists(full_path):
            return jsonify({"error": f"Target file not found: {rel_file_path}"}), 404

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total_lines = len(lines)
        s_idx = max(0, line_start - 1)
        e_idx = min(total_lines, line_end)

        new_lines = updated_code.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        lines[s_idx:e_idx] = new_lines

        with open(full_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

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
        file_match = re.search(r'(?:Target File|File Path|file):\s*`?([a-zA-Z0-9_\-/\\]+\.[a-zA-Z0-9]+)`?', answer_text, re.IGNORECASE)
        lines_match = re.search(r'(?:Lines?|Line numbers?):\s*`?(\d+)\s*(?:to|-)\s*(\d+)`?', answer_text, re.IGNORECASE)
        code_match = re.search(r'```(?:python|javascript|typescript|js|ts|json|code)?\s*\n(.*?)```', answer_text, re.DOTALL)

        file_path = file_match.group(1).replace("\\", "/") if file_match else ""
        line_start = int(lines_match.group(1)) if lines_match else 1
        line_end = int(lines_match.group(2)) if lines_match else 1
        code_snippet = code_match.group(1).strip() if code_match else ""
        if code_snippet:
            code_snippet = code_snippet.replace('\\n', '\n').replace('\\t', '    ')

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

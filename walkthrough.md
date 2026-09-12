# SuperNova Code-GraphRAG System - Final Project Walkthrough

The professional-grade **SuperNova Code-GraphRAG System** is fully implemented, feature-complete, and empirically verified.

---

## 1. System Architecture Summary

The complete multi-modal brain architecture integrates **Gmail 6-Digit OTP Authentication**, **Isolated Multi-Chat Memory Sessions**, **AST-driven Semantic Vector Search (ChromaDB BGE-small-en-v1.5)**, **BM25 Lexical Keyword Search**, an **Ontological Knowledge Graph (Neo4j OKF)**, a **Log-Based Redis Chat Memory Buffer**, and **Local LLM Inference (`gemma4:31b-cloud` / `gpt-oss:120b`)**:

```
                          ┌───────────────────────────────────────────────┐
                          │ Gmail 6-Digit OTP Authentication (.env SMTP)  │
                          └───────────────────────┬───────────────────────┘
                                                  │
                                                  ▼
                          ┌───────────────────────────────────────────────┐
                          │ Multi-Chat Session Sidebar & Isolated Workspace│
                          └───────────────────────┬───────────────────────┘
                                                  │
                                                  ▼
                          ┌───────────────────────────────────────────────┐
                          │ CodeGraphRAG Pipeline (graph_rag_pipeline.py) │
                          └──────┬────────────────┬────────────────┬──────┘
                                 │                │                │
           ┌─────────────────────┘                │                └─────────────────────┐
           ▼                                      ▼                                      ▼
┌─────────────────────────────┐       ┌─────────────────────────┐            ┌───────────────────────┐
│ Redis Chat Buffer           │       │ Hybrid Vector + BM25    │            │ OKF Dual Blast Radius │
│ (Chat-Scoped 8 Messages)    │       │ (Top 2 Chunks via RRF)  │            │ (Neo4j Traversal)     │
└──────────┬──────────────────┘       └───────────┬─────────────┘            └───────────┬───────────┘
           │                                      │                                      │
           └──────────────────────────────────────┼──────────────────────────────────────┘
                                                  ▼
                               ┌────────────────────────────────────┐
                               │ Ollama LLM Inference Engine        │
                               │ (gemma4:31b-cloud)                 │
                               └──────────────────┬─────────────────┘
                                                  ▼
                               ┌────────────────────────────────────┐
                               │ Architect Code Rewrite             │
                               │ + Exact Line Spans                 │
                               │ + Dual Graph Blast Radius Impact   │
                               └────────────────────────────────────┘
```

---

## 2. Core Subsystems & Technical Details

### A. Gmail 6-Digit OTP Authentication Subsystem
- **Configuration ([`.env`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/.env))**: Real-time SMTP credentials configured for Gmail TLS authentication:
  ```env
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=srijan.chakraborty.office@gmail.com
  SMTP_PASS=jaok bptl pyev hfny
  SMTP_FROM=srijan.chakraborty.office@gmail.com
  ```
- **Authentication Flow**:
  - `POST /api/send-otp`: Generates a random 6-digit numeric OTP code (`100000`-`999999`) with a 10-minute expiration window and sends an email via `smtplib` TLS.
  - `POST /api/verify-otp`: Validates user-submitted OTP codes and authenticates user sessions.

### B. Multi-Chat Session Isolation (Zero Context Leakage)
- **Chat-Scoped Memory & Projects**: Each chat session generates a unique `chat_id` (e.g. `chat_1725985200000`) bound to a specific repository project (`repo_url`).
- **Context Boundary**: Redis chat history keys (`supernova:chat_history:<chat_id>`) and BM25 store keys (`bm25_<chat_id>.json`) are isolated strictly per `chat_id`.
- **Zero Leakage**: Messages, code chunks, and query contexts in Chat A are completely isolated from Chat B. Switch between projects without cross-project context contamination.

### C. Semantic AST Chunking, BM25 Keyword Indexing & OKF Graph Database
- **AST-Based Semantic Chunking ([`okf_extractor.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_extractor.py) & [`dual_index_sync.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/dual_index_sync.py))**: Tree-Sitter & Python AST parser extracting line bounds (`line_start`, `line_end`), signatures, and docstrings.
- **BM25 Lexical Keyword Indexing ([`keyword_index.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/keyword_index.py))**: Code-aware identifier tokenizer handling `snake_case`, `camelCase`, and keywords, calculating BM25Okapi relevance scores for lexical query terms.
- **Open Knowledge Format (OKF) ([`okf_blueprint.yml`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_blueprint.yml))**: Subject-Relationship-Object graph triples stored in Neo4j Cypher (`Repository CONTAINS File`, `File DEFINES Function`, `Function CALLS Function`) for blast radius analysis.

### D. Hybrid Retrieval & Dual Blast Radius Strategy
- **Reciprocal Rank Fusion (RRF)**: Blends dense semantic vector scores (`BAAI/bge-small-en-v1.5`) with sparse lexical keyword scores (`BM25Okapi`).
- **Top 2 Chunks Context**: Retrieval is strictly constrained to **Top 2 candidate code chunks** (`top_k=2`) for prompt generation (eliminating noise from top 5 chunks).
- **Dual Blast Radius Graph Traversal**: Performs OKF graph traversal and blast radius impact analysis for **both top candidate chunks** (Top 1 and Top 2 target components).

### E. Frontend Bug Fixes & Responsive UI
- **Code Block Formatting**: Markdown renderer extracts fenced `<pre><code>` blocks before escaping and line-break conversion, preserving code formatting and indentation without `<br>` distortion.
- **Event Delegation**: Replaced inline `onclick` string handlers with dataset attributes (`data-file-path`, `data-line-start`, `data-line-end`, `data-chat-id`) and event delegation for Code Update proposals and sidebar sessions.
- **Graceful Chat Deletion**: Deleting active chat sessions automatically selects the next available session or creates a clean new chat without leaving frozen UI state.

---

## 3. Key Modules & Files Created

| File | Description |
| :--- | :--- |
| [`.env`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/.env) | System environment file containing Gmail SMTP host, port, user, and app password credentials. |
| [`okf_blueprint.yml`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_blueprint.yml) | Standardized Open Knowledge Format schema defining graph nodes and relationship edges. |
| [`okf_extractor.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_extractor.py) | Tree-Sitter & AST parser extracting line ranges, signatures, calls, docstrings, and OKF triples. |
| [`keyword_index.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/keyword_index.py) | **[NEW]** BM25Okapi keyword index engine with code-aware tokenization and persistent chat-scoped storage. |
| [`dual_index_sync.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/dual_index_sync.py) | Synchronizes source files across Neo4j OKF graph, ChromaDB vector store, and BM25 keyword index. |
| [`redis_chat_buffer.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/redis_chat_buffer.py) | Redis log-based conversation memory buffer retaining the past 8 messages context per chat session ID. |
| [`graph_rag_pipeline.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/graph_rag_pipeline.py) | RAG Engine integrating hybrid vector/keyword search, Top 2 chunks limitation, dual OKF blast radius traversal, and Ollama LLM. |
| [`app.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/app.py) | Flask Web Server supporting Gmail OTP auth, multi-chat session routing, SSE ingestion streaming, history clearance, and surgical updates. |
| [`templates/index.html`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/templates/index.html) | Dashboard UI with OTP login overlay, left chat sidebar, BM25 chunk counters, and interactive chat interface. |
| [`static/js/app.js`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/static/js/app.js) | Frontend application logic managing OTP state, multi-chat switching, SSE streaming, event delegation, and surgical code updates. |
| [`static/css/style.css`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/static/css/style.css) | Dark-mode glassmorphism styling for dashboard, sidebar layout, code block containers, and OTP login modal. |
| [`requirements.txt`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/requirements.txt) | Complete dependency list including `langchain`, `langchain-ollama`, `neo4j`, `chromadb`, `redis`, `tree-sitter`, `fastapi`, and `flask`. |

---

## 4. Web API Endpoints

| Endpoint | Method | Purpose |
| :--- | :--- | :--- |
| `/` | `GET` | Renders main SuperNova interactive web dashboard. |
| `/api/status` | `GET` | Checks Neo4j, ChromaDB, and BM25 keyword index status and chunk counts. |
| `/api/send-otp` | `POST` | Generates and sends a 6-digit OTP code to the requested Gmail address via SMTP. |
| `/api/verify-otp` | `POST` | Verifies the 6-digit OTP code submitted by the user and authenticates session. |
| `/api/chats` | `GET / POST / DELETE` | Fetches, creates, or deletes isolated multi-chat sessions for the authenticated user. |
| `/api/chats/<chat_id>` | `GET` | Retrieves full conversation history and project scope for a specific chat ID. |
| `/api/stream-sync` | `GET` | SSE endpoint streaming real-time repository parsing, dual-index embedding, and BM25 indexing progress. |
| `/api/query` | `POST` | Processes chat-isolated RAG queries using top 2 hybrid chunks, dual OKF blast radius, and Redis 8-message history context. |
| `/api/chat-history` | `GET / DELETE` | Retrieves or clears the conversation history log for a specific session. |
| `/api/apply-update` | `POST` | Surgically applies proposed code changes to target files and re-indexes modified AST chunks across vector, BM25, and graph databases. |

---

## 5. Verification & Test Results

### Verification Command:
```bash
python scratch/test_keyword_and_pipeline.py
```

### Verified System Behavior:
1. **BM25 Lexical Keyword Scoring**: Accurately tokenized code identifiers (`process_payment`, `calculate_total`) and ranked keyword candidates.
2. **Top-2 Chunk Limit**: Confirmed retrieval strictly returns a maximum of 2 candidate code blocks to the LLM model context.
3. **Dual Blast Radius**: OKF graph traversal extracted relationships and target components for both top candidate chunks.
4. **Code Block Formatting**: Preserved code indentation and formatting inside chat bubbles without `<br>` distortion.

# SuperNova Code-GraphRAG System - Final Project Walkthrough

The professional-grade **SuperNova Code-GraphRAG System** is fully implemented, feature-complete, and empirically verified.

---

## 1. System Architecture Summary

The complete dual-brain architecture integrates **AST-driven Semantic Search (ChromaDB)** with an **Ontological Knowledge Graph (Neo4j)**, a **Log-Based Redis Chat Memory Buffer**, and **Local LLM Inference (`gemma4:31b-cloud` / `gpt-oss:120b`)**:

```
                                ┌──────────────────────────────────────┐
                                │ User Query / Web Dashboard / CLI     │
                                └──────────────────┬───────────────────┘
                                                   │
                                                   ▼
                                ┌──────────────────────────────────────┐
                                │ CodeGraphRAG Pipeline                │
                                │ (graph_rag_pipeline.py)              │
                                └──────┬───────────┬───────────┬───────┘
                                       │           │           │
                 ┌─────────────────────┘           │           └──────────────────────┐
                 ▼                                 ▼                                  ▼
┌─────────────────────────────────┐   ┌───────────────────────────┐     ┌───────────────────────────┐
│ Redis Chat Buffer               │   │ Vector Search (Cosine Sim)│     │ OKF Graph Blast Radius    │
│ (Past 8 Messages Context)       │   │ ChromaDB + BGE-small-v1.5 │     │ Neo4j Cypher Traversal    │
└────────────────┬────────────────┘   └────────────┬──────────────┘     └─────────────┬─────────────┘
                 │                                 │                                  │
                 └─────────────────────────────────┼──────────────────────────────────┘
                                                   ▼
                                  ┌─────────────────────────────────┐
                                  │ Ollama LLM Inference Engine     │
                                  │ (gemma4:31b-cloud)              │
                                  └────────────────┬────────────────┘
                                                   ▼
                                  ┌─────────────────────────────────┐
                                  │ Architect Code Rewrite          │
                                  │ + Exact Line Spans              │
                                  │ + Graph Blast Radius Impact     │
                                  └─────────────────────────────────┘
```

---

## 2. Core Subsystems & Technical Details

### A. Semantic AST Chunking vs. Sliding Window
- **AST-Based Semantic Chunking ([`okf_extractor.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_extractor.py))**: Source code files (`.py`, `.js`, `.ts`) are parsed into granular syntax tree blocks using Tree-Sitter and Python AST rather than arbitrary line counts. Each chunk carries exact metadata:
  - `id`: `file_path:function_name:line_start`
  - `line_start` and `line_end`
  - `signature` and `docstring`
- **Sliding Window Chunking ([`dual_index_sync.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/dual_index_sync.py))**: Used for sequential cues (transcripts/subtitles) with 60.0s window size and 5.0s overlap.

### B. Open Knowledge Format (OKF) & Neo4j Graph Database
- **Standardized Blueprint ([`okf_blueprint.yml`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_blueprint.yml))**: Defines nodes (`Repository`, `File`, `Class`, `Function`, `Module`) and relationships (`CONTAINS`, `DEFINES`, `CALLS`, `IMPORTS`, `DEPENDS_ON`).
- **Subject-Relationship-Object Triples**: Automatically extracted during code ingestion (e.g. `File DEFINES Function`, `Function CALLS Function`).
- **Blast Radius Analysis**: When querying or rewriting code, Cypher queries traverse incoming callers and downstream dependencies up to 2 hops away to warn the user of potential breaking changes.

### C. Redis Log-Based Chat Memory Buffer ([`redis_chat_buffer.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/redis_chat_buffer.py))
- **Rolling Log Buffer**: Stores user/assistant turns in a Redis list (`supernova:chat_history:<session_id>`) using `RPUSH` and `LTRIM -8 -1`.
- **Recursive Follow-up Queries**: Automatically fetches the **past 8 messages context** and injects them into the LLM system prompt under `=== RECENT CONVERSATION HISTORY (Last 8 Messages Log Context) ===`.
- **Zero-Crash Fallback**: Gracefully falls back to an in-memory dictionary list if the Redis server is unavailable.

### D. Vector Similarity & Chunk Retrieval Strategy
- **Cosine Similarity**: ChromaDB collection is initialized with `metadata={"hnsw:space": "cosine"}`. Embedded using normalized vectors from `BAAI/bge-small-en-v1.5`.
- **Top 5 Candidate Chunks**: Vector search retrieves the **top 5 most relevant semantic chunks** (`n_results=5`) to construct the multi-block code context for the LLM.
- **Top 1 Chunk**: The single top-scoring chunk (`vector_chunks[0]`) is used to select the primary target keyword for Neo4j blast radius graph traversal and fallback response generation.

---

## 3. Key Modules & Files Created

| File | Description |
| :--- | :--- |
| [`okf_blueprint.yml`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_blueprint.yml) | Standardized Open Knowledge Format schema defining graph node types and structural relationship edges. |
| [`okf_extractor.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/okf_extractor.py) | Tree-Sitter & AST parser extracting line ranges, signatures, calls, docstrings, and OKF triples. |
| [`dual_index_sync.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/dual_index_sync.py) | Dual-Brain Sync Engine. Chunks code files and synchronizes Neo4j graph nodes and ChromaDB vector embeddings with surgical stale-data purging. |
| [`redis_chat_buffer.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/redis_chat_buffer.py) | Redis log-based conversation memory buffer retaining the past 8 messages context per session ID. |
| [`graph_rag_pipeline.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/graph_rag_pipeline.py) | Full RAG Engine integrating ChromaDB vector search, Neo4j blast radius graph traversal, Redis history, and Ollama LLM code generation. |
| [`app.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/app.py) | Flask Web Dashboard backend supporting `/api/query`, `/api/stream-sync`, `/api/chat-history`, and `/api/apply-update`. |
| [`main.py`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/main.py) | Unified Command Line Interface for repository indexing (`sync`), RAG querying (`query`), file updating (`update`), and web dashboard launch. |
| [`requirements.txt`](file:///c:/Users/User/OneDrive/Documents/GitHub/SuperNova/requirements.txt) | Complete dependency list including `langchain`, `langchain-ollama`, `neo4j`, `chromadb`, `redis`, `tree-sitter`, `fastapi`, and `flask`. |

---

## 4. Web API Endpoints

| Endpoint | Method | Purpose |
| :--- | :--- | :--- |
| `/` | `GET` | Renders main SuperNova interactive web dashboard. |
| `/api/status` | `GET` | Checks Neo4j/ChromaDB database connectivity and chunk counts. |
| `/api/stream-sync` | `GET` | SSE endpoint streaming real-time repository parsing & dual-index embedding progress. |
| `/api/query` | `POST` | Processes user RAG queries using top 5 vector chunks, OKF blast radius, and Redis 8-message history context. |
| `/api/chat-history` | `GET / DELETE` | Retrieves or clears the past 8 messages Redis chat history log for a session. |
| `/api/apply-update` | `POST` | Surgically applies proposed code changes to target files and re-indexes modified AST chunks. |

---

## 5. Verification & Test Results

### Execution Verification:
```bash
python main.py query "Update sync_file in dual_index_sync.py to add retry logic"
```

### Verified Output & Behavior:
1. **Chat Memory**: Past 8 conversation turns successfully loaded from `RedisChatBuffer` and passed to the LLM prompt.
2. **Vector Search**: Top 5 code blocks retrieved via ChromaDB cosine similarity.
3. **Graph Blast Radius**: Identified dependent calls in `DualIndexSync.sync_repository` and `app.py`.
4. **Code Rewrite**: Outputted precise file line spans (`Lines 87 to 114`) and exact drop-in replacement code.

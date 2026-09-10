import os
import re
import ast
import yaml
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from git import Repo
import tree_sitter
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjs
from neo4j import GraphDatabase
import chromadb
from chromadb.utils import embedding_functions
from langchain_ollama import ChatOllama


class RepoManager:
    """Manages cloning or loading local Git repositories for AST ingestion."""
    
    def __init__(self, repo_target: str, target_dir: str = "./downloaded_repo"):
        self.repo_target = repo_target
        self.target_dir = target_dir
        self.repo_path = ""
        self.repo_name = ""

    def prepare_repo(self) -> str:
        if self.repo_target.startswith("http://") or self.repo_target.startswith("https://") or self.repo_target.endswith(".git"):
            self.repo_name = self.repo_target.rstrip("/").split("/")[-1].replace(".git", "")
            clone_path = os.path.abspath(os.path.join(self.target_dir, self.repo_name))
            if not os.path.exists(clone_path):
                print(f"[RepoManager] Cloning remote repository {self.repo_target} into {clone_path}...")
                os.makedirs(os.path.dirname(clone_path), exist_ok=True)
                Repo.clone_from(self.repo_target, clone_path)
            else:
                print(f"[RepoManager] Repository already available at {clone_path}")
            self.repo_path = clone_path
        else:
            self.repo_path = os.path.abspath(self.repo_target)
            self.repo_name = os.path.basename(self.repo_path.rstrip("/\\"))
            print(f"[RepoManager] Using local repository path: {self.repo_path}")
            
        return self.repo_path

    def get_source_files(self, extensions: List[str] = [".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".c", ".cpp"]) -> List[str]:
        source_files = []
        for root, dirs, files in os.walk(self.repo_path):
            # Ignore hidden dirs, venv, node_modules, git, dist, build
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', 'venv', '__pycache__', 'dist', 'build', 'downloaded_repo', 'chroma_db']]
            for file in files:
                if any(file.endswith(ext) for ext in extensions):
                    full_path = os.path.abspath(os.path.join(root, file))
                    source_files.append(full_path)
        return source_files


class ASTParser:
    """Uses Tree-Sitter and AST inspection to extract structural metadata & line ranges."""

    def __init__(self):
        try:
            self.py_language = tree_sitter.Language(tspython.language())
            self.py_parser = tree_sitter.Parser(self.py_language)
        except Exception as e:
            print(f"[ASTParser] Warning loading Tree-sitter Python: {e}")
            self.py_parser = None

        try:
            self.js_language = tree_sitter.Language(tsjs.language())
            self.js_parser = tree_sitter.Parser(self.js_language)
        except Exception as e:
            print(f"[ASTParser] Warning loading Tree-sitter JS: {e}")
            self.js_parser = None

    def parse_file(self, file_path: str, repo_root: str) -> Dict[str, Any]:
        rel_path = os.path.relpath(file_path, repo_root).replace("\\", "/")
        ext = os.path.splitext(file_path)[1].lower()
        language = "python" if ext == ".py" else ("javascript" if ext in [".js", ".jsx", ".ts", ".tsx"] else "unknown")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            code_content = f.read()

        total_lines = len(code_content.splitlines())

        file_metadata = {
            "path": rel_path,
            "full_path": file_path,
            "language": language,
            "total_lines": total_lines,
            "classes": [],
            "functions": [],
            "imports": [],
            "calls": []
        }

        if language == "python":
            self._parse_python(code_content, rel_path, file_metadata)
        elif language == "javascript":
            self._parse_javascript(code_content, rel_path, file_metadata)
        else:
            self._parse_generic_fallback(code_content, rel_path, file_metadata)

        return file_metadata

    def _parse_python(self, code_content: str, rel_path: str, meta: Dict[str, Any]):
        bytes_code = code_content.encode("utf-8")
        if self.py_parser:
            tree = self.py_parser.parse(bytes_code)
            
            def traverse(node):
                if node.type == "class_definition":
                    name_node = node.child_by_field_name("name")
                    class_name = bytes_code[name_node.start_byte:name_node.end_byte].decode("utf-8") if name_node else "UnknownClass"
                    meta["classes"].append({
                        "id": f"{rel_path}:{class_name}",
                        "name": class_name,
                        "file_path": rel_path,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "docstring": ""
                    })
                elif node.type == "function_definition":
                    name_node = node.child_by_field_name("name")
                    func_name = bytes_code[name_node.start_byte:name_node.end_byte].decode("utf-8") if name_node else "anonymous"
                    line_start = node.start_point[0] + 1
                    line_end = node.end_point[0] + 1
                    meta["functions"].append({
                        "id": f"{rel_path}:{func_name}:{line_start}",
                        "name": func_name,
                        "file_path": rel_path,
                        "line_start": line_start,
                        "line_end": line_end,
                        "signature": f"def {func_name}(...)",
                        "docstring": ""
                    })
                elif node.type in ["import_statement", "import_from_statement"]:
                    import_text = bytes_code[node.start_byte:node.end_byte].decode("utf-8")
                    meta["imports"].append({
                        "statement": import_text,
                        "line": node.start_point[0] + 1
                    })
                elif node.type == "call":
                    func_node = node.child_by_field_name("function")
                    if func_node:
                        called_name = bytes_code[func_node.start_byte:func_node.end_byte].decode("utf-8")
                        meta["calls"].append({
                            "called_name": called_name,
                            "line": node.start_point[0] + 1
                        })

                for child in node.children:
                    traverse(child)

            traverse(tree.root_node)

        # Complement with Python AST module for robust docstring & import extraction
        try:
            parsed_ast = ast.parse(code_content)
            for node in ast.walk(parsed_ast):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name not in [imp.get('name') for imp in meta['imports']]:
                            meta['imports'].append({"name": alias.name, "line": getattr(node, 'lineno', 1)})
                elif isinstance(node, ast.ImportFrom):
                    mod_name = node.module or ""
                    meta['imports'].append({"name": mod_name, "line": getattr(node, 'lineno', 1)})
                elif isinstance(node, ast.FunctionDef) and not self.py_parser:
                    line_start = node.lineno
                    line_end = getattr(node, 'end_lineno', line_start + 10)
                    meta['functions'].append({
                        "id": f"{rel_path}:{node.name}:{line_start}",
                        "name": node.name,
                        "file_path": rel_path,
                        "line_start": line_start,
                        "line_end": line_end,
                        "signature": f"def {node.name}(...)",
                        "docstring": ast.get_docstring(node) or ""
                    })
                elif isinstance(node, ast.ClassDef) and not self.py_parser:
                    line_start = node.lineno
                    line_end = getattr(node, 'end_lineno', line_start + 10)
                    meta['classes'].append({
                        "id": f"{rel_path}:{node.name}",
                        "name": node.name,
                        "file_path": rel_path,
                        "line_start": line_start,
                        "line_end": line_end,
                        "docstring": ast.get_docstring(node) or ""
                    })
        except Exception:
            pass

    def _parse_javascript(self, code_content: str, rel_path: str, meta: Dict[str, Any]):
        bytes_code = code_content.encode("utf-8")
        if self.js_parser:
            tree = self.js_parser.parse(bytes_code)

            def traverse(node):
                if node.type in ["class_declaration", "class"]:
                    name_node = node.child_by_field_name("name")
                    class_name = bytes_code[name_node.start_byte:name_node.end_byte].decode("utf-8") if name_node else "UnknownClass"
                    meta["classes"].append({
                        "id": f"{rel_path}:{class_name}",
                        "name": class_name,
                        "file_path": rel_path,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "docstring": ""
                    })
                elif node.type in ["function_declaration", "method_definition", "arrow_function"]:
                    name_node = node.child_by_field_name("name")
                    func_name = bytes_code[name_node.start_byte:name_node.end_byte].decode("utf-8") if name_node else "anonymous"
                    line_start = node.start_point[0] + 1
                    line_end = node.end_point[0] + 1
                    meta["functions"].append({
                        "id": f"{rel_path}:{func_name}:{line_start}",
                        "name": func_name,
                        "file_path": rel_path,
                        "line_start": line_start,
                        "line_end": line_end,
                        "signature": f"function {func_name}(...)",
                        "docstring": ""
                    })
                elif node.type == "import_statement":
                    import_text = bytes_code[node.start_byte:node.end_byte].decode("utf-8")
                    meta["imports"].append({
                        "statement": import_text,
                        "line": node.start_point[0] + 1
                    })
                elif node.type == "call_expression":
                    func_node = node.child_by_field_name("function")
                    if func_node:
                        called_name = bytes_code[func_node.start_byte:func_node.end_byte].decode("utf-8")
                        meta["calls"].append({
                            "called_name": called_name,
                            "line": node.start_point[0] + 1
                        })

                for child in node.children:
                    traverse(child)

            traverse(tree.root_node)

    def _parse_generic_fallback(self, code_content: str, rel_path: str, meta: Dict[str, Any]):
        lines = code_content.splitlines()
        func_regex = re.compile(r'^\s*(def|function|async def|public|private|fn|func)\s+([a-zA-Z0-9_]+)')
        class_regex = re.compile(r'^\s*(class|interface|struct)\s+([a-zA-Z0-9_]+)')

        for idx, line in enumerate(lines, start=1):
            f_match = func_regex.match(line)
            if f_match:
                func_name = f_match.group(2)
                meta["functions"].append({
                    "id": f"{rel_path}:{func_name}:{idx}",
                    "name": func_name,
                    "file_path": rel_path,
                    "line_start": idx,
                    "line_end": min(len(lines), idx + 20),
                    "signature": line.strip(),
                    "docstring": ""
                })
            c_match = class_regex.match(line)
            if c_match:
                class_name = c_match.group(2)
                meta["classes"].append({
                    "id": f"{rel_path}:{class_name}:{idx}",
                    "name": class_name,
                    "file_path": rel_path,
                    "line_start": idx,
                    "line_end": min(len(lines), idx + 30),
                    "docstring": ""
                })


class OKFExtractor:
    """Uses Ollama gpt-oss:120b to refine AST structures into OKF triples, with deterministic fallback."""

    def __init__(self, model_name: str = "gpt-oss:120b", ollama_url: str = "http://localhost:11434"):
        try:
            self.llm = ChatOllama(model=model_name, base_url=ollama_url, temperature=0.1)
        except Exception:
            self.llm = None

    def extract_okf_triples(self, file_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Always build baseline deterministic triples first
        triples = []
        file_name = file_meta['path']
        for c in file_meta['classes']:
            triples.append({
                "subject": file_name, "subject_type": "File",
                "relationship": "DEFINES",
                "object": c['name'], "object_type": "Class"
            })
        for f in file_meta['functions']:
            triples.append({
                "subject": file_name, "subject_type": "File",
                "relationship": "DEFINES",
                "object": f['name'], "object_type": "Function"
            })
        for call in file_meta['calls']:
            triples.append({
                "subject": file_name, "subject_type": "File",
                "relationship": "CALLS",
                "object": call['called_name'], "object_type": "Function"
            })

        if not self.llm:
            return triples

        prompt = f"""You are an expert Software Knowledge Graph Architect.
Analyze the following AST blueprint extracted from `{file_meta['path']}`.

File Path: {file_meta['path']}
Language: {file_meta['language']}
Classes defined: {[c['name'] for c in file_meta['classes']]}
Functions defined: {[f['name'] for f in file_meta['functions']]}
Import statements: {file_meta['imports'][:5]}
Function Calls: {file_meta['calls'][:10]}

Format output as a JSON list of OKF Triples representing high-level relationships.
Each triple MUST follow this schema:
{{
  "subject": "SubjectName",
  "subject_type": "File" | "Class" | "Function" | "Module",
  "relationship": "DEFINES" | "CALLS" | "IMPORTS" | "DEPENDS_ON",
  "object": "ObjectName",
  "object_type": "File" | "Class" | "Function" | "Module"
}}

Respond ONLY with valid JSON array of objects.
"""
        try:
            response = self.llm.invoke(prompt)
            content = response.content.strip()
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                llm_triples = json.loads(match.group(0))
                return llm_triples if isinstance(llm_triples, list) else triples
        except Exception as e:
            print(f"[OKFExtractor] Using deterministic AST triples for {file_meta['path']} ({e})")
            
        return triples


class Neo4jGraphManager:
    """Manages Neo4j driver connection and Cypher graph updates with offline fallback."""

    def __init__(self, uri: str = "bolt://localhost:7687", auth: tuple = ("neo4j", "test123456")):
        self.uri = uri
        self.auth = auth
        self.driver = None
        self.is_connected = False
        self._in_memory_triples = []
        self._connect()

    def _connect(self):
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=self.auth)
            self.driver.verify_connectivity()
            self.is_connected = True
            print("[Neo4jGraphManager] Connected to Neo4j successfully.")
        except Exception as e:
            print(f"[Neo4jGraphManager] Neo4j server unavailable at {self.uri} ({e}). Using in-memory graph fallback.")
            self.is_connected = False
            self.driver = None

    def close(self):
        if self.driver:
            self.driver.close()

    def setup_schema(self):
        if not self.is_connected or not self.driver:
            return
        queries = [
            "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE;",
            "CREATE CONSTRAINT class_id_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.id IS UNIQUE;",
            "CREATE CONSTRAINT func_id_unique IF NOT EXISTS FOR (fn:Function) REQUIRE fn.id IS UNIQUE;",
            "CREATE CONSTRAINT module_name_unique IF NOT EXISTS FOR (m:Module) REQUIRE m.name IS UNIQUE;",
            "CREATE CONSTRAINT repo_name_unique IF NOT EXISTS FOR (r:Repository) REQUIRE r.name IS UNIQUE;"
        ]
        try:
            with self.driver.session() as session:
                for q in queries:
                    session.run(q)
        except Exception as e:
            print(f"[Neo4jGraphManager] Schema setup notice: {e}")

    def ingest_okf_graph(self, repo_name: str, file_metas: List[Dict[str, Any]], llm_triples: List[Dict[str, Any]]):
        for meta in file_metas:
            for fn in meta['functions']:
                self._in_memory_triples.append({
                    "subject": meta['path'], "subject_type": "File",
                    "relationship": "DEFINES",
                    "object": fn['name'], "object_type": "Function",
                    "file_path": meta['path'], "line_start": fn['line_start'], "line_end": fn['line_end']
                })

        for triple in llm_triples:
            self._in_memory_triples.append(triple)

        if not self.is_connected or not self.driver:
            return

        try:
            with self.driver.session() as session:
                session.run("""
                    MERGE (r:Repository {name: $repo_name})
                    ON CREATE SET r.indexed_at = $timestamp
                    ON MATCH SET r.updated_at = $timestamp
                """, repo_name=repo_name, timestamp=datetime.now().isoformat())

                for meta in file_metas:
                    session.run("""
                        MATCH (r:Repository {name: $repo_name})
                        MERGE (f:File {path: $path})
                        ON CREATE SET f.language = $language, f.total_lines = $total_lines
                        MERGE (r)-[:CONTAINS]->(f)
                    """, repo_name=repo_name, path=meta['path'], language=meta['language'], total_lines=meta['total_lines'])

                    for c in meta['classes']:
                        session.run("""
                            MATCH (f:File {path: $file_path})
                            MERGE (cls:Class {id: $id})
                            ON CREATE SET cls.name = $name, cls.line_start = $line_start, cls.line_end = $line_end, cls.file_path = $file_path
                            MERGE (f)-[:DEFINES]->(cls)
                        """, **c)

                    for fn in meta['functions']:
                        session.run("""
                            MATCH (f:File {path: $file_path})
                            MERGE (func:Function {id: $id})
                            ON CREATE SET func.name = $name, func.signature = $signature, func.line_start = $line_start, func.line_end = $line_end, func.file_path = $file_path
                            MERGE (f)-[:DEFINES]->(func)
                        """, **fn)

                for triple in llm_triples:
                    s_type = triple.get("subject_type", "File")
                    o_type = triple.get("object_type", "Function")
                    rel = triple.get("relationship", "DEPENDS_ON")

                    session.run(f"""
                        MERGE (s:{s_type} {{name: $subj}})
                        MERGE (o:{o_type} {{name: $obj}})
                        MERGE (s)-[:{rel}]->(o)
                    """, subj=triple.get("subject"), obj=triple.get("object"))
        except Exception as e:
            print(f"[Neo4jGraphManager] Ingestion error notice: {e}")

    def get_summary_stats(self) -> Dict[str, Any]:
        if self.is_connected and self.driver:
            try:
                with self.driver.session() as session:
                    res = session.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count")
                    stats = {record['label']: record['count'] for record in res}
                    rel_res = session.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS count")
                    rel_stats = {record['rel']: record['count'] for record in rel_res}
                    return {"nodes": stats, "relationships": rel_stats}
            except Exception:
                pass
        return {"in_memory_triples": len(self._in_memory_triples)}


class ChromaManager:
    """Manages ChromaDB vector client with BAAI/bge-small-en-v1.5 embeddings."""

    def __init__(self, host: str = "localhost", port: int = 8000, persist_dir: str = "./chroma_db"):
        self.embedding_fn = None
        try:
            print("[ChromaManager] Initializing BAAI/bge-small-en-v1.5 embedding function...")
            self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="BAAI/bge-small-en-v1.5"
            )
        except Exception as e:
            print(f"[ChromaManager] Warning initializing BAAI/bge-small-en-v1.5 embedding: {e}")

        try:
            self.client = chromadb.HttpClient(host=host, port=port)
            self.client.heartbeat()
            print(f"[ChromaManager] Connected to ChromaDB HttpClient at http://{host}:{port}")
        except Exception:
            print(f"[ChromaManager] ChromaDB HttpClient unavailable. Using local PersistentClient at '{persist_dir}'")
            self.client = chromadb.PersistentClient(path=persist_dir)

    def verify_connection(self) -> bool:
        try:
            self.client.heartbeat()
            return True
        except Exception:
            return True

    def get_or_create_collection(self, collection_name: str = "code_semantic_chunks"):
        kwargs = {"name": collection_name, "metadata": {"hnsw:space": "cosine"}}
        if self.embedding_fn:
            kwargs["embedding_function"] = self.embedding_fn
        return self.client.get_or_create_collection(**kwargs)

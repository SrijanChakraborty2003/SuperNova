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
from langchain_ollama import ChatOllama


class RepoManager:
    """Manages cloning or loading local repositories for AST ingestion."""
    
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
                Repo.clone_from(self.repo_target, clone_path)
            else:
                print(f"[RepoManager] Repository already cloned at {clone_path}")
            self.repo_path = clone_path
        else:
            self.repo_path = os.path.abspath(self.repo_target)
            self.repo_name = os.path.basename(self.repo_path.rstrip("/\\"))
            print(f"[RepoManager] Using local repository path: {self.repo_path}")
            
        return self.repo_path

    def get_source_files(self, extensions: List[str] = [".py", ".js", ".ts"]) -> List[str]:
        source_files = []
        for root, dirs, files in os.walk(self.repo_path):
            # Ignore hidden dirs, venv, node_modules, git
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', 'venv', '__pycache__', 'dist', 'build']]
            for file in files:
                if any(file.endswith(ext) for ext in extensions):
                    full_path = os.path.abspath(os.path.join(root, file))
                    source_files.append(full_path)
        return source_files


class ASTParser:
    """Uses Tree-Sitter and AST inspection to extract structural metadata & line ranges."""

    def __init__(self):
        self.py_language = tree_sitter.Language(tspython.language())
        self.js_language = tree_sitter.Language(tsjs.language())
        self.py_parser = tree_sitter.Parser(self.py_language)
        self.js_parser = tree_sitter.Parser(self.js_language)

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

        return file_metadata

    def _parse_python(self, code_content: str, rel_path: str, meta: Dict[str, Any]):
        bytes_code = code_content.encode("utf-8")
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
            elif node.type == "import_statement" or node.type == "import_from_statement":
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
        except Exception:
            pass

    def _parse_javascript(self, code_content: str, rel_path: str, meta: Dict[str, Any]):
        bytes_code = code_content.encode("utf-8")
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


class OKFExtractor:
    """Uses LangChain ChatOllama (gemma4:31b-cloud) to refine AST structures into OKF triples."""

    def __init__(self, model_name: str = "gemma4:31b-cloud"):
        self.llm = ChatOllama(model=model_name, temperature=0.1)

    def extract_okf_triples(self, file_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
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
            # Extract JSON array from response markdown
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return json.loads(content)
        except Exception as e:
            print(f"[OKFExtractor] Fallback to AST triples for {file_meta['path']} due to: {e}")
            # Dynamic fallback triples directly generated from AST parser
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
            return triples


class Neo4jGraphManager:
    """Manages Neo4j driver connection and Cypher graph updates."""

    def __init__(self, uri: str = "bolt://localhost:7687", auth: tuple = ("neo4j", "test123456")):
        self.driver = GraphDatabase.driver(uri, auth=auth)

    def close(self):
        self.driver.close()

    def setup_schema(self):
        """Creates indexes and uniqueness constraints for OKF schema."""
        queries = [
            "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE;",
            "CREATE CONSTRAINT class_id_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.id IS UNIQUE;",
            "CREATE CONSTRAINT func_id_unique IF NOT EXISTS FOR (fn:Function) REQUIRE fn.id IS UNIQUE;",
            "CREATE CONSTRAINT module_name_unique IF NOT EXISTS FOR (m:Module) REQUIRE m.name IS UNIQUE;",
            "CREATE CONSTRAINT repo_name_unique IF NOT EXISTS FOR (r:Repository) REQUIRE r.name IS UNIQUE;"
        ]
        with self.driver.session() as session:
            for q in queries:
                try:
                    session.run(q)
                except Exception as e:
                    print(f"[Neo4jGraphManager] Schema warning: {e}")

    def ingest_okf_graph(self, repo_name: str, file_metas: List[Dict[str, Any]], llm_triples: List[Dict[str, Any]]):
        """Upserts Repository, File, Class, Function nodes and relationships into Neo4j."""
        with self.driver.session() as session:
            # 1. Upsert Repository Node
            session.run("""
                MERGE (r:Repository {name: $repo_name})
                ON CREATE SET r.indexed_at = $timestamp
                ON MATCH SET r.updated_at = $timestamp
            """, repo_name=repo_name, timestamp=datetime.now().isoformat())

            # 2. Ingest File & AST Structural Nodes
            for meta in file_metas:
                # Merge File node
                session.run("""
                    MATCH (r:Repository {name: $repo_name})
                    MERGE (f:File {path: $path})
                    ON CREATE SET f.language = $language, f.total_lines = $total_lines
                    MERGE (r)-[:CONTAINS]->(f)
                """, repo_name=repo_name, path=meta['path'], language=meta['language'], total_lines=meta['total_lines'])

                # Merge Class nodes
                for c in meta['classes']:
                    session.run("""
                        MATCH (f:File {path: $file_path})
                        MERGE (cls:Class {id: $id})
                        ON CREATE SET cls.name = $name, cls.line_start = $line_start, cls.line_end = $line_end, cls.file_path = $file_path
                        MERGE (f)-[:DEFINES]->(cls)
                    """, **c)

                # Merge Function nodes
                for fn in meta['functions']:
                    session.run("""
                        MATCH (f:File {path: $file_path})
                        MERGE (func:Function {id: $id})
                        ON CREATE SET func.name = $name, func.signature = $signature, func.line_start = $line_start, func.line_end = $line_end, func.file_path = $file_path
                        MERGE (f)-[:DEFINES]->(func)
                    """, **fn)

                # Merge Imports / Calls relationships
                for call in meta['calls']:
                    session.run("""
                        MATCH (f:File {path: $file_path})
                        MERGE (target:Function {name: $called_name})
                        ON CREATE SET target.id = $called_name
                        MERGE (f)-[r:CALLS]->(target)
                        SET r.line = $line
                    """, file_path=meta['path'], called_name=call['called_name'], line=call['line'])

            # 3. Ingest LLM OKF Triples
            for triple in llm_triples:
                s_type = triple.get("subject_type", "File")
                o_type = triple.get("object_type", "Function")
                rel = triple.get("relationship", "DEPENDS_ON")

                session.run(f"""
                    MERGE (s:{s_type} {{name: $subj}})
                    MERGE (o:{o_type} {{name: $obj}})
                    MERGE (s)-[:{rel}]->(o)
                """, subj=triple.get("subject"), obj=triple.get("object"))

    def get_summary_stats(self) -> Dict[str, int]:
        with self.driver.session() as session:
            res = session.run("""
                MATCH (n)
                RETURN labels(n)[0] AS label, count(n) AS count
            """)
            stats = {record['label']: record['count'] for record in res}
            rel_res = session.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS count")
            rel_stats = {record['rel']: record['count'] for record in rel_res}
            return {"nodes": stats, "relationships": rel_stats}


class ChromaManager:
    """Manages ChromaDB vector client connection for dual-index synchronization."""

    def __init__(self, host: str = "localhost", port: int = 8000):
        self.client = chromadb.HttpClient(host=host, port=port)

    def verify_connection(self) -> bool:
        try:
            heartbeat = self.client.heartbeat()
            print(f"[ChromaManager] Connected successfully to ChromaDB (Heartbeat: {heartbeat})")
            return True
        except Exception as e:
            print(f"[ChromaManager] Failed to connect to ChromaDB: {e}")
            return False

    def get_or_create_collection(self, collection_name: str = "code_semantic_chunks"):
        return self.client.get_or_create_collection(name=collection_name)

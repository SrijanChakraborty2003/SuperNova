import os
import shutil
from pathlib import Path
from typing import List
import subprocess

from mcp.server.fastmcp import FastMCP


BASE_PATH = Path("C:/Users/User/OneDrive/Desktop/Test-Run").resolve()
CONDA_ENV = "learning"

mcp = FastMCP("filesystem-tools")


def resolve_safe_path(user_path: str) -> Path:
    """
    Ensure path stays inside BASE_PATH.
    """
    target = (BASE_PATH / user_path).resolve()

    if not str(target).startswith(str(BASE_PATH)):
        raise ValueError("Access outside BASE_PATH is not allowed")

    return target


# ------------------------------------------------
# FILE CREATION
# ------------------------------------------------
@mcp.tool()
def create_file(path: str, content: str = "") -> str:
    """
    Create a file and return its absolute path.
    """
    file_path = resolve_safe_path(path)

    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return str(file_path)


# ------------------------------------------------
# FILE DELETE
# ------------------------------------------------
@mcp.tool()
def delete_file(path: str) -> str:
    """
    Delete a file.
    """
    file_path = resolve_safe_path(path)

    if not file_path.exists():
        return "File does not exist"

    file_path.unlink()

    return f"Deleted: {file_path}"


# ------------------------------------------------
# FOLDER CREATE
# ------------------------------------------------
@mcp.tool()
def create_folder(path: str) -> str:
    """
    Create folder and return its path.
    """
    folder_path = resolve_safe_path(path)

    folder_path.mkdir(parents=True, exist_ok=True)

    return str(folder_path)


# ------------------------------------------------
# FOLDER DELETE
# ------------------------------------------------
@mcp.tool()
def delete_folder(path: str) -> str:
    """
    Delete folder recursively.
    """
    folder_path = resolve_safe_path(path)

    if not folder_path.exists():
        return "Folder does not exist"

    shutil.rmtree(folder_path)

    return f"Deleted folder: {folder_path}"


# ------------------------------------------------
# FILE READ
# ------------------------------------------------
@mcp.tool()
def read_file(path: str) -> str:
    """
    Read file content.
    """
    file_path = resolve_safe_path(path)

    if not file_path.exists():
        return "File does not exist"

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------
# FILE WRITE / APPEND
# ------------------------------------------------
@mcp.tool()
def write_file(path: str, content: str, append: bool = False) -> str:
    """
    Write or append to file and return path.
    """
    file_path = resolve_safe_path(path)

    mode = "a" if append else "w"

    with open(file_path, mode, encoding="utf-8") as f:
        f.write(content)

    return str(file_path)


# ------------------------------------------------
# LIST DIRECTORY
# ------------------------------------------------
@mcp.tool()
def list_directory(path: str = "") -> List[str]:
    """
    List files and folders inside directory.
    """
    dir_path = resolve_safe_path(path)

    if not dir_path.exists():
        return []

    return [p.name for p in dir_path.iterdir()]


# ------------------------------------------------
# SEARCH FILES
# ------------------------------------------------
@mcp.tool()
def search_files(name: str, start_path: str = "") -> List[str]:
    """
    Recursively search files by name.
    """
    root = resolve_safe_path(start_path)

    matches = []

    for path in root.rglob("*"):
        if name.lower() in path.name.lower():
            matches.append(str(path.relative_to(BASE_PATH)))

    return matches


# ------------------------------------------------
# DISCOVER FOLDER STRUCTURE
# ------------------------------------------------
@mcp.tool()
def discover_structure(start_path: str = "") -> List[str]:
    """
    Recursively discover folder structure.
    """
    root = resolve_safe_path(start_path)

    structure = []

    for path in root.rglob("*"):
        structure.append(str(path.relative_to(BASE_PATH)))

    return structure





# ------------------------------------------------
# START MCP SERVER
# ------------------------------------------------
if __name__ == "__main__":
    mcp.run()
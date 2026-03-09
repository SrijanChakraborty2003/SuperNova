import subprocess
import os
from mcp.server.fastmcp import FastMCP
from pathlib import Path
mcp = FastMCP("filesystem-tools")

BASE_PATH = Path("C:/Users/User/OneDrive/Desktop/Test-Run").resolve()
CONDA_ENV = "learning"

# ------------------------------------------------
# RUN PYTHON FILE
# ------------------------------------------------
def resolve_safe_path(user_path: str) -> Path:
    """
    Ensure path stays inside BASE_PATH.
    """
    target = (BASE_PATH / user_path).resolve()

    if not str(target).startswith(str(BASE_PATH)):
        raise ValueError("Access outside BASE_PATH is not allowed")

    return target

@mcp.tool()
def run_python_file(file_path: str) -> str:
    """
    Run python file inside conda environment 'learning'.
    Accepts relative or absolute path.
    """

    try:

        path_obj = Path(file_path)

        if not path_obj.is_absolute():
            path_obj = resolve_safe_path(file_path)

        command = [
            "conda",
            "run",
            "-n",
            CONDA_ENV,
            "python",
            str(path_obj)
        ]

        process = subprocess.run(
            command,
            capture_output=True,
            text=True
        )

        return f"""
Return Code: {process.returncode}

STDOUT:
{process.stdout}

STDERR:
{process.stderr}
"""

    except Exception as e:
        return f"Execution error: {str(e)}"
    
# ------------------------------------------------
# START MCP SERVER
# ------------------------------------------------
if __name__ == "__main__":
    mcp.run()
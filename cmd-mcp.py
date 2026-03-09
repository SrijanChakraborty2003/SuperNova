import subprocess
import os
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("filesystem-tools")

CONDA_ENV = "learning"

@mcp.tool()
def run_python_file(file_path: str, working_directory: str = "") -> str:
    """
    Run python file inside conda environment 'learning'.
    """

    cwd = BASE_PATH

    if working_directory:
        cwd = resolve_safe_path(working_directory)

    command = [
        "conda",
        "run",
        "-n",
        CONDA_ENV,
        "python",
        file_path
    ]

    process = subprocess.run(
        command,
        cwd=cwd,
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
import subprocess
import os
from mcp.server.fastmcp import FastMCP
from pathlib import Path
mcp = FastMCP("filesystem-tools")

BASE_PATH = Path("C:/Users/User/OneDrive/Desktop/Test-Run").resolve()
CONDA_ENV = "learning"

@mcp.tool()
def run_python_file(file_path: str) -> str:
    """
    Run python file inside conda environment 'learning'.
    Accepts relative or absolute path.
    Returns stdout + stderr logs so the caller can debug issues.
    """

    abs_path = os.path.abspath(file_path)
    working_dir = os.path.dirname(abs_path)

    # Create a temp log file to capture output
    log_file = os.path.join(working_dir, "run_log.txt")

    # Build the command:
    # 1. conda activate learning
    # 2. run python file
    # 3. pipe both stdout and stderr to log file
    # 4. also show in CMD window (tee-like using > and type)
    command = (
    f'conda run -n learning '
    f'python "{abs_path}" > "{log_file}" 2>&1')

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30       # ← wait up to 30s to capture startup logs
        )

        # Read the log file
        logs = ""
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                logs = f.read()

        # Combine everything
        output = []
        if logs:
            output.append(f"=== SERVER LOGS ===\n{logs}")
        if result.stdout:
            output.append(f"=== STDOUT ===\n{result.stdout}")
        if result.stderr:
            output.append(f"=== STDERR ===\n{result.stderr}")
        if result.returncode != 0:
            output.append(f"=== EXIT CODE: {result.returncode} ===")

        return "\n".join(output) if output else "Process started with no output."

    except subprocess.TimeoutExpired:
        # For long-running servers (like Flask), timeout is expected
        # Still read whatever was logged so far
        logs = ""
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                logs = f.read()
        return f"=== SERVER STARTED (timeout after 30s - normal for servers) ===\n{logs}"

    except Exception as e:
        return f"=== TOOL ERROR ===\n{str(e)}"
    

@mcp.tool()
def kill_python_server(file_path: str) -> str:
    """
    Kill a running python process that was started using the given file path.
    Works on Windows using taskkill.
    """

    import os
    import subprocess

    abs_path = os.path.abspath(file_path)

    try:
        # Find the process using WMIC
        find_cmd = f'wmic process where "CommandLine like \'%{abs_path}%\' and Name=\'python.exe\'" get ProcessId'

        result = subprocess.run(
            find_cmd,
            shell=True,
            capture_output=True,
            text=True
        )

        pids = []

        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(line)

        if not pids:
            return "No running server process found."

        killed = []

        for pid in pids:
            kill_cmd = f"taskkill /PID {pid} /F"
            subprocess.run(kill_cmd, shell=True)
            killed.append(pid)

        return f"Server stopped. Killed PIDs: {', '.join(killed)}"

    except Exception as e:
        return f"=== KILL TOOL ERROR ===\n{str(e)}"
    
# ------------------------------------------------
# START MCP SERVER
# ------------------------------------------------
if __name__ == "__main__":
    mcp.run()
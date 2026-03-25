# import subprocess
# import os
# from mcp.server.fastmcp import FastMCP
# from pathlib import Path
# mcp = FastMCP("filesystem-tools")

# BASE_PATH = Path("C:/Users/User/OneDrive/Desktop/Test-Run").resolve()
# CONDA_ENV = "learning"

# @mcp.tool()
# def run_python_file(file_path: str) -> str:
#     """
#     Run python file inside conda environment 'learning'.
#     Accepts relative or absolute path.
#     Returns stdout + stderr logs so the caller can debug issues.
#     """

#     abs_path = os.path.abspath(file_path)
#     working_dir = os.path.dirname(abs_path)

#     # Create a temp log file to capture output
#     log_file = os.path.join(working_dir, "run_log.txt")

#     # Build the command:
#     # 1. conda activate learning
#     # 2. run python file
#     # 3. pipe both stdout and stderr to log file
#     # 4. also show in CMD window (tee-like using > and type)
#     command = (
#     f'conda run -n learning '
#     f'python "{abs_path}" > "{log_file}" 2>&1')

#     try:
#         result = subprocess.run(
#             command,
#             shell=True,
#             cwd=working_dir,
#             capture_output=True,
#             text=True,
#             timeout=30       # ← wait up to 30s to capture startup logs
#         )

#         # Read the log file
#         logs = ""
#         if os.path.exists(log_file):
#             with open(log_file, "r") as f:
#                 logs = f.read()

#         # Combine everything
#         output = []
#         if logs:
#             output.append(f"=== SERVER LOGS ===\n{logs}")
#         if result.stdout:
#             output.append(f"=== STDOUT ===\n{result.stdout}")
#         if result.stderr:
#             output.append(f"=== STDERR ===\n{result.stderr}")
#         if result.returncode != 0:
#             output.append(f"=== EXIT CODE: {result.returncode} ===")

#         return "\n".join(output) if output else "Process started with no output."

#     except subprocess.TimeoutExpired:
#         # For long-running servers (like Flask), timeout is expected
#         # Still read whatever was logged so far
#         logs = ""
#         if os.path.exists(log_file):
#             with open(log_file, "r") as f:
#                 logs = f.read()
#         return f"=== SERVER STARTED (timeout after 30s - normal for servers) ===\n{logs}"

#     except Exception as e:
#         return f"=== TOOL ERROR ===\n{str(e)}"
    

# @mcp.tool()
# def kill_python_server(file_path: str) -> str:
#     """
#     Kill a running python process that was started using the given file path.
#     Works on Windows using taskkill.
#     """

#     import os
#     import subprocess

#     abs_path = os.path.abspath(file_path)

#     try:
#         # Find the process using WMIC
#         find_cmd = f'wmic process where "CommandLine like \'%{abs_path}%\' and Name=\'python.exe\'" get ProcessId'

#         result = subprocess.run(
#             find_cmd,
#             shell=True,
#             capture_output=True,
#             text=True
#         )

#         pids = []

#         for line in result.stdout.splitlines():
#             line = line.strip()
#             if line.isdigit():
#                 pids.append(line)

#         if not pids:
#             return "No running server process found."

#         killed = []

#         for pid in pids:
#             kill_cmd = f"taskkill /PID {pid} /F"
#             subprocess.run(kill_cmd, shell=True)
#             killed.append(pid)

#         return f"Server stopped. Killed PIDs: {', '.join(killed)}"

#     except Exception as e:
#         return f"=== KILL TOOL ERROR ===\n{str(e)}"
    
# # ------------------------------------------------
# # START MCP SERVER
# # ------------------------------------------------
# if __name__ == "__main__":
#     mcp.run()





"""
MCP Server - Run CMD commands via Conda env & read logs
--------------------------------------------------------
Configuration (edit these two lines):
  BASE_PATH  : root folder of your project
  CONDA_ENV  : name of the conda environment to activate
"""

import asyncio
import subprocess
import os
import sys
import glob
from datetime import datetime
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ─────────────────────────────────────────────
#  USER CONFIGURATION  ← edit these two lines
# ─────────────────────────────────────────────
BASE_PATH = Path("C:/Users/User/OneDrive/Desktop/Test-Run").resolve()
CONDA_ENV = "learning"                                    # conda environment name
# ─────────────────────────────────────────────

app = Server("cmd-runner")


def _conda_run(command: str, cwd: str | None = None) -> dict:
    """
    Execute *command* inside the configured conda environment.
    Works on Windows (conda activate) and Linux/macOS (conda run).
    Returns {"stdout": ..., "stderr": ..., "returncode": ...}
    """
    work_dir = cwd or BASE_PATH

    if sys.platform == "win32":
        # Windows: use conda activate via cmd /c
        full_cmd = (
            f'cmd /c "conda activate {CONDA_ENV} && {command}"'
        )
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=work_dir,
        )
    else:
        # Linux / macOS: use conda run
        result = subprocess.run(
            ["conda", "run", "-n", CONDA_ENV, "bash", "-c", command],
            capture_output=True,
            text=True,
            cwd=work_dir,
        )

    return {
        "stdout":     result.stdout.strip(),
        "stderr":     result.stderr.strip(),
        "returncode": result.returncode,
    }


def _fmt(result: dict) -> str:
    """Format a command result dict into a readable string."""
    lines = [f"Return code: {result['returncode']}"]
    if result["stdout"]:
        lines += ["", "── STDOUT ──", result["stdout"]]
    if result["stderr"]:
        lines += ["", "── STDERR ──", result["stderr"]]
    return "\n".join(lines)


# ══════════════════════════════════════════════
#  TOOL DEFINITIONS
# ══════════════════════════════════════════════

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # ── 1. Run any Python file ─────────────────
        types.Tool(
            name="run_python_file",
            description=(
                "Activate the configured conda env, then run a Python file "
                "with optional arguments. The file path is relative to BASE_PATH "
                "unless an absolute path is given."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Python file to run, e.g. 'server.py' or 'scripts/worker.py'",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional CLI arguments to pass to the script (space-separated)",
                        "default": "",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory override (absolute path). Defaults to BASE_PATH.",
                        "default": "",
                    },
                },
                "required": ["file"],
            },
        ),

        # ── 2. Run any raw shell command ───────────
        types.Tool(
            name="run_command",
            description=(
                "Run any shell command inside the configured conda environment. "
                "Useful for pip install, migrations, custom scripts, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Full shell command to execute, e.g. 'pip install -r requirements.txt'",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory override (absolute path). Defaults to BASE_PATH.",
                        "default": "",
                    },
                },
                "required": ["command"],
            },
        ),

        # ── 3. Read a log file ─────────────────────
        types.Tool(
            name="read_log",
            description=(
                "Read the contents of a log file. Supports tail (last N lines), "
                "search by keyword, and listing all .log files under BASE_PATH."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": (
                            "Log file path (relative to BASE_PATH or absolute). "
                            "Pass 'list' to list all .log files under BASE_PATH."
                        ),
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Return only the last N lines. 0 = return all lines.",
                        "default": 100,
                    },
                    "keyword": {
                        "type": "string",
                        "description": "Filter lines containing this keyword (case-insensitive). Leave empty for no filter.",
                        "default": "",
                    },
                },
                "required": ["file"],
            },
        ),

        # ── 4. Start a long-running process ────────
        types.Tool(
            name="start_server",
            description=(
                "Start a Python file as a background process (non-blocking). "
                "Useful for launching a backend server that keeps running. "
                "Returns the process PID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Python file to start, e.g. 'server.py'",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional CLI arguments",
                        "default": "",
                    },
                    "log_output_to": {
                        "type": "string",
                        "description": "Optional file path to redirect stdout+stderr (e.g. 'logs/server.log'). Relative to BASE_PATH.",
                        "default": "",
                    },
                },
                "required": ["file"],
            },
        ),

        # ── 5. Check conda env info ────────────────
        types.Tool(
            name="env_info",
            description="Show Python version, pip list, and working directory for the configured conda env.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


# ══════════════════════════════════════════════
#  TOOL HANDLERS
# ══════════════════════════════════════════════

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    # ── 1. run_python_file ─────────────────────
    if name == "run_python_file":
        file    = arguments["file"]
        args    = arguments.get("args", "")
        cwd     = arguments.get("cwd", "") or BASE_PATH

        # Resolve file path
        if not os.path.isabs(file):
            file_path = os.path.join(BASE_PATH, file)
        else:
            file_path = file

        if not os.path.exists(file_path):
            return [types.TextContent(
                type="text",
                text=f"❌ File not found: {file_path}"
            )]

        command = f"python \"{file_path}\" {args}".strip()
        result  = _conda_run(command, cwd=cwd)

        text = (
            f"▶ conda activate {CONDA_ENV}\n"
            f"▶ python {file} {args}\n"
            f"📁 cwd: {cwd}\n\n"
            + _fmt(result)
        )
        return [types.TextContent(type="text", text=text)]

    # ── 2. run_command ─────────────────────────
    elif name == "run_command":
        command = arguments["command"]
        cwd     = arguments.get("cwd", "") or BASE_PATH

        result = _conda_run(command, cwd=cwd)

        text = (
            f"▶ conda activate {CONDA_ENV}\n"
            f"▶ {command}\n"
            f"📁 cwd: {cwd}\n\n"
            + _fmt(result)
        )
        return [types.TextContent(type="text", text=text)]

    # ── 3. read_log ────────────────────────────
    elif name == "read_log":
        file    = arguments["file"]
        tail    = int(arguments.get("tail", 100))
        keyword = arguments.get("keyword", "").lower()

        # List mode
        if file.strip().lower() == "list":
            pattern  = os.path.join(BASE_PATH, "**", "*.log")
            log_files = glob.glob(pattern, recursive=True)
            if not log_files:
                return [types.TextContent(type="text", text="No .log files found under BASE_PATH.")]
            listing = "\n".join(
                os.path.relpath(f, BASE_PATH) for f in sorted(log_files)
            )
            return [types.TextContent(type="text", text=f"📋 Log files found:\n{listing}")]

        # Resolve path
        if not os.path.isabs(file):
            file_path = os.path.join(BASE_PATH, file)
        else:
            file_path = file

        if not os.path.exists(file_path):
            return [types.TextContent(
                type="text",
                text=f"❌ Log file not found: {file_path}"
            )]

        # Read file
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return [types.TextContent(type="text", text=f"❌ Error reading file: {e}")]

        # Apply keyword filter
        if keyword:
            lines = [l for l in lines if keyword in l.lower()]

        # Apply tail
        if tail > 0:
            lines = lines[-tail:]

        total    = len(lines)
        content  = "".join(lines) if lines else "(empty)"
        mod_time = datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M:%S")

        header = (
            f"📄 {os.path.relpath(file_path, BASE_PATH)}\n"
            f"🕒 Last modified: {mod_time}  |  Lines shown: {total}"
            + (f"  |  Filter: '{keyword}'" if keyword else "")
            + "\n" + "─" * 60 + "\n"
        )
        return [types.TextContent(type="text", text=header + content)]

    # ── 4. start_server ────────────────────────
    elif name == "start_server":
        file         = arguments["file"]
        args         = arguments.get("args", "")
        log_output   = arguments.get("log_output_to", "")

        if not os.path.isabs(file):
            file_path = os.path.join(BASE_PATH, file)
        else:
            file_path = file

        if not os.path.exists(file_path):
            return [types.TextContent(
                type="text",
                text=f"❌ File not found: {file_path}"
            )]

        # Build the command string
        py_cmd = f"python \"{file_path}\" {args}".strip()

        # Resolve log output path
        stdout_dest = subprocess.DEVNULL
        stderr_dest = subprocess.DEVNULL
        log_msg     = "stdout/stderr: discarded"

        if log_output:
            if not os.path.isabs(log_output):
                log_output = os.path.join(BASE_PATH, log_output)
            os.makedirs(os.path.dirname(log_output) or BASE_PATH, exist_ok=True)
            log_file    = open(log_output, "a", encoding="utf-8")
            stdout_dest = log_file
            stderr_dest = log_file
            log_msg     = f"stdout/stderr → {log_output}"

        # Launch in background
        if sys.platform == "win32":
            full_cmd = f'conda activate {CONDA_ENV} && {py_cmd}'
            proc = subprocess.Popen(
                full_cmd,
                shell=True,
                stdout=stdout_dest,
                stderr=stderr_dest,
                cwd=BASE_PATH,
            )
        else:
            proc = subprocess.Popen(
                ["conda", "run", "-n", CONDA_ENV, "bash", "-c", py_cmd],
                stdout=stdout_dest,
                stderr=stderr_dest,
                cwd=BASE_PATH,
            )

        text = (
            f"🚀 Started background process\n"
            f"   File : {file}\n"
            f"   Args : {args or '(none)'}\n"
            f"   PID  : {proc.pid}\n"
            f"   Logs : {log_msg}\n"
            f"   Env  : {CONDA_ENV}"
        )
        return [types.TextContent(type="text", text=text)]

    # ── 5. env_info ────────────────────────────
    elif name == "env_info":
        checks = [
            ("Python version", "python --version"),
            ("Pip packages",   "pip list"),
            ("Working dir",    "python -c \"import os; print(os.getcwd())\""),
        ]
        output_lines = [f"🐍 Conda env: {CONDA_ENV}\n📁 BASE_PATH: {BASE_PATH}\n"]
        for label, cmd in checks:
            r = _conda_run(cmd)
            output_lines.append(f"── {label} ──\n{r['stdout'] or r['stderr']}\n")

        return [types.TextContent(type="text", text="\n".join(output_lines))]

    else:
        return [types.TextContent(type="text", text=f"❌ Unknown tool: {name}")]


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )

if __name__ == "__main__":
    asyncio.run(main())
"""
FastMCP CMD Runner
Same features:
- run python file
- run command
- read logs
- start background server
- env info
"""

import subprocess
import os
import sys
import glob
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
BASE_PATH = Path("C:/Users/User/OneDrive/Desktop/Test-Run").resolve()
CONDA_ENV = "learning"

mcp = FastMCP("cmd-runner")


# ─────────────────────────────
# CORE HELPERS
# ─────────────────────────────
def _conda_run(command: str, cwd: str | None = None):
    work_dir = cwd or BASE_PATH

    if sys.platform == "win32":
        full_cmd = f'cmd /c "conda activate {CONDA_ENV} && {command}"'
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, text=True, cwd=work_dir
        )
    else:
        result = subprocess.run(
            ["conda", "run", "-n", CONDA_ENV, "bash", "-c", command],
            capture_output=True, text=True, cwd=work_dir
        )

    return {
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "returncode": result.returncode,
    }


def _fmt(r):
    out = [f"Return code: {r['returncode']}"]
    if r["stdout"]:
        out += ["", "── STDOUT ──", r["stdout"]]
    if r["stderr"]:
        out += ["", "── STDERR ──", r["stderr"]]
    return "\n".join(out)


# ─────────────────────────────
# TOOLS (AUTO-REGISTERED)
# ─────────────────────────────

@mcp.tool()
def run_python_file(file: str, args: str = "", cwd: str = ""):
    cwd = cwd or str(BASE_PATH)

    file_path = file if os.path.isabs(file) else os.path.join(BASE_PATH, file)

    if not os.path.exists(file_path):
        return f"❌ File not found: {file_path}"

    cmd = f'python "{file_path}" {args}'.strip()
    result = _conda_run(cmd, cwd)

    return (
        f"▶ python {file} {args}\n"
        f"📁 cwd: {cwd}\n\n"
        + _fmt(result)
    )


@mcp.tool()
def run_command(command: str, cwd: str = ""):
    cwd = cwd or str(BASE_PATH)
    result = _conda_run(command, cwd)

    return (
        f"▶ {command}\n"
        f"📁 cwd: {cwd}\n\n"
        + _fmt(result)
    )


@mcp.tool()
def read_log(file: str, tail: int = 100, keyword: str = ""):
    keyword = keyword.lower()

    # list logs
    if file.lower().strip() == "list":
        logs = glob.glob(str(BASE_PATH / "**/*.log"), recursive=True)
        if not logs:
            return "No .log files found"
        return "\n".join(os.path.relpath(f, BASE_PATH) for f in logs)

    file_path = file if os.path.isabs(file) else os.path.join(BASE_PATH, file)

    if not os.path.exists(file_path):
        return f"❌ Log file not found: {file_path}"

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    if keyword:
        lines = [l for l in lines if keyword in l.lower()]

    if tail > 0:
        lines = lines[-tail:]

    content = "".join(lines) if lines else "(empty)"

    mod_time = datetime.fromtimestamp(
        os.path.getmtime(file_path)
    ).strftime("%Y-%m-%d %H:%M:%S")

    return (
        f"📄 {os.path.relpath(file_path, BASE_PATH)}\n"
        f"🕒 {mod_time}\n"
        f"{'-'*50}\n"
        + content
    )


@mcp.tool()
def start_server(file: str, args: str = "", log_output_to: str = ""):
    file_path = file if os.path.isabs(file) else os.path.join(BASE_PATH, file)

    if not os.path.exists(file_path):
        return f"❌ File not found: {file_path}"

    py_cmd = f'python "{file_path}" {args}'.strip()

    stdout_dest = subprocess.DEVNULL
    stderr_dest = subprocess.DEVNULL

    if log_output_to:
        log_path = (
            log_output_to
            if os.path.isabs(log_output_to)
            else os.path.join(BASE_PATH, log_output_to)
        )
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
        stdout_dest = stderr_dest = log_file

    if sys.platform == "win32":
        cmd = f'conda activate {CONDA_ENV} && {py_cmd}'
        proc = subprocess.Popen(cmd, shell=True, cwd=BASE_PATH,
                                stdout=stdout_dest, stderr=stderr_dest)
    else:
        proc = subprocess.Popen(
            ["conda", "run", "-n", CONDA_ENV, "bash", "-c", py_cmd],
            cwd=BASE_PATH,
            stdout=stdout_dest,
            stderr=stderr_dest,
        )

    return f"🚀 Started PID: {proc.pid}"


@mcp.tool()
def env_info():
    checks = [
        ("Python", "python --version"),
        ("Pip", "pip list"),
        ("CWD", 'python -c "import os; print(os.getcwd())"'),
    ]

    out = [f"Env: {CONDA_ENV}\nBASE: {BASE_PATH}\n"]

    for name, cmd in checks:
        r = _conda_run(cmd)
        out.append(f"── {name} ──\n{r['stdout'] or r['stderr']}\n")

    return "\n".join(out)


# ─────────────────────────────
# ENTRY
# ─────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="stdio")
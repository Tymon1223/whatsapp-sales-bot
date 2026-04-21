from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STDOUT_LOG = ROOT / "bot_stdout.log"
STDERR_LOG = ROOT / "bot_stderr.log"
PID_FILE = ROOT / "bot.pid"


def _stop_existing_project_bots() -> None:
    if os.name != "nt":
        return

    root = str(ROOT).replace("'", "''")
    command = rf"""
$root = '{root}'
Get-CimInstance Win32_Process |
  Where-Object {{
    $_.Name -match '^python' -and
    $_.CommandLine -like "*$root*" -and
    $_.CommandLine -like "*main.py*"
  }} |
  ForEach-Object {{
    try {{
      Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
    }} catch {{
    }}
  }}
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    python_exe = sys.executable
    env = os.environ.copy()
    for proxy_name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GIT_HTTP_PROXY",
        "GIT_HTTPS_PROXY",
    ):
        if "127.0.0.1:9" in env.get(proxy_name, ""):
            env.pop(proxy_name, None)

    stdout_handle = STDOUT_LOG.open("w", encoding="utf-8")
    stderr_handle = STDERR_LOG.open("w", encoding="utf-8")
    _stop_existing_project_bots()

    if PID_FILE.exists():
        existing_pid = PID_FILE.read_text(encoding="utf-8").strip()
        if existing_pid:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", existing_pid, "/T", "/F"],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                else:
                    os.kill(int(existing_pid), 15)
            except Exception:
                pass

    popen_kwargs = {
        "args": [python_exe, "-u", str(ROOT / "main.py")],
        "cwd": ROOT,
        "env": env,
        "stdout": stdout_handle,
        "stderr": stderr_handle,
    }

    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )

    process = subprocess.Popen(**popen_kwargs)
    time.sleep(2)
    return_code = process.poll()
    if return_code is not None:
        stdout_handle.close()
        stderr_handle.close()
        error_tail = STDERR_LOG.read_text(encoding="utf-8", errors="ignore").strip()[-1000:]
        print("Bot failed to start")
        if error_tail:
            print(error_tail)
        return return_code

    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    print(f"Bot started with PID {process.pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

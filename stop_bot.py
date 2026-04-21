from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
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
    _stop_existing_project_bots()

    if not PID_FILE.exists():
        print("No bot.pid file found")
        return 0

    pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=True,
                capture_output=True,
                text=True,
            )
            print(f"Stopped PID {pid}")
        else:
            os.kill(pid, signal.SIGTERM)
            print(f"Stop signal sent to PID {pid}")
    except ProcessLookupError:
        print(f"Process {pid} is not running")
    except subprocess.CalledProcessError as error:
        print(f"Failed to stop process {pid}: {error.stderr.strip() or error.stdout.strip()}")
        return 1
    except Exception as error:
        print(f"Failed to stop process {pid}: {error}")
        return 1
    finally:
        Path(PID_FILE).unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

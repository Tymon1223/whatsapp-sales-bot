from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
PID_FILE = ROOT / "bot.pid"
STDOUT_LOG = ROOT / "bot_stdout.log"
STDERR_LOG = ROOT / "bot_stderr.log"


def tail(path: Path, count: int = 20) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-count:]


def main() -> int:
    if PID_FILE.exists():
        print(f"PID: {PID_FILE.read_text(encoding='utf-8').strip()}")
    else:
        print("PID: not found")

    print("--- stdout ---")
    for line in tail(STDOUT_LOG):
        print(line)

    print("--- stderr ---")
    for line in tail(STDERR_LOG):
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

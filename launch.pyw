"""Start the local panel once; optional browser opening. No model calls."""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

root = Path(__file__).resolve().parent
url = "http://127.0.0.1:19876/"
parser = argparse.ArgumentParser()
parser.add_argument("--no-browser", action="store_true")
parser.add_argument("--codex-home", type=Path)
args = parser.parse_args()


def healthy():
    try:
        with urllib.request.urlopen(url + "api/health", timeout=0.6) as response:
            return json.load(response).get("app") == "codex-speed-panel"
    except (OSError, ValueError):
        return False


if not healthy():
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    executable = Path(sys.executable).with_name("pythonw.exe")
    if not executable.exists():
        executable = Path(sys.executable)
    with (runtime / "server.log").open("a", encoding="utf-8") as out, (runtime / "error.log").open("a", encoding="utf-8") as err:
        command = [str(executable), str(root / "server.py")]
        if args.codex_home is not None:
            command += ["--codex-home", str(args.codex_home)]
        process = subprocess.Popen(command, cwd=root, stdout=out, stderr=err,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (runtime / "server.pid").write_text(str(process.pid), encoding="ascii")
    for _ in range(40):
        if healthy():
            break
        if process.poll() is not None:
            break
        time.sleep(0.15)
    if not healthy():
        raise SystemExit("Speed panel could not start. Check .runtime/error.log.")
if not args.no_browser:
    webbrowser.open(url)

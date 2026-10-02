"""Windows executable entry: start the local panel and open its browser UI."""
import argparse
import ctypes
import http.client
import io
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from server import public_error, serve


def ready(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=.5)
    try:
        connection.request("GET", "/api/health")
        return json.loads(connection.getresponse().read()).get("app") == "codex-speed-panel"
    except (OSError, ValueError):
        return False
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19876)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/" + ("?demo=1" if args.demo else "")
    if args.stop:
        if ready(args.port):
            connection = http.client.HTTPConnection("127.0.0.1", args.port, timeout=2)
            try:
                connection.request("POST", "/api/stop")
                connection.getresponse().read()
            finally:
                connection.close()
        return
    if ready(args.port):
        if not args.no_browser:
            webbrowser.open(url)
        return
    if not args.no_browser:
        def open_when_ready():
            for _ in range(60):
                if ready(args.port):
                    webbrowser.open(url)
                    return
                time.sleep(.15)
        threading.Thread(target=open_when_ready, daemon=True).start()
    serve(args.codex_home, args.port)


if __name__ == "__main__":
    # Windowed executables have no terminal streams.
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()
    try:
        main()
    except Exception as error:
        if os.name == "nt" and "--no-browser" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(0, public_error(error), "Codex Token 速度面板", 0x10)
        raise SystemExit(1) from None

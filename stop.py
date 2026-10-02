"""Ask this local panel to stop itself; never terminate unrelated processes."""
import json
import urllib.request

url = "http://127.0.0.1:19876/"
try:
    with urllib.request.urlopen(url + "api/health", timeout=2) as response:
        ours = json.load(response).get("app") == "codex-speed-panel"
    if ours:
        with urllib.request.urlopen(urllib.request.Request(url + "api/stop", method="POST"), timeout=2) as response:
            response.read()
except OSError:
    pass

"""Small, local, read-only Codex response monitor. Python standard library only."""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from data_source import discover_home, find_database, readonly_db

ROOT = Path(__file__).resolve().parent
VERSION = "0.2.0"
UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
ITEM = re.compile(r'item_type="([^"]+)" item_id="([^"]+)"')
MODEL = re.compile(r'run_sampling_request\{[^}]*model=([^ }]+)')


def public_error(error):
    """Return a useful message without exposing exception payloads or file paths."""
    messages = {
        "找不到 Codex 会话索引。",
        "请先选择要查看的聊天。",
        "这条聊天的本地记录不存在。",
        "会话编号格式不正确。",
        "请选择可用的 Codex 数据目录。",
        "数据目录格式不正确。",
    }
    if isinstance(error, (ValueError, RuntimeError)) and str(error) in messages:
        return str(error)
    if isinstance(error, FileNotFoundError):
        return "找不到需要的本地记录，请确认 Codex 已运行并检查数据目录。"
    if isinstance(error, PermissionError):
        return "无法读取本地记录，请检查数据目录的访问权限。"
    if isinstance(error, sqlite3.Error):
        return "无法读取 Codex 数据库，请确认 Codex 已运行并检查数据目录。"
    return "暂时无法读取本地数据，请确认 Codex 已启动后重试。"


def read_events(path: Path, limit=4 * 1024 * 1024):
    """Bounded tail: skip a cut-off first line and an unfinished last line."""
    with path.open("rb") as file:
        size = file.seek(0, 2)
        start = max(0, size - limit)
        file.seek(start)
        raw = file.read(limit)
    if start:
        raw = raw.partition(b"\n")[2]
    if not raw.endswith(b"\n"):
        raw = raw.rpartition(b"\n")[0]
    events = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
            if event.get("type") in ("event_msg", "turn_context"):
                events.append(event)
        except (ValueError, UnicodeDecodeError):
            continue
    return events


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def token_usage(value):
    """Keep missing fields unknown and reject context-size placeholder events."""
    if not isinstance(value, dict):
        return None
    def count(key):
        result = value.get(key)
        return result if isinstance(result, int) and not isinstance(result, bool) and result >= 0 else None
    output, input_count, total = count("output_tokens"), count("input_tokens"), count("total_tokens")
    if output is None:
        return None
    if input_count is not None:
        if total is not None and total != input_count + output:
            return None
        total = input_count + output
    return dict(input=input_count, cached=count("cached_input_tokens"), output=output,
                reasoning=count("reasoning_output_tokens"), total=total)


def token_updates(events):
    """A repeated cumulative counter is a notification, not another request."""
    updates, previous = {}, None
    for index, event in enumerate(events):
        payload = event.get("payload", {})
        if event.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        cumulative = info.get("total_token_usage")
        key = json.dumps(cumulative, sort_keys=True) if cumulative else None
        repeated = key is not None and key == previous
        if key is not None:
            previous = key
        usage = token_usage(info.get("last_token_usage"))
        if usage is not None and not repeated and stamp(event.get("timestamp")) is not None:
            updates[index] = usage
    return updates


def summarize_turns(events, updates):
    """Sum completed request usage between user-turn boundaries, independently of timing logs."""
    turns, current = [], None
    for index, event in enumerate(events):
        payload = event.get("payload", {})
        ts = stamp(event.get("timestamp"))
        if ts is None:
            continue
        kind, turn_id = payload.get("type"), payload.get("turn_id")
        beginning = event.get("type") == "event_msg" and kind in ("task_started", "turn_started")
        context = event.get("type") == "turn_context" and turn_id is not None
        if context and current is not None and current["id"] is None and current["requests"] == 0:
            current["id"] = turn_id
        if beginning or (context and (current is None or current["id"] != turn_id)):
            if current is None or current["id"] != turn_id or turn_id is None:
                current = dict(id=turn_id, start=ts, at=ts, complete=False, aborted=False,
                               partial=not beginning, requests=0, input=0, cached=0,
                               output=0, reasoning=0, total=0)
                turns.append(current)
        if index in updates:
            if current is None:
                current = dict(id=None, start=ts, at=ts, complete=False, aborted=False,
                               partial=True, requests=0, input=0, cached=0,
                               output=0, reasoning=0, total=0)
                turns.append(current)
            current["requests"] += 1
            current["at"] = ts
            for key, value in updates[index].items():
                current[key] = current[key] + value if current[key] is not None and value is not None else None
        if (event.get("type") == "event_msg" and
                kind in ("task_complete", "task_completed", "turn_complete", "turn_completed", "turn_aborted") and
                current is not None and (turn_id is None or current["id"] == turn_id)):
            current.update(at=ts, complete=True, aborted=kind == "turn_aborted")
    return turns[-40:]


def analyze(rows, events, now=None):
    """Pair usage with its sampling request; stop timing BEFORE tool execution."""
    now = time.time() if now is None else now
    requests = []
    for log_id, sec, nanos, target, body in sorted(rows, key=lambda r: (r[1], r[2], r[0])):
        ts = sec + nanos / 1e9
        body = body or ""
        if target == "feedback_tags" and 'endpoint="/responses"' in body and "run_sampling_request{" in body and "websocket.warmup=true" not in body:
            model = MODEL.search(body)
            requests.append(dict(start=ts, end=None, model=model.group(1) if model else None, items={}, tool=False))
        elif target == "codex_core::stream_events_utils" and requests:
            request = requests[-1]
            done = 'from="output_item_done"' in body
            item = ITEM.search(body)
            if item:
                record = request["items"].setdefault(item[2], {"type": item[1]})
                record["end" if done else "start"] = ts
            if done:
                request["end"] = ts
                request["tool"] |= "ToolCall:" in body

    starts = [r["start"] for r in requests]
    started = finished = 0
    model = effort = None
    cumulative = None
    updates = token_updates(events)
    for event in events:
        payload = event.get("payload", {})
        ts = stamp(event.get("timestamp"))
        if ts is None:
            continue
        if event["type"] == "turn_context":
            model = payload.get("model", model)
            effort = payload.get("effort", payload.get("reasoning_effort", effort))
        elif payload.get("type") in ("task_started", "turn_started"):
            started = ts
        elif payload.get("type") in ("task_complete", "task_completed", "turn_complete", "turn_completed", "turn_aborted"):
            finished = ts
        elif payload.get("type") == "token_count" and payload.get("info"):
            info = payload["info"]
            cumulative = info.get("total_token_usage", cumulative)
    for index, usage in updates.items():
        ts = stamp(events[index]["timestamp"])
        pos = bisect.bisect_right(starts, ts) - 1
        if pos >= 0 and requests[pos].get("end") is not None and requests[pos]["end"] <= ts:
            requests[pos]["usage"] = usage

    samples = []
    for request in requests:
        usage = request.get("usage")
        if not usage or request["end"] is None:
            continue
        seconds = request["end"] - request["start"]
        output = usage["output"]
        reasoning = usage["reasoning"]
        if seconds <= 0 or not isinstance(output, (int, float)) or output < 0:
            continue
        sample = dict(at=request["end"], model=request["model"] or model,
                      seconds=round(seconds, 3), output=output, reasoning=reasoning,
                      visible=max(0, output - reasoning) if reasoning is not None else None,
                      input=usage["input"], cached=usage["cached"], total=usage["total"],
                      tps=round(output / seconds, 2), text_tps=None)
        visible_items = [i for i in request["items"].values() if i["type"] != "reasoning"]
        if not request["tool"] and len(visible_items) == 1:
            item = visible_items[0]
            duration = item.get("end", 0) - item.get("start", 0)
            if item["type"] == "message" and "start" in item and "end" in item and duration > 0 and sample["visible"] is not None:
                sample["text_tps"] = round(sample["visible"] / duration, 2)
        samples.append(sample)

    active = started > finished
    latest = requests[-1] if requests else None
    phase = "idle"
    elapsed = None
    if active and latest and latest["start"] >= started:
        if latest["end"] is None:
            phase = "generating"
            elapsed = max(0, now - latest["start"])
        elif not latest.get("usage") and latest["tool"]:
            phase = "tools"
            elapsed = max(0, now - latest["end"])
        else:
            phase = "waiting"
    recent = samples[-10:]
    duration = sum(s["seconds"] for s in recent)
    plain = next((s for s in reversed(samples) if s["text_tps"] is not None), None)
    return dict(samples=samples[-40:], latest=samples[-1] if samples else None,
                average=round(sum(s["output"] for s in recent) / duration, 2) if duration else None,
                average_count=len(recent), plain=plain, model=(latest or {}).get("model") or model,
                effort=effort, phase=phase, elapsed=round(elapsed, 1) if elapsed is not None else None,
                total_output=(cumulative or {}).get("output_tokens"), total_usage=token_usage(cumulative),
                turns=summarize_turns(events, updates), active=active)


class Monitor:
    def __init__(self, home=None, default_thread=None):
        self.source = discover_home(home)
        self.home = self.source.directory if self.source.ready else None
        self.default_thread = default_thread
        self.lock = threading.RLock()
        self.cache = {}
        self.threads_cache = (0, [])

    def source_info(self):
        with self.lock:
            return self.source.public()

    def configure(self, directory=None):
        source = discover_home(directory)
        with self.lock:
            self.source = source
            self.home = source.directory if source.ready else None
            self.cache.clear()
            self.threads_cache = (0, [])
            return source.public()

    def threads(self):
        with self.lock:
            return self._threads()

    def _threads(self):
        if self.home is None:
            raise ValueError("请选择可用的 Codex 数据目录。")
        if time.time() - self.threads_cache[0] < 10:
            return self.threads_cache[1]
        names = {}
        index = self.home / "session_index.jsonl"
        if index.exists():
            for event in read_index(index):
                names[event.get("id")] = event.get("thread_name")
        database = find_database(self.home, "state")
        if database is None:
            raise RuntimeError("找不到 Codex 会话索引。")
        with closing(readonly_db(database)) as conn:
            rows = conn.execute("SELECT id,title,rollout_path,updated_at FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT 100").fetchall()
            if self.default_thread and self.default_thread not in {r[0] for r in rows}:
                rows += conn.execute("SELECT id,title,rollout_path,updated_at FROM threads WHERE id=?", (self.default_thread,)).fetchall()
        result = [dict(id=r[0], title=names.get(r[0]) or r[1] or "未命名会话", path=r[2], updated=r[3])
                  for r in rows if names.get(r[0]) or r[1] or r[0] == self.default_thread]
        self.threads_cache = (time.time(), result)
        return result

    def snapshot(self, thread=None):
        with self.lock:
            thread = thread or self.default_thread
            if not thread:
                raise ValueError("请先选择要查看的聊天。")
            threads = self.threads()
            metadata = next((t for t in threads if t["id"] == thread), None)
            if metadata is None:
                with closing(readonly_db(find_database(self.home, "state"))) as conn:
                    row = conn.execute("SELECT id,title,rollout_path,updated_at FROM threads WHERE id=?", (thread,)).fetchone()
                if row is None:
                    raise ValueError("这条聊天的本地记录不存在。")
                metadata = dict(id=row[0], title=row[1] or "当前聊天", path=row[2], updated=row[3])
            log_database = find_database(self.home, "logs")
            if log_database is None:
                raise FileNotFoundError()
            files = (Path(metadata["path"]), log_database, Path(str(log_database) + "-wal"))
            def file_stamp(path):
                try:
                    info = path.stat()
                    return (str(path), info.st_size, info.st_mtime_ns)
                except FileNotFoundError:
                    return (str(path), None, None)
            signature = (metadata["title"], tuple(file_stamp(path) for path in files))
            now = time.time()
            cached = self.cache.get(thread)
            if cached is not None and cached[2] == signature:
                result = dict(cached[1], updated=now)
                if result.get("elapsed") is not None:
                    result["elapsed"] = round(result["elapsed"] + max(0, now-cached[0]), 1)
                return result
            with closing(readonly_db(log_database)) as conn:
                rows = conn.execute("SELECT id,ts,ts_nanos,target,feedback_log_body FROM logs WHERE thread_id=? ORDER BY ts DESC,ts_nanos DESC,id DESC LIMIT 2400", (thread,)).fetchall()
            result = analyze(rows, read_events(Path(metadata["path"])), now=now)
            result.update(thread=dict(id=thread, title=metadata["title"]),
                          scope="single-chat", updated=now)
            self.cache.pop(thread, None)
            self.cache[thread] = (now, result, signature)
            while len(self.cache) > 8:
                self.cache.pop(next(iter(self.cache)))
            return result


def read_index(path):
    with path.open("rb") as file:
        size = file.seek(0, 2)
        file.seek(max(0, size - 512 * 1024))
        raw = file.read(512 * 1024)
    result = []
    for line in raw.splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result


def demo_snapshot(now=None):
    """Synthetic documentation data; this function never accesses Codex files."""
    now = time.time() if now is None else now
    rates = [18.4, 21.6, 20.8, 24.1, 23.5, 27.3, 25.1, 29.4, 26.8, 28.2, 26.1, 24.8]
    samples = []
    for index, rate in enumerate(rates):
        seconds = 20 + index % 4 * 5
        output = round(rate * seconds)
        reasoning = round(output * .62)
        samples.append(dict(at=now - (len(rates) - 1 - index) * 90, model="示例模型",
                            seconds=seconds, output=output, reasoning=reasoning, visible=output-reasoning,
                            input=12000, cached=9000, total=12000+output, tps=rate, text_tps=None))
    recent = samples[-10:]
    thread = dict(id="00000000-0000-0000-0000-000000000001", title="示例会话 · 演示数据")
    turns = []
    for index in range(0, len(samples), 3):
        group = samples[index:index+3]
        turns.append(dict(id=None, start=group[0]["at"]-group[0]["seconds"], at=group[-1]["at"],
                          complete=True, aborted=False, partial=False, requests=len(group),
                          **{key: sum(s[key] for s in group) for key in ("input", "cached", "output", "reasoning", "total")}))
    return dict(demo=True, samples=samples, latest=samples[-1], average_count=len(recent),
                average=round(sum(s["output"] for s in recent)/sum(s["seconds"] for s in recent), 2),
                plain=None, model="示例模型", effort="medium", phase="demo", elapsed=None,
                active=False, thread=thread, threads=[thread], updated=now, turns=turns,
                total_usage={key: sum(s[key] for s in samples) for key in ("input", "cached", "output", "reasoning", "total")})


def serve(home, port):
    monitor = Monitor(home)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in allowed_hosts or (origin and urlparse(origin).netloc not in allowed_hosts):
                self.send_error(403)
                return
            if self.path not in ("/api/stop", "/api/source"):
                self.send_error(404)
                return
            if self.path == "/api/stop":
                self.reply(b'{"stopping":true}', "application/json")
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("数据目录格式不正确。")
                body = json.loads(self.rfile.read(length))
                directory = body.get("directory") if isinstance(body, dict) else None
                if not isinstance(directory, str) or "\x00" in directory:
                    raise ValueError("数据目录格式不正确。")
                data = monitor.configure(directory.strip() or None)
                self.reply(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")
            except (OSError, ValueError) as exc:
                self.reply(json.dumps(dict(error=public_error(exc)), ensure_ascii=False).encode("utf-8"), "application/json", 400)

        def do_GET(self):
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in allowed_hosts or (origin and urlparse(origin).netloc not in allowed_hosts):
                self.send_error(403)
                return
            route = urlparse(self.path)
            try:
                if route.path == "/api/health":
                    self.reply(json.dumps(dict(app="codex-speed-panel", version=VERSION)).encode(), "application/json")
                elif route.path == "/api/source":
                    self.reply(json.dumps(monitor.source_info(), ensure_ascii=False).encode("utf-8"), "application/json")
                elif route.path == "/api/context":
                    data = dict(thread_id=None, status="manual")
                    data["threads"] = [dict(id=t["id"], title=t["title"].replace("\n", " ")[:60]) for t in monitor.threads()[:20]]
                    self.reply(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")
                elif route.path == "/api/demo":
                    self.reply(json.dumps(demo_snapshot(), ensure_ascii=False).encode("utf-8"), "application/json")
                elif route.path == "/api/snapshot":
                    thread = parse_qs(route.query).get("thread", [None])[0]
                    if thread and not UUID.fullmatch(thread):
                        raise ValueError("会话编号格式不正确。")
                    data = monitor.snapshot(thread)
                    self.reply(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")
                elif route.path == "/favicon.ico":
                    self.reply(b"", "image/x-icon", 204)
                elif route.path in ("/", "/index.html", "/app.js", "/style.css"):
                    name = "index.html" if route.path == "/" else route.path[1:]
                    kind = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name]
                    self.reply((ROOT / name).read_bytes(), kind)
                else:
                    self.send_error(404)
            except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
                self.reply(json.dumps(dict(error=public_error(exc)), ensure_ascii=False).encode("utf-8"), "application/json", 503)

        def reply(self, content, kind, code=200):
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Codex speed panel: http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19876)
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args()
    serve(args.codex_home, args.port)

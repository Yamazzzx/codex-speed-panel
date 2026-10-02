import json
import http.client
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from server import Monitor, analyze, demo_snapshot, public_error, read_events, readonly_db, serve
from http.server import ThreadingHTTPServer
import server
from data_source import candidate_homes, discover_home, find_database, probe_home
import sqlite3
from contextlib import closing
import os
from unittest.mock import patch


def event(sec, payload):
    return dict(timestamp=datetime.fromtimestamp(sec, timezone.utc).isoformat(), type="event_msg", payload=payload)


def start(sec):
    return (sec, sec, 0, "feedback_tags", 'run_sampling_request{model=gpt-test}:websocket.warmup=false: endpoint="/responses"')


def item(sec, kind, item_id, done=False):
    return (sec, sec, 0, "codex_core::stream_events_utils", ('from="output_item_done" ' if done else '') + f'item_type="{kind}" item_id="{item_id}"')


def usage(sec, output, reasoning=0):
    return event(sec, dict(type="token_count", info=dict(last_token_usage=dict(output_tokens=output, reasoning_output_tokens=reasoning, input_tokens=100000), total_token_usage=dict(output_tokens=output))))


def metered_usage(sec, input_count, cached, output, reasoning, cumulative=None):
    counts = dict(input_tokens=input_count, cached_input_tokens=cached, output_tokens=output,
                  reasoning_output_tokens=reasoning, total_tokens=input_count+output)
    return event(sec, dict(type="token_count", info=dict(last_token_usage=counts, total_token_usage=cumulative or counts)))


def fixture_home(directory, logs_version=2):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    thread = "00000000-0000-0000-0000-000000000002"
    rollout = directory / "fixture.jsonl"
    rollout.write_text(json.dumps(metered_usage(111, 1000, 800, 200, 120)) + "\n", encoding="utf-8")
    with closing(sqlite3.connect(directory / "state_1.sqlite")) as connection:
        connection.execute("CREATE TABLE threads (id TEXT,title TEXT,rollout_path TEXT,updated_at INTEGER,archived INTEGER)")
        connection.execute("INSERT INTO threads VALUES (?,?,?,?,0)", (thread, "Synthetic example", str(rollout), 111))
        connection.commit()
    with closing(sqlite3.connect(directory / f"logs_{logs_version}.sqlite")) as connection:
        connection.execute("CREATE TABLE logs (id INTEGER,ts INTEGER,ts_nanos INTEGER,target TEXT,feedback_log_body TEXT,thread_id TEXT)")
        for row in (start(100), item(110, "message", "fixture", True)):
            connection.execute("INSERT INTO logs VALUES (?,?,?,?,?,?)", (*row, thread))
        connection.commit()
    return directory, thread, rollout


class MonitorTests(unittest.TestCase):
    def test_turn_usage_sums_multiple_requests_without_double_counting_subsets_or_notifications(self):
        first = metered_usage(111, 1000, 800, 100, 70)
        duplicate = dict(first, timestamp=event(112, {})["timestamp"])
        second = metered_usage(141, 1200, 1000, 200, 100,
                               dict(input_tokens=2200, cached_input_tokens=1800, output_tokens=300,
                                    reasoning_output_tokens=170, total_tokens=2500))
        events = [event(99, dict(type="task_started", turn_id="example-a")), first, duplicate,
                  second, event(145, dict(type="task_complete", turn_id="example-a"))]
        rows = [start(100), item(110, "message", "a", True), start(120), item(140, "message", "b", True)]
        data = analyze(rows, events)
        turn = data["turns"][0]
        self.assertEqual((turn["requests"], turn["input"], turn["cached"], turn["output"], turn["reasoning"], turn["total"]),
                         (2, 2200, 1800, 300, 170, 2500))
        self.assertTrue(turn["complete"])
        self.assertFalse(turn["partial"])
        self.assertEqual(data["latest"]["total"], 1400)
        self.assertEqual(data["total_usage"]["total"], 2500)

    def test_turn_boundaries_keep_each_dialogue_separate_and_current_turn_pending(self):
        events = [event(99, dict(type="task_started", turn_id="example-a")),
                  metered_usage(111, 1000, 800, 100, 70),
                  event(115, dict(type="task_complete", turn_id="example-a")),
                  event(120, dict(type="task_started", turn_id="example-b"))]
        data = analyze([], events)
        self.assertEqual(len(data["turns"]), 2)
        self.assertEqual(data["turns"][0]["total"], 1100)
        self.assertEqual(data["turns"][1]["requests"], 0)
        self.assertFalse(data["turns"][1]["complete"])

    def test_compaction_placeholder_does_not_erase_usage_or_split_a_turn(self):
        counted = metered_usage(111, 1000, 800, 100, 70)
        placeholder = event(112, dict(type="token_count", info=dict(
            last_token_usage=dict(input_tokens=0, cached_input_tokens=0, output_tokens=0,
                                  reasoning_output_tokens=0, total_tokens=37),
            total_token_usage=counted["payload"]["info"]["total_token_usage"])))
        context = dict(timestamp=event(113, {})["timestamp"], type="turn_context", payload=dict(turn_id="example-a"))
        data = analyze([start(100), item(110, "message", "a", True)],
                       [event(99, dict(type="task_started", turn_id="example-a")), counted, placeholder, context])
        self.assertEqual(data["latest"]["output"], 100)
        self.assertEqual(len(data["turns"]), 1)
        self.assertEqual(data["turns"][0]["total"], 1100)

    def test_turn_usage_survives_missing_speed_logs_and_marks_a_truncated_boundary(self):
        data = analyze([], [metered_usage(111, 1000, 800, 100, 70)])
        self.assertIsNone(data["latest"])
        self.assertEqual(data["turns"][0]["total"], 1100)
        self.assertTrue(data["turns"][0]["partial"])

    def test_missing_cached_usage_stays_unknown(self):
        data = analyze([start(100), item(110, "message", "a", True)], [usage(111, 100)])
        self.assertIsNone(data["latest"]["cached"])
        self.assertIsNone(data["turns"][0]["cached"])

    def test_tool_wait_is_excluded_and_input_never_counted_as_output(self):
        rows = [start(100), item(101, "custom_tool_call", "t"), (3, 110, 0, "codex_core::stream_events_utils", 'from="output_item_done" ToolCall: exec')]
        data = analyze(rows, [usage(170, 200, 50)])
        self.assertEqual(data["latest"]["tps"], 20)
        self.assertEqual(data["latest"]["visible"], 150)
        self.assertIsNone(data["plain"])

    def test_plain_output_excludes_reasoning_and_first_output_wait(self):
        rows = [start(100), item(102, "reasoning", "r"), item(110, "reasoning", "r", True), item(110, "message", "m"), item(115, "message", "m", True)]
        data = analyze(rows, [usage(116, 300, 200)])
        self.assertEqual(data["plain"]["text_tps"], 20)
        self.assertEqual(data["latest"]["tps"], 20)

    def test_new_request_cannot_inherit_old_usage(self):
        rows = [start(100), item(101, "message", "m"), item(110, "message", "m", True), start(120)]
        data = analyze(rows, [event(99, {"type": "task_started"}), usage(111, 200)], now=130)
        self.assertEqual(len(data["samples"]), 1)
        self.assertEqual(data["phase"], "generating")
        self.assertEqual(data["elapsed"], 10)

    def test_duplicate_usage_and_missing_duration(self):
        rows = [start(100), item(105, "message", "m", True)]
        data = analyze(rows, [usage(106, 100), usage(107, 100)])
        self.assertEqual(len(data["samples"]), 1)
        self.assertIsNone(data["plain"])
        self.assertIsNone(analyze([start(100)], [usage(102, 100)])["latest"])

    def test_completed_turn_is_idle(self):
        data = analyze([start(100)], [event(99, {"type": "task_started"}), event(110, {"type": "task_complete"})], now=120)
        self.assertEqual(data["phase"], "idle")

    def test_average_is_weighted_by_model_duration(self):
        rows = [start(100), item(110, "message", "a", True), start(120), item(140, "message", "b", True)]
        data = analyze(rows, [usage(111, 1000), usage(141, 100)])
        self.assertEqual(data["average"], 36.67)

    def test_tail_discards_partial_and_malformed_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rollout.jsonl"
            record = event(100, {"type": "task_started"})
            path.write_bytes(b'{bad}\n' + json.dumps(record).encode() + b'\n{"unfinished":')
            self.assertEqual(read_events(path), [record])

    def test_readonly_open_does_not_create_a_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.sqlite"
            with self.assertRaises(Exception):
                readonly_db(path)
            self.assertFalse(path.exists())

    def test_selected_session_ignores_a_more_recent_chat(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            session_id = "00000000-0000-0000-0000-000000000002"
            rollout = home / "example.jsonl"
            rollout.write_text(json.dumps(usage(111, 200)) + "\n", encoding="utf-8")
            with closing(sqlite3.connect(home / "state_1.sqlite")) as conn:
                conn.execute("CREATE TABLE threads (id TEXT,title TEXT,rollout_path TEXT,updated_at INTEGER,archived INTEGER)")
                conn.execute("INSERT INTO threads VALUES (?,?,?,?,0)", (session_id,"Synthetic example",str(rollout),111))
                other_id = "00000000-0000-0000-0000-000000000003"
                other = home / "other.jsonl"
                other.write_text(json.dumps(usage(211, 900)) + "\n", encoding="utf-8")
                conn.execute("INSERT INTO threads VALUES (?,?,?,?,0)", (other_id,"Other window",str(other),211))
                conn.commit()
            with closing(sqlite3.connect(home / "logs_2.sqlite")) as conn:
                conn.execute("CREATE TABLE logs (id INTEGER,ts INTEGER,ts_nanos INTEGER,target TEXT,feedback_log_body TEXT,thread_id TEXT)")
                for row in [start(100),item(110,"message","m",True)]:
                    conn.execute("INSERT INTO logs VALUES (?,?,?,?,?,?)", (*row,session_id))
                for row in [start(200),item(210,"message","n",True)]:
                    conn.execute("INSERT INTO logs VALUES (?,?,?,?,?,?)", (*row,other_id))
                conn.commit()
            with self.assertRaisesRegex(ValueError, "选择"):
                Monitor(home).snapshot()
            data = Monitor(home, session_id).snapshot()
            self.assertEqual(data["thread"]["id"], session_id)
            self.assertEqual(data["latest"]["tps"], 20)
            self.assertEqual(data["total_usage"]["output"], 200)
            self.assertEqual(data["scope"], "single-chat")
            self.assertNotIn(str(home),json.dumps(data))

    def test_demo_is_explicit_and_uses_only_synthetic_metadata(self):
        data = demo_snapshot(now=10000)
        self.assertTrue(data["demo"])
        self.assertEqual(data["phase"], "demo")
        self.assertEqual(data["thread"]["title"], "示例会话 · 演示数据")
        self.assertEqual(data["model"], "示例模型")






class SourceTests(unittest.TestCase):
    def test_default_directory_and_explicit_environment_are_resolved_without_broad_search(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            directory, _, _ = fixture_home(home / ".codex")
            self.assertEqual(discover_home(environ={}, user_home=home, platform="win32").directory, directory)
            configured, _, _ = fixture_home(home / "configured")
            self.assertEqual(candidate_homes({"CODEX_HOME": str(configured)}, home, "darwin"), [configured])
            self.assertEqual(discover_home(environ={"CODEX_HOME": str(configured)}, user_home=home).directory, configured)

    def test_macos_candidate_path_uses_the_same_readonly_database_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            directory, _, _ = fixture_home(home / "Library" / "Application Support" / "Codex")
            result = discover_home(environ={}, user_home=home, platform="darwin")
            self.assertTrue(result.ready)
            self.assertEqual(result.directory, directory)

    def test_configured_missing_directory_does_not_silently_switch_to_another_source(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            fixture_home(home / ".codex")
            result = discover_home(environ={"CODEX_HOME": str(home / "missing")}, user_home=home)
            self.assertFalse(result.ready)
            self.assertEqual(result.status, "missing")

    def test_empty_incompatible_and_unreadable_directories_are_rejected_without_creating_databases(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertEqual(probe_home(directory).status, "incomplete")
            self.assertEqual(list(directory.iterdir()), [])
            (directory / "state_1.sqlite").write_bytes(b"not a database")
            self.assertEqual(probe_home(directory).status, "unreadable")
            (directory / "state_1.sqlite").unlink()
            with closing(sqlite3.connect(directory / "state_1.sqlite")) as connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
            self.assertEqual(probe_home(directory).status, "unsupported")

    def test_directory_permission_failure_is_reported_without_exception_details(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(Path, "stat", side_effect=PermissionError("PRIVATE_FIXTURE_DETAILS")):
                result = probe_home(Path(folder))
            self.assertEqual(result.status, "permission")
            self.assertNotIn("PRIVATE_FIXTURE_DETAILS", result.message)

    def test_database_version_selection_is_numeric_and_ignores_unrelated_names(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, thread, _ = fixture_home(Path(folder), logs_version=12)
            (directory / "logs_2.sqlite").write_bytes(b"outdated fixture")
            (directory / "logs_backup.sqlite").write_bytes(b"unrelated fixture")
            self.assertEqual(find_database(directory, "logs").name, "logs_12.sqlite")
            self.assertTrue(probe_home(directory).ready)
            self.assertEqual(Monitor(directory, thread).snapshot()["latest"]["output"], 200)

    def test_manual_source_change_clears_previous_chat_metadata_and_cached_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            first, thread, _ = fixture_home(Path(folder) / "first")
            second, _, _ = fixture_home(Path(folder) / "second")
            monitor = Monitor(first, thread)
            monitor.snapshot()
            self.assertTrue(monitor.cache)
            self.assertTrue(monitor.configure(second)["ready"])
            self.assertFalse(monitor.cache)
            self.assertTrue(monitor.threads()[0]["path"].startswith(str(second)))
            self.assertFalse(monitor.configure(Path(folder) / "missing")["ready"])
            with self.assertRaisesRegex(ValueError, "数据目录"):
                monitor.threads()


class CacheTests(unittest.TestCase):
    def test_unchanged_files_reuse_statistics_but_a_changed_rollout_is_reparsed(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, thread, rollout = fixture_home(Path(folder))
            monitor = Monitor(directory, thread)
            with patch.object(server, "read_events", wraps=read_events) as reader:
                first = monitor.snapshot()
                monitor.snapshot()
                self.assertEqual(reader.call_count, 1)
                with rollout.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(usage(121, 300)) + "\n")
                changed = monitor.snapshot()
                self.assertEqual(reader.call_count, 2)
                self.assertEqual(first["total_usage"]["output"], 200)
                self.assertEqual(changed["total_usage"]["output"], 300)

    def test_wal_updates_invalidate_the_cache_without_relying_on_the_main_database_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, thread, _ = fixture_home(Path(folder))
            database = directory / "logs_2.sqlite"
            with closing(sqlite3.connect(database)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                monitor = Monitor(directory, thread)
                with patch.object(server, "analyze", wraps=analyze) as analyzer:
                    monitor.snapshot()
                    monitor.snapshot()
                    self.assertEqual(analyzer.call_count, 1)
                    before = database.stat().st_mtime_ns
                    writer.execute("INSERT INTO logs VALUES (?,?,?,?,?,?)", (*start(120), thread))
                    writer.commit()
                    self.assertEqual(database.stat().st_mtime_ns, before)
                    monitor.snapshot()
                    self.assertEqual(analyzer.call_count, 2)

    def test_cached_generating_elapsed_time_continues_to_advance(self):
        with tempfile.TemporaryDirectory() as folder:
            directory, thread, rollout = fixture_home(Path(folder))
            rollout.write_text(json.dumps(event(99, {"type": "task_started"})) + "\n", encoding="utf-8")
            with closing(sqlite3.connect(directory / "logs_2.sqlite")) as connection:
                connection.execute("DELETE FROM logs WHERE ts=110")
                connection.commit()
            monitor = Monitor(directory, thread)
            with patch.object(server.time, "time", return_value=200):
                self.assertEqual(monitor.snapshot()["elapsed"], 100)
            with patch.object(server.time, "time", return_value=210):
                self.assertEqual(monitor.snapshot()["elapsed"], 110)


class PrivacyTests(unittest.TestCase):
    def test_unexpected_exception_payloads_are_not_public_messages(self):
        secret = "SENSITIVE_FIXTURE_PAYLOAD"
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "private-fixture.jsonl")
            for error in (FileNotFoundError(2, secret, path), PermissionError(13, secret, path),
                          sqlite3.OperationalError(secret), OSError(secret),
                          ValueError(secret), RuntimeError(secret)):
                with self.subTest(kind=type(error).__name__):
                    message = public_error(error)
                    self.assertTrue(message)
                    self.assertNotIn(secret, message)
                    self.assertNotIn(path, message)
            self.assertEqual(public_error(ValueError("会话编号格式不正确。")), "会话编号格式不正确。")

    def test_http_error_response_does_not_include_a_local_path_or_exception_payload(self):
        ready, holder = threading.Event(), {}
        def create_httpd(address, handler):
            httpd = ThreadingHTTPServer(address, handler)
            holder["httpd"] = httpd
            ready.set()
            return httpd
        with tempfile.TemporaryDirectory() as folder:
            missing = str(Path(folder) / "private-fixture.jsonl")
            error = FileNotFoundError(2, "SENSITIVE_FIXTURE_PAYLOAD", missing)
            with patch.object(server, "ThreadingHTTPServer", side_effect=create_httpd), \
                 patch.object(Monitor, "snapshot", side_effect=error), \
                 patch("builtins.print"):
                worker = threading.Thread(target=serve, args=(Path(folder), 0), daemon=True)
                worker.start()
                self.assertTrue(ready.wait(5))
                httpd = holder["httpd"]
                try:
                    with closing(http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)) as connection:
                        connection.request("GET", "/api/snapshot?thread=00000000-0000-0000-0000-000000000006",
                                           headers={"Host": "127.0.0.1:0"})
                        response = connection.getresponse()
                        body = response.read().decode("utf-8")
                        self.assertEqual(response.status, 503)
                        self.assertTrue(json.loads(body)["error"])
                        self.assertNotIn("private-fixture.jsonl", body)
                        self.assertNotIn("SENSITIVE_FIXTURE_PAYLOAD", body)
                        self.assertNotIn(missing, body)
                finally:
                    httpd.shutdown()
                    worker.join(5)
                self.assertFalse(worker.is_alive())


class SourceHTTPTests(unittest.TestCase):
    def test_directory_setup_and_chat_selection_use_local_data_without_automatic_selection(self):
        ready, holder = threading.Event(), {}
        def create_httpd(address, handler):
            httpd = ThreadingHTTPServer(address, handler)
            holder["httpd"] = httpd
            ready.set()
            return httpd
        with tempfile.TemporaryDirectory() as folder:
            directory, thread, _ = fixture_home(Path(folder) / "data")
            with patch.object(server, "ThreadingHTTPServer", side_effect=create_httpd), patch("builtins.print"):
                worker = threading.Thread(target=serve, args=(directory, 0), daemon=True)
                worker.start()
                self.assertTrue(ready.wait(5))
                httpd = holder["httpd"]
                def request(method, path, value=None, extra_headers=None):
                    body = json.dumps(value) if value is not None else None
                    headers = {"Host": "127.0.0.1:0", **(extra_headers or {})}
                    with closing(http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)) as connection:
                        connection.request(method, path, body=body, headers=headers)
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                try:
                    code, source = request("GET", "/api/source")
                    self.assertEqual(code, 200)
                    self.assertTrue(source["ready"])
                    code, context = request("GET", "/api/context")
                    self.assertEqual(context["status"], "manual")
                    self.assertIsNone(context["thread_id"])
                    self.assertEqual(context["threads"][0]["id"], thread)
                    code, snapshot = request("GET", "/api/snapshot?thread=" + thread)
                    self.assertEqual(code, 200)
                    self.assertEqual(snapshot["latest"]["output"], 200)
                    self.assertNotIn(str(directory), json.dumps(snapshot))
                    code, source = request("POST", "/api/source", {"directory": str(Path(folder) / "missing")})
                    self.assertFalse(source["ready"])
                    code, error = request("GET", "/api/context")
                    self.assertEqual(code, 503)
                    code, source = request("POST", "/api/source", {"directory": str(directory)})
                    self.assertTrue(source["ready"])
                    code, error = request("POST", "/api/source", {"directory": "invalid\x00path"})
                    self.assertEqual(code, 400)
                    self.assertEqual(error["error"], "数据目录格式不正确。")
                finally:
                    httpd.shutdown()
                    worker.join(5)
                self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()

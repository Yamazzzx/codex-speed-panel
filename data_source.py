"""Bounded, read-only discovery of Codex data directories on Windows and macOS."""
import os
import re
import sqlite3
import stat
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

REQUIRED = {
    "state": ("threads", {"id", "title", "rollout_path", "updated_at", "archived"}),
    "logs": ("logs", {"id", "ts", "ts_nanos", "target", "feedback_log_body", "thread_id"}),
}


def readonly_db(path):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)


def find_database(directory, kind):
    pattern = re.compile(rf"^{kind}_(\d+)\.sqlite$")
    files = [(int(match[1]), path) for path in directory.iterdir()
             if (match := pattern.fullmatch(path.name)) and path.is_file()]
    return max(files, key=lambda item: item[0])[1] if files else None


@dataclass(frozen=True)
class SourceStatus:
    directory: Path | None
    status: str
    message: str

    @property
    def ready(self):
        return self.status == "ready"

    def public(self):
        return dict(ready=self.ready, status=self.status, message=self.message,
                    directory=str(self.directory) if self.directory else "")


def probe_home(directory):
    try:
        directory = Path(directory).expanduser().resolve()
        if not stat.S_ISDIR(directory.stat().st_mode):
            return SourceStatus(directory, "missing", "请选择 Codex 数据文件夹。")
        for kind, (table, required) in REQUIRED.items():
            database = find_database(directory, kind)
            if database is None:
                return SourceStatus(directory, "incomplete", "目录缺少会话或速度日志数据库，请先使用 Codex 或选择其他目录。")
            with closing(readonly_db(database)) as connection:
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if not required <= columns:
                    return SourceStatus(directory, "unsupported", "数据库格式与当前版本不兼容，需要适配后才能读取。")
                connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        return SourceStatus(directory, "ready", "已找到可读取的 Codex 数据目录。")
    except FileNotFoundError:
        return SourceStatus(directory, "missing", "未找到数据目录，可以手动填写后重新检查。")
    except PermissionError:
        return SourceStatus(directory, "permission", "没有读取权限，请检查目录权限或选择其他目录。")
    except sqlite3.Error:
        return SourceStatus(directory, "unreadable", "数据库暂时无法读取，请确认 Codex 已运行后重试。")
    except (OSError, ValueError, RuntimeError):
        return SourceStatus(None, "invalid", "数据目录不可用，请检查填写的路径。")


def candidate_homes(environ=None, user_home=None, platform=None):
    environ = os.environ if environ is None else environ
    user_home = Path.home() if user_home is None else Path(user_home)
    platform = sys.platform if platform is None else platform
    configured = environ.get("CODEX_HOME")
    if configured:
        return [Path(configured).expanduser()]
    candidates = [user_home / ".codex"]
    if platform == "darwin":
        candidates.append(user_home / "Library" / "Application Support" / "Codex")
    elif platform == "win32":
        for variable in ("LOCALAPPDATA", "APPDATA"):
            if environ.get(variable):
                candidates.append(Path(environ[variable]) / "Codex")
    candidates.append(user_home / ".config" / "codex")
    return list(dict.fromkeys(candidates))


def discover_home(explicit=None, environ=None, user_home=None, platform=None):
    if explicit is not None:
        return probe_home(explicit)
    candidates = candidate_homes(environ, user_home, platform)
    failures = []
    for directory in candidates:
        result = probe_home(directory)
        if result.ready:
            return result
        failures.append(result)
    return next((result for result in failures if result.status != "missing"), failures[0])

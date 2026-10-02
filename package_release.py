"""Build a privacy-checked source release from an explicit allowlist."""
import hashlib
import re
import zipfile
from pathlib import Path
from server import VERSION

ROOT = Path(__file__).resolve().parent
FILES = (
    "server.py", "data_source.py", "app.js", "style.css", "index.html", "launch.pyw", "stop.py",
    "start.cmd", "stop.cmd", "双击打开.cmd", "停止面板.cmd", "test_monitor.py",
    "package_release.py", ".gitignore", "README.md", "PRIVACY.md", "LICENSE",
    "docs/banner.png", "docs/speed-5.6.png", "docs/speed-6.1.png",
    "desktop.py", "build_windows.py", "requirements-build.txt", "使用说明.txt", "INSTALL.md",
    "start.command", "stop.command",
)
CHECKS = (
    re.compile(r"\b[A-Z]:[\\/]", re.I),
    re.compile(r"/(?:Users|home)/[a-z][\w.-]+/", re.I),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{15,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b"),
)
IDS = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)


def check_text(name, text):
    if any(pattern.search(text) for pattern in CHECKS):
        raise ValueError(f"Private path or credential pattern found in {name}")
    if any(not match.group().startswith("00000000-") for match in IDS.finditer(text)):
        raise ValueError(f"Non-demo session identifier found in {name}")


def write_checksums(output):
    """Write one manifest for the release archives that actually exist."""
    archives = [output / f"codex-speed-panel-v{VERSION}-{kind}.zip" for kind in ("windows-x64", "source")]
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
             for path in archives if path.is_file()]
    (output / "SHA256SUMS.txt").write_text("".join(lines), encoding="ascii")


def build():
    sources = []
    for name in FILES:
        source = (ROOT / name).resolve()
        if not source.is_relative_to(ROOT) or not source.is_file():
            raise ValueError(f"Missing or out-of-project release file: {name}")
        content = source.read_bytes()
        if not name.endswith((".jpg", ".png")):
            check_text(name, content.decode("utf-8"))
        sources.append((name, content))
    output = ROOT / ".release"
    output.mkdir(exist_ok=True)
    archive = output / f"codex-speed-panel-v{VERSION}-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for name, content in sources:
            entry = zipfile.ZipInfo(f"codex-speed-panel/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = (0o100755 if name.endswith(".command") else 0o100644) << 16
            zip_file.writestr(entry, content)
    write_checksums(output)
    print(f"Privacy checks passed. {len(sources)} files, {archive.stat().st_size:,} bytes.")
    print(archive.name)
    return archive


if __name__ == "__main__":
    build()

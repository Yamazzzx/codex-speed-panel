"""Build a portable Windows executable; end users need no Python installation."""
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from server import VERSION
from package_release import check_text, write_checksums

ROOT = Path(__file__).resolve().parent


def build():
    if os.name != "nt":
        raise SystemExit("Build the Windows executable on Windows.")
    if struct.calcsize("P") != 8:
        raise SystemExit("Use 64-bit Python to build the Windows x64 executable.")
    for name in ("使用说明.txt", "LICENSE"):
        check_text(name, (ROOT / name).read_text(encoding="utf-8"))
    output = ROOT / ".release"
    work = ROOT / ".build"
    output.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    args = [sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--noconfirm",
            "--name", "CodexSpeedPanel", "--distpath", str(output / "bin"),
            "--workpath", str(work / "pyinstaller"), "--specpath", str(work)]
    for name in ("index.html", "app.js", "style.css"):
        args += ["--add-data", f"{ROOT/name}{os.pathsep}."]
    subprocess.run(args + [str(ROOT/"desktop.py")], cwd=ROOT, check=True)
    license_file = Path(sys.base_prefix) / "LICENSE.txt"
    if not license_file.is_file():
        raise SystemExit("Missing bundled Python license; no download archive was created.")
    archive = output / f"codex-speed-panel-v{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(output/"bin/CodexSpeedPanel.exe", "CodexSpeedPanel.exe")
        z.write(ROOT/"使用说明.txt", "使用说明.txt")
        z.write(ROOT/"LICENSE", "LICENSE")
        z.writestr("THIRD-PARTY-LICENSES.txt", license_file.read_bytes())
        z.writestr("关闭监测.cmd", '@echo off\r\n"%~dp0CodexSpeedPanel.exe" --stop\r\n')
    write_checksums(output)
    print(f"Portable Windows archive: {archive.name} ({archive.stat().st_size:,} bytes)")
    return archive


if __name__ == "__main__":
    build()

# -*- mode: python ; coding: utf-8 -*-
"""Build on 64-bit Windows: pyinstaller --clean --noconfirm VideoGet.spec."""

import platform
import struct
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

if sys.platform != "win32":
    raise SystemExit("VideoGet.exe must be built on Windows; cross-compiling from macOS/Linux is unsupported.")
if struct.calcsize("P") != 8 or platform.machine().lower() not in {"amd64", "x86_64"}:
    raise SystemExit("Use 64-bit x86 Windows and a 64-bit x86 Python interpreter to build VideoGet.exe.")

import imageio_ffmpeg

project_dir = Path(SPECPATH)
yt_datas, yt_binaries, yt_imports = collect_all("yt_dlp")
ff_datas, ff_binaries, ff_imports = collect_all("imageio_ffmpeg")
pw_datas, pw_binaries, pw_imports = collect_all("playwright")

# Keep the wheel's executable in the location imageio_ffmpeg expects at runtime.
# FFmpeg executables are not DLLs, so collect_dynamic_libs alone is insufficient.
ffmpeg_dir = Path(imageio_ffmpeg.__file__).resolve().parent / "binaries"
ffmpeg_exes = sorted(ffmpeg_dir.glob("ffmpeg*.exe"))
if not ffmpeg_exes:
    raise SystemExit("The imageio-ffmpeg Windows wheel has no bundled FFmpeg executable. Reinstall imageio-ffmpeg.")

ffmpeg_names = {exe.name for exe in ffmpeg_exes}
ff_datas = [entry for entry in ff_datas if Path(entry[0]).name not in ffmpeg_names]
ff_binaries = [entry for entry in ff_binaries if Path(entry[0]).name not in ffmpeg_names]
ff_binaries.extend((str(exe), "imageio_ffmpeg/binaries") for exe in ffmpeg_exes)

analysis = Analysis(
    [str(project_dir / "main.py")],
    pathex=[str(project_dir)],
    # collect_all("playwright") includes its bundled driver (node.exe and
    # playwright/driver/package), while browser binaries are intentionally not
    # installed or packaged. The browser scan uses an existing Edge/Chrome.
    binaries=yt_binaries + ff_binaries + pw_binaries,
    datas=yt_datas + ff_datas + pw_datas,
    hiddenimports=sorted(set(yt_imports + ff_imports + pw_imports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="VideoGet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

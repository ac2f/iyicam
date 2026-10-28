"""Build the program into a folder (and a zip, and with Inno Setup an installer) that runs without Python.

    python packaging/build.py              # dist/iyiCAM/ and dist/iyiCAM-<version>-<system>.zip
    python packaging/build.py --installer  # also dist/iyiCAM-<version>-setup.exe (Windows, needs Inno Setup 6)
    python packaging/build.py --no-test    # do not run ``iyicam-cli selftest`` on the result

It needs a 64-bit Python 3.11 or newer with the program's own libraries and PyInstaller:
    python -m pip install ".[gui]" pyinstaller
``packaging/build_windows.bat`` and ``packaging/build_unix.sh`` do that in a fresh virtual environment first.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "packaging"
DIST, WORK = ROOT / "dist", ROOT / "build"
WINDOWS = sys.platform == "win32"


def fail(msg: str) -> None:
    print(f"\nerror: {msg}", file=sys.stderr)
    sys.exit(1)


def check_python() -> None:
    if sys.version_info < (3, 11):
        fail(f"Python {sys.version.split()[0]} is too old: the program needs Python 3.11 or newer.")
    if struct.calcsize("P") * 8 != 64:
        fail("This is a 32-bit Python. Qt for Python (PySide6) has no 32-bit version: install the 64-bit Python "
             "from python.org (\"Windows installer (64-bit)\") and build with that.")
    missing = []
    for mod, pip in (("PySide6", "PySide6-Essentials"), ("numpy", "numpy"), ("pyclipper", "pyclipper"),
                     ("ezdxf", "ezdxf"), ("svgelements", "svgelements"), ("PyInstaller", "pyinstaller")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip)
    if missing:
        fail("missing libraries: " + ", ".join(missing) + f'\n  install them with:  "{sys.executable}" -m pip '
             'install ".[gui]" pyinstaller')


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from iyicam import __version__
    return __version__


def version_file(ver: str) -> Path:
    """The version resource of the Windows .exe (what Explorer shows under Properties > Details)."""
    nums = (tuple(int(x) for x in ver.split(".") if x.isdigit()) + (0, 0, 0, 0))[:4]
    WORK.mkdir(exist_ok=True)
    f = WORK / "version_info.txt"
    f.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'iyiCAM'), StringStruct('FileDescription', 'iyiCAM - 2D CAM for CNC routers'),
      StringStruct('FileVersion', '{ver}'), StringStruct('InternalName', 'iyiCAM'),
      StringStruct('OriginalFilename', 'iyiCAM.exe'), StringStruct('ProductName', 'iyiCAM'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")
    return f


def run(cmd: list[str], **kw) -> None:
    print("\n$", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], cwd=ROOT, **kw)
    if r.returncode:
        fail(f"{Path(str(cmd[0])).name} stopped with exit code {r.returncode}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--installer", action="store_true", help="also make the Windows installer (Inno Setup 6)")
    ap.add_argument("--no-test", action="store_true", help="do not run the self test on the built program")
    ap.add_argument("--no-zip", action="store_true", help="do not make the zip")
    a = ap.parse_args()

    check_python()
    ver = version()
    print(f"iyiCAM {ver}, Python {platform.python_version()}, {platform.system()} {platform.machine()}")
    if not (PACK / "iyicam.ico").is_file():
        run([sys.executable, PACK / "make_icons.py"])
    for d in (DIST / "iyiCAM", WORK / "iyicam"):
        shutil.rmtree(d, ignore_errors=True)
    env = None
    if WINDOWS:
        import os
        env = dict(os.environ, IYICAM_VERSION_FILE=str(version_file(ver)))
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST, "--workpath", WORK,
         PACK / "iyicam.spec"], env=env)

    out = DIST / "iyiCAM"
    exe = out / ("iyiCAM.exe" if WINDOWS else "iyiCAM")
    cli = out / ("iyicam-cli.exe" if WINDOWS else "iyicam-cli")
    if not exe.is_file() or not cli.is_file():
        fail(f"the build did not make {exe.name} and {cli.name} in {out}")
    if not a.no_test:
        print("\nTesting the built program (it must work without Python and without the source):")
        run([cli, "selftest"], env=_test_env())
    if not a.no_zip:
        name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux") + "-" + platform.machine().lower()
        z = DIST / f"iyiCAM-{ver}-{name}.zip"
        print(f"\nMaking {z.name} ...")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    zf.write(f, Path("iyiCAM") / f.relative_to(out))
        print(f"  {z}  ({z.stat().st_size / 1e6:.0f} MB)")
    if a.installer:
        if not WINDOWS:
            fail("the installer is made on Windows (Inno Setup).")
        iscc = shutil.which("iscc") or next((str(p) for p in (
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"), Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
            Path.home() / r"AppData\Local\Programs\Inno Setup 6\ISCC.exe") if p.is_file()), None)
        if not iscc:
            fail("Inno Setup 6 was not found: install it from https://jrsoftware.org/isinfo.php and try again.")
        run([iscc, f"/DAppVersion={ver}", f"/DSourceDir={out}", f"/DOutputDir={DIST}", PACK / "iyicam.iss"])
    print(f"\nDone. Run it with:  {exe}")


def _test_env():
    """The environment for the self test: nothing of this Python's libraries may be picked up."""
    import os
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(k, None)
    return env


if __name__ == "__main__":
    main()

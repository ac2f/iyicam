# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe: ``python packaging/build.py`` runs it (or ``pyinstaller packaging/iyicam.spec``).

Makes a folder ``dist/iyiCAM`` with ``iyiCAM`` (the window, no console) and ``iyicam-cli`` (the command line
commands, e.g. ``iyicam-cli nest job.icam -o nested.icam`` and ``iyicam-cli selftest``) that share the libraries.
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent                       # noqa: F821  (SPECPATH is given by PyInstaller)
sys.path.insert(0, str(ROOT))
from iyicam import __version__  # noqa: E402

ICON = ROOT / "packaging" / "iyicam.ico"
VERSION_FILE = os.environ.get("IYICAM_VERSION_FILE")         # (Windows: the "Details" of the .exe; build.py makes it)
WINDOWS, MAC = sys.platform == "win32", sys.platform == "darwin"

# the post-processors (.toml) are data of the package; the documents and examples go along for the user
datas = collect_data_files("iyicam")
datas += [(str(ROOT / "docs"), "docs"), (str(ROOT / "examples"), "examples"), (str(ROOT / "README.md"), ".")]
# modules that are only imported by name (plugins, commands, post-processors) or inside functions
hidden = collect_submodules("iyicam") + collect_submodules("ezdxf") + ["svgelements", "pyclipper", "numpy"]
excludes = ["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "PIL", "lxml", "PyQt5", "PyQt6", "PySide2",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtCharts"]


def analysis(script):
    return Analysis([str(ROOT / "packaging" / script)], pathex=[str(ROOT)], datas=datas,       # noqa: F821
                    hiddenimports=hidden, excludes=excludes, noarchive=False)


a_gui, a_cli = analysis("launcher.py"), analysis("launcher.py")
MERGE((a_gui, "iyiCAM", "iyiCAM"), (a_cli, "iyicam-cli", "iyicam-cli"))                       # noqa: F821

common = dict(exclude_binaries=True, debug=False, strip=False, upx=False,
              icon=str(ICON) if ICON.is_file() and WINDOWS else None,
              version=VERSION_FILE if WINDOWS and VERSION_FILE else None)
pyz_gui, pyz_cli = PYZ(a_gui.pure), PYZ(a_cli.pure)                                           # noqa: F821
gui = EXE(pyz_gui, a_gui.scripts, [], name="iyiCAM", console=False, **common)                 # noqa: F821
cli = EXE(pyz_cli, a_cli.scripts, [], name="iyicam-cli", console=True, **common)              # noqa: F821
coll = COLLECT(gui, a_gui.binaries, a_gui.datas, cli, a_cli.binaries, a_cli.datas,            # noqa: F821
               strip=False, upx=False, name="iyiCAM")

if MAC:
    app = BUNDLE(coll, name="iyiCAM.app", icon=None, bundle_identifier="app.iyicam.iyicam",    # noqa: F821
                 version=__version__,
                 info_plist={"NSHighResolutionCapable": True, "CFBundleDisplayName": "iyiCAM",
                             "CFBundleDocumentTypes": [{"CFBundleTypeName": "iyiCAM project",
                                                        "CFBundleTypeExtensions": ["icam"], "CFBundleTypeRole": "Editor"}]})

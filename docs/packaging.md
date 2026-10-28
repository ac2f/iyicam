# Packaging: a program that runs without Python

`packaging/build.py` turns the source into a folder that starts with a double click and needs no Python, no
`pip` and no source: `dist/iyiCAM/iyiCAM.exe` (the window) and `dist/iyiCAM/iyicam-cli.exe` (the command line
commands). It also makes a zip of the folder and, on Windows with Inno Setup, an installer.

## Windows, step by step

1. Install the **64-bit** Python 3.11 or newer from <https://www.python.org/downloads/windows/>
   (*Windows installer (64-bit)*, tick "Add python.exe to PATH"). The 32-bit Python cannot be used: Qt for
   Python (PySide6), the library of the window, only exists for 64-bit.
2. Download the source (the green *Code* button on GitHub, *Download ZIP*) and unpack it.
3. Open the folder in a terminal and run

   ```
   packaging\build_windows.bat
   ```

   It makes a virtual environment (`.venv-build`), installs the libraries and PyInstaller, builds, and then runs
   `iyicam-cli selftest` on the result: every feature is used once (libraries, post-processors, DXF / SVG,
   toolpaths, nesting, the window with tabs and both themes) and anything missing from the package is reported.
4. Take `dist\iyiCAM\` (or `dist\iyiCAM-<version>-windows-amd64.zip`). Run `iyiCAM.exe`.

For a **setup program** with start menu entry, uninstaller and the `.icam` file type, install
[Inno Setup 6](https://jrsoftware.org/isinfo.php) and run `packaging\build_windows.bat --installer`:
`dist\iyiCAM-<version>-setup.exe` (English and Turkish).

## Linux and macOS

```
packaging/build_unix.sh          # dist/iyiCAM/ and a zip
```

(macOS also gets `dist/iyiCAM.app`.)

## Without the scripts

```
python -m pip install ".[gui]" pyinstaller
python packaging/build.py [--installer] [--no-test] [--no-zip]
```

## Built on GitHub

`.github/workflows/build.yml` runs the tests on every push. Push a tag (`git tag v0.2.0 && git push --tags`) or
start the workflow by hand (*Actions > build > Run workflow*) and it packages for Windows, Linux and macOS:
the zips and the Windows setup program are attached to the run and, for a tag, to the release. Nothing has to be
installed on your own computer.

## What is in the package

* Python and the libraries (Qt, numpy, ezdxf, pyclipper, svgelements), the post-processors (`.toml`), the
  documents (`docs/`) and the examples.
* Your settings, tools, own posts and plugins stay where they were: `~/.iyicam` (`%USERPROFILE%\.iyicam` on
  Windows), or the folder named by the `IYICAM_HOME` environment variable. A new version finds them again.
* Plugins (`~/.iyicam/plugins/*.py`) work in the package; they can use `iyicam` and the libraries above. A plugin
  that needs another library has to be run from the source instead (`python -m iyicam`).

* `iyiCAM.exe` has no console window. An error that nothing caught is shown in a box and written to
  `error.log` in the settings folder (send that file with a bug report).

## If something is missing

`iyicam-cli selftest` (also `iyicam selftest` from the source) names the part that does not work. A module that
PyInstaller did not see goes into `hidden` in `packaging/iyicam.spec`; a file that is not found goes into `datas`.
`--no-gui` leaves the window out of the test (a machine without graphics libraries).

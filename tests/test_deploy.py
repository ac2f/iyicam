"""The files that put the web service on a Linux server (``packaging/linux``) stay consistent with each other and with
the program.  They cannot be run here (root, systemd), so this checks what can go wrong without that."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LINUX = Path(__file__).resolve().parent.parent / "packaging" / "linux"
UNIT = LINUX / "iyicam-web.service"
INSTALL = LINUX / "install.sh"
UNINSTALL = LINUX / "uninstall.sh"
FILES = [UNIT, INSTALL, UNINSTALL, LINUX / "nginx.conf.example", LINUX / "Caddyfile.example",
         LINUX / "haproxy.cfg.example"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_files_exist_and_have_unix_line_endings(path):
    data = path.read_bytes()                      # a CRLF script fails on the server with "bash\r: not found"
    assert data and b"\r" not in data


def test_every_placeholder_of_the_unit_is_filled_by_the_installer():
    placeholders = set(re.findall(r"@([A-Z_]+)@", UNIT.read_text(encoding="utf-8")))
    assert placeholders == {"APP", "SERVICE_USER", "HOST", "PORT", "PROXY", "FILES", "ROOTS", "RW_PATHS"}
    script = INSTALL.read_text(encoding="utf-8")
    for name in placeholders:
        assert f"s|@{name}@|" in script, name


def test_the_unit_uses_options_that_the_program_has():
    unit = UNIT.read_text(encoding="utf-8")
    line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    flags = set(re.findall(r"(?<!\S)(--[a-z-]+)", line))
    assert {"--host", "--port", "--quiet"} <= flags
    out = subprocess.run([sys.executable, "-m", "iyicam", "serve", "--help"], capture_output=True, text=True,
                         timeout=60).stdout
    # (the installer fills "--trust-proxy" in through @PROXY@ and "--no-files" through @FILES@)
    for flag in flags | {"--trust-proxy", "--no-files", "--root"}:
        assert flag in out, flag
    assert "-m iyicam serve" in line and "@PROXY@" in line and "@FILES@" in line


def test_the_state_directory_is_the_same_in_the_unit_and_the_installer():
    unit = UNIT.read_text(encoding="utf-8")
    assert "Environment=IYICAM_HOME=/var/lib/iyicam" in unit and "StateDirectory=iyicam" in unit
    assert 'STATE_DIR="/var/lib/iyicam"' in INSTALL.read_text(encoding="utf-8")
    assert 'STATE_DIR="/var/lib/iyicam"' in UNINSTALL.read_text(encoding="utf-8")


def test_the_folders_of_the_page_are_given_to_the_service_and_may_be_written():
    unit = UNIT.read_text(encoding="utf-8")
    script = INSTALL.read_text(encoding="utf-8")
    assert "Environment=IYICAM_ROOTS=@ROOTS@" in unit          # what the program reads (iyicam.service.files)
    assert "ReadWritePaths=@RW_PATHS@" in unit and "ProtectSystem=strict" in unit    # (else nothing could be saved)
    assert 'DEFAULT_ROOT="$STATE_DIR/files"' in script          # inside the state folder, so it is writable anyway
    assert "--no-files" in script and "IYICAM_FILES" in script


def test_the_proxy_examples_overwrite_the_headers_that_trust_proxy_reads():
    nginx = (LINUX / "nginx.conf.example").read_text(encoding="utf-8")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx       # (not $proxy_add_x_forwarded_for)
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in nginx
    assert "proxy_set_header Host $host;" in nginx                         # else the Origin check refuses the page
    haproxy = (LINUX / "haproxy.cfg.example").read_text(encoding="utf-8")
    assert "http-request set-header X-Forwarded-For %[src]" in haproxy
    assert "127.0.0.1:8300" in nginx and "127.0.0.1:8300" in haproxy and "127.0.0.1:8300" in \
        (LINUX / "Caddyfile.example").read_text(encoding="utf-8")


@pytest.mark.parametrize("script", [INSTALL, UNINSTALL], ids=lambda p: p.name)
def test_the_scripts_are_valid_bash(script):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    probe = subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True)
    if probe.returncode != 0 or probe.stdout.strip() != "ok":       # (the WSL stub on a Windows without a distribution)
        pytest.skip("bash does not work here")
    r = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

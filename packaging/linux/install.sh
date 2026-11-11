#!/usr/bin/env bash
# ======================================================================
#  iyiCAM web service - Linux (systemd) installation
#
#  What it does:
#    1. Finds Python 3.11 or newer (and the venv module); on Debian/Ubuntu it
#       installs them with apt when they are missing
#    2. Creates an unprivileged "iyicam" service user
#    3. Creates a venv in the project directory and installs iyiCAM in it
#       (editable, without Qt: the service needs no window)
#    4. Installs the systemd service "iyicam-web", enables it at boot, starts it
#
#  Usage (root):   sudo bash packaging/linux/install.sh
#  Update:         git pull && sudo bash packaging/linux/install.sh      (idempotent)
#
#  Settings (environment variables, "sudo IYICAM_PORT=8301 bash ..."):
#    IYICAM_HOST=127.0.0.1   address to listen on; the default is for a reverse proxy
#                            that adds HTTPS (docs/web.md). 0.0.0.0 = every network, plain HTTP.
#    IYICAM_PORT=8300
#    PYTHON=python3.12       the Python to use (3.11 or newer)
#  The project directory must NOT be under /root or /home (the service user
#  cannot read it); recommended: /opt/iyicam
# ======================================================================
set -euo pipefail

SERVICE="iyicam-web"
SERVICE_USER="iyicam"
STATE_DIR="/var/lib/iyicam"
HOST="${IYICAM_HOST:-127.0.0.1}"
PORT="${IYICAM_PORT:-8300}"
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die()  { echo "ERROR: $*" >&2; exit 1; }
step() { echo "  [$1] $2"; }

[[ $EUID -eq 0 ]] || die "run as root:  sudo bash packaging/linux/install.sh"
command -v systemctl >/dev/null || die "systemd is required."
[[ "$PORT" =~ ^[0-9]+$ ]] || die "IYICAM_PORT must be a number."
[[ "$HOST" =~ ^[0-9A-Za-z.:_-]+$ ]] || die "IYICAM_HOST must be an address such as 127.0.0.1 or 0.0.0.0."
case "$APP" in
  /root|/root/*|/home|/home/*)
    die "The project directory ($APP) is not accessible to the service user. Example:
       git clone <repo> /opt/iyicam && sudo bash /opt/iyicam/packaging/linux/install.sh" ;;
esac
[[ -f "$APP/packaging/linux/$SERVICE.service" ]] || die "$APP/packaging/linux/$SERVICE.service is missing."

# "--trust-proxy" makes the server believe X-Forwarded-For / -Proto. That is only safe when nothing but the
# reverse proxy can reach it, i.e. when it listens on this computer only.
case "$HOST" in
  127.*|::1|localhost) PROXY="--trust-proxy" ;;
  *) PROXY="" ;;
esac

echo "iyiCAM web service installation  ->  $APP"

# 1) Python 3.11+ and the venv module
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; }
find_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then command -v "$c"; return 0; fi
  done
  return 1
}
if [[ -n "${PYTHON:-}" ]]; then
  command -v "$PYTHON" >/dev/null 2>&1 && py_ok "$PYTHON" || die "PYTHON=$PYTHON is not a Python 3.11 or newer."
  PY="$(command -v "$PYTHON")"
else
  PY="$(find_python)" || PY=""
  if [[ -z "$PY" ]] && command -v apt-get >/dev/null; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3
    PY="$(find_python)" || PY=""
  fi
  [[ -n "$PY" ]] || die "iyiCAM needs Python 3.11 or newer and this system has $(python3 --version 2>&1 || echo none).
       Install a newer one (Debian 12 / Ubuntu 24.04 have it; Ubuntu 22.04: the deadsnakes PPA, or uv),
       then:  sudo PYTHON=python3.12 bash packaging/linux/install.sh"
fi
if ! "$PY" -c 'import ensurepip, venv' >/dev/null 2>&1; then
  command -v apt-get >/dev/null || die "the venv module of $PY is missing; install the 'python3-venv' package."
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
  "$PY" -c 'import ensurepip, venv' >/dev/null 2>&1 \
    || die "the venv module of $PY is missing; install the matching package (for example python3.12-venv)."
fi
step 1/4 "python ready: $("$PY" --version) ($PY)"

# 2) service user
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent \
          --shell /usr/sbin/nologin "$SERVICE_USER"
fi
step 2/4 "service user: $SERVICE_USER"

# 3) venv + package (core only: pyclipper, numpy, ezdxf, svgelements; no Qt)
if [[ -x "$APP/venv/bin/python" ]] && ! py_ok "$APP/venv/bin/python"; then
  rm -rf "$APP/venv"                                   # a venv of an older Python is no use
fi
[[ -x "$APP/venv/bin/python" ]] || "$PY" -m venv "$APP/venv"
PIP_LOG="$(mktemp)"
trap 'rm -f "$PIP_LOG"' EXIT
{ "$APP/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip \
  && "$APP/venv/bin/python" -m pip install --quiet --disable-pip-version-check -e "$APP"; } \
  >"$PIP_LOG" 2>&1 || { cat "$PIP_LOG" >&2
    die "pip installation failed (if a package had to be compiled: apt-get install build-essential python3-dev)."; }
"$APP/venv/bin/python" -c 'import iyicam.service.server' || die "iyiCAM was installed but the web service cannot be imported."
step 3/4 "venv ready: $APP/venv/bin/python"

# 4) systemd
sed -e "s|@APP@|$APP|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
    -e "s|@HOST@|$HOST|g" -e "s|@PORT@|$PORT|g" -e "s|@PROXY@|$PROXY|g" \
    "$APP/packaging/linux/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"
step 4/4 "service started"

CHECK_HOST="$HOST"
case "$HOST" in 0.0.0.0|::) CHECK_HOST="127.0.0.1" ;; esac
[[ "$CHECK_HOST" == *:* ]] && CHECK_HOST="[$CHECK_HOST]"
for _ in $(seq 1 30); do
  if "$APP/venv/bin/python" -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://$CHECK_HOST:$PORT/health', timeout=2).status == 200 else 1)" >/dev/null 2>&1; then
    HEALTHY=1; break
  fi
  sleep 0.5
done

echo
echo "======================================================================"
if [[ -n "${HEALTHY:-}" ]]; then echo "  INSTALLATION COMPLETE - the service is running at http://$HOST:$PORT"
else echo "  The service was started but the health check did not pass:  journalctl -u $SERVICE -n 50"; fi
echo
echo "  Key             : $STATE_DIR/server.key   (made at the first start; type it on the page)"
echo "                    to view it:  sudo cat $STATE_DIR/server.key"
echo "  New key         : sudo -u $SERVICE_USER IYICAM_HOME=$STATE_DIR $APP/venv/bin/iyicam serve --new-key"
echo "                    (prints it; every signed-in browser is signed out at once)"
echo "  Status / log    : systemctl status $SERVICE   |   journalctl -u $SERVICE -f"
echo "  Update          : git pull && sudo bash packaging/linux/install.sh"
if [[ -n "$PROXY" ]]; then
  echo "  Expose publicly : put HTTPS in front -> packaging/linux/Caddyfile.example | nginx.conf.example | haproxy.cfg.example"
  echo "                    (the page needs a host name of its own, e.g. iyicam.example.com)"
else
  echo "  WARNING         : it listens on $HOST in plain HTTP: the key and the drawings can be read on that network."
  echo "                    Prefer the default (127.0.0.1) with an HTTPS reverse proxy in front."
fi
echo "  Full guide      : docs/web.md"
echo "======================================================================"

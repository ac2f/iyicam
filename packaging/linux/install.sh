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
#    IYICAM_ROOTS=/var/lib/iyicam/files
#                            the folders (separated by ":") in which the page may open and save files; the
#                            default is made here. A folder of your own must be writable by the user "iyicam".
#    IYICAM_FILES=off        the page cannot open or save files of this computer (serve without --local)
#    IYICAM_HUB=on           this server has no files of its own: an agent (iyicam agent) on the workshop PC connects
#                            to it and the page works with that PC's folders (docs/web.md). The CAM always runs in the browser.
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
DEFAULT_ROOT="$STATE_DIR/files"
ROOTS="${IYICAM_ROOTS:-$DEFAULT_ROOT}"
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die()  { echo "ERROR: $*" >&2; exit 1; }
step() { echo "  [$1] $2"; }

[[ $EUID -eq 0 ]] || die "run as root:  sudo bash packaging/linux/install.sh"
command -v systemctl >/dev/null || die "systemd is required."
[[ "$PORT" =~ ^[0-9]+$ ]] || die "IYICAM_PORT must be a number."
[[ "$HOST" =~ ^[0-9A-Za-z.:_-]+$ ]] || die "IYICAM_HOST must be an address such as 127.0.0.1 or 0.0.0.0."
[[ "$ROOTS" =~ ^[0-9A-Za-z/._:-]+$ ]] || die "IYICAM_ROOTS: absolute folder names separated by ':' (letters, digits and / . _ - only)."
IFS=: read -r -a ROOT_LIST <<<"$ROOTS"
RW_PATHS=""
for r in "${ROOT_LIST[@]}"; do
  [[ "$r" == /* ]] || die "IYICAM_ROOTS: '$r' is not an absolute folder name."
  RW_PATHS+="-$r "                      # ("-": a folder that is missing when the service starts does not stop it)
done
LOCAL_FLAG="--local"                  # (the files of this computer; not with an agent, not when switched off)
case "${IYICAM_FILES:-on}" in
  off|OFF|no|0) LOCAL_FLAG="" ;;
esac
HUB=""
case "${IYICAM_HUB:-off}" in
  on|ON|yes|1) HUB=1; LOCAL_FLAG="" ;;
esac
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent \
          --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# the folders of the page: the default one is made here (inside the state folder, owned by the service user, so
# that systemd leaves it as it is); a folder of your own that is missing is made, one that exists is left alone
if [[ -n "$LOCAL_FLAG" ]]; then        # (a server for an agent has no files of its own)
  for r in "${ROOT_LIST[@]}"; do
    if [[ "$r" == "$DEFAULT_ROOT" ]]; then
      install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR" "$DEFAULT_ROOT"
    elif [[ ! -d "$r" ]]; then
      install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$r"
    fi
    if ! runuser -u "$SERVICE_USER" -- test -w "$r" 2>/dev/null; then
      echo "  WARNING: the user $SERVICE_USER cannot write in $r; give it access (chown, or setfacl -m u:$SERVICE_USER:rwx)." >&2
    fi
    case "$r" in /home|/home/*|/root|/root/*)
      echo "  WARNING: $r is under /home or /root, which the service cannot see (ProtectHome); see docs/web.md." >&2 ;;
    esac
  done
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
"$APP/venv/bin/python" -c 'import iyicam.share.server' || die "iyiCAM was installed but the web server cannot be imported."
step 3/4 "venv ready: $APP/venv/bin/python"

# 4) systemd
sed -e "s|@APP@|$APP|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
    -e "s|@HOST@|$HOST|g" -e "s|@PORT@|$PORT|g" -e "s|@PROXY@|$PROXY|g" \
    -e "s|@LOCAL@|$LOCAL_FLAG|g" -e "s|@ROOTS@|$ROOTS|g" -e "s|@RW_PATHS@|$RW_PATHS|g" \
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
if [[ -n "$HUB" ]]; then
  echo "  Mode            : for an AGENT (no files here; the agent on the workshop PC shares its folders)"
  echo "  Key             : $STATE_DIR/server.key   (made at the first start; typed on the page AND given to the agent)"
  echo "                    to view it:  sudo cat $STATE_DIR/server.key"
  echo "  On the computer : iyicam agent --server https://<this server's host name> --key <the key> --root <folder>"
  echo "                    (docs/web.md)"
  echo "  New key         : sudo -u $SERVICE_USER IYICAM_HOME=$STATE_DIR $APP/venv/bin/iyicam serve --new-key"
  echo "                    (every browser is signed out and every agent then needs the new one)"
else
  echo "  Key             : $STATE_DIR/server.key   (made at the first start; type it on the page)"
  echo "                    to view it:  sudo cat $STATE_DIR/server.key"
  echo "  New key         : sudo -u $SERVICE_USER IYICAM_HOME=$STATE_DIR $APP/venv/bin/iyicam serve --new-key"
  echo "                    (prints it; every signed-in browser is signed out at once)"
  if [[ -z "$LOCAL_FLAG" ]]; then
    echo "  Files           : off (the page cannot open or save files of this computer)"
  else
    echo "  Files           : the page opens and saves files in:  ${ROOTS//:/  }"
  fi
fi
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

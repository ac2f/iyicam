#!/usr/bin/env bash
# Removes the iyiCAM web service.
#   sudo bash packaging/linux/uninstall.sh          stops + removes the service (the key and settings are kept)
#   sudo bash packaging/linux/uninstall.sh --full   also deletes the venv, /var/lib/iyicam (the KEY too, and the
#                                                   files of the page in /var/lib/iyicam/files) and the service user
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "ERROR: run as root." >&2; exit 1; }
SERVICE="iyicam-web"; SERVICE_USER="iyicam"; STATE_DIR="/var/lib/iyicam"
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

systemctl disable --now "$SERVICE" 2>/dev/null || true
rm -f "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
echo "Service removed."

if [[ "${1:-}" == "--full" ]]; then
  rm -rf "$APP/venv" "$STATE_DIR" "$APP"/*.egg-info
  id "$SERVICE_USER" >/dev/null 2>&1 && userdel "$SERVICE_USER" || true
  echo "venv, $STATE_DIR (key, settings, the default folder of the page) and the service user were deleted."
  echo "Folders you gave with IYICAM_ROOTS elsewhere were not touched."
fi

#!/usr/bin/env bash
# Black Box V1 — install on Raspberry Pi OS (Bookworm, Python 3.11+).
# Run from the pi/ directory: sudo ./install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

INSTALL_DIR=/opt/blackbox

echo "==> Installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -r blackbox "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"

echo "==> Creating virtualenv"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

echo "==> Config"
if [[ ! -f /etc/blackbox.toml ]]; then
  cp config.example.toml /etc/blackbox.toml
  echo "    Created /etc/blackbox.toml — EDIT vehicle_id and device_id."
fi
if [[ ! -f /etc/blackbox.env ]]; then
  cp blackbox.env.example /etc/blackbox.env
  chmod 600 /etc/blackbox.env
  echo "    Created /etc/blackbox.env — EDIT Supabase credentials."
fi

echo "==> systemd service"
cp blackbox.service /etc/systemd/system/blackbox.service
systemctl daemon-reload
systemctl enable blackbox.service

echo
echo "Done. Edit /etc/blackbox.toml and /etc/blackbox.env, then:"
echo "  sudo systemctl start blackbox && journalctl -fu blackbox"

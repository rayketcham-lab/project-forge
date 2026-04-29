#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "${SCRIPT_DIR}/project-forge-reload.service" /etc/systemd/system/
cp "${SCRIPT_DIR}/project-forge-reload.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl disable --now project-forge-reload.path 2>/dev/null || true
systemctl enable --now project-forge-reload.timer
systemctl restart project-forge-web
systemctl status project-forge-web --no-pager
echo ""
echo "Timer status:"
systemctl status project-forge-reload.timer --no-pager

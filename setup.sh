#!/usr/bin/env bash
# TodoBot deployment for Ubuntu 24.04: venv, web service, reminders, and safe Git sync.
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"
SERVICE_USER="${SUDO_USER:-${USER}}"

if [ "${EUID}" -ne 0 ]; then
    echo "Run as root (for example: sudo bash setup.sh)." >&2
    exit 1
fi

apt-get update -y
apt-get install -y python3 python3-venv git nginx
python3 -m venv "${VENV_DIR}"
"${PYTHON}" -m pip install --upgrade pip
if [ -f "${PROJECT_DIR}/requirements.txt" ]; then
    "${PYTHON}" -m pip install -r "${PROJECT_DIR}/requirements.txt"
fi

mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/uploads" "${PROJECT_DIR}/logs"
if [ ! -f "${PROJECT_DIR}/.env" ]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
fi

WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-8080}"
cat > /etc/systemd/system/todobot-web.service <<EOF
[Unit]
Description=TodoBot web application
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=-${PROJECT_DIR}/.env
ExecStart=${PYTHON} ${PROJECT_DIR}/app.py --host ${WEB_HOST} --port ${WEB_PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/todobot-reminder.service <<EOF
[Unit]
Description=TodoBot reminder worker
After=network.target

[Service]
Type=oneshot
User=${SERVICE_USER}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=-${PROJECT_DIR}/.env
ExecStart=${PYTHON} ${PROJECT_DIR}/worker.py
EOF

cat > /etc/systemd/system/todobot-reminder.timer <<'EOF'
[Unit]
Description=Run TodoBot reminders every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/todobot-git-sync.service <<EOF
[Unit]
Description=TodoBot safe Git synchronisation
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PYTHON} ${PROJECT_DIR}/git_sync.py
EOF

cat > /etc/systemd/system/todobot-git-sync.timer <<'EOF'
[Unit]
Description=Run safe TodoBot Git synchronisation periodically

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now todobot-web.service todobot-reminder.timer todobot-git-sync.timer

echo "TodoBot deployment complete."
echo "Configure .env, then place HTTPS/reverse proxy/VPN in front of the web service before IP access."
echo "Build the Android client with: cd android && ./gradlew assembleDebug"

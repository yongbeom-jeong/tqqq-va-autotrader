#!/bin/sh
set -eu

MODE="${1:-dry-run}"
case "$MODE" in
  dry-run|run) ;;
  *) echo "Usage: sudo ./deploy/install-systemd.sh [dry-run|run]" >&2; exit 2 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

REPO_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
SERVICE_USER="${SUDO_USER:-$(stat -c '%U' "$REPO_DIR")}"
DOCKER_BIN="$(command -v docker || true)"

if [ -z "$DOCKER_BIN" ]; then
  echo "Docker is not installed. Install Docker Engine and the Compose plugin first." >&2
  exit 1
fi
if ! "$DOCKER_BIN" compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin is not installed." >&2
  exit 1
fi
if [ ! -f "$REPO_DIR/.env" ]; then
  echo "Missing $REPO_DIR/.env. Copy .env.example and configure it first." >&2
  exit 1
fi
if [ ! -f "$REPO_DIR/data/va_path.csv" ]; then
  echo "Missing data/va_path.csv." >&2
  exit 1
fi
if [ "$MODE" = "run" ] && ! grep -Eq '^[[:space:]]*DRY_RUN[[:space:]]*=[[:space:]]*false[[:space:]]*$' "$REPO_DIR/.env"; then
  echo "Live mode requires DRY_RUN=false in .env." >&2
  exit 1
fi
if ! id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx docker; then
  echo "User $SERVICE_USER is not in the docker group." >&2
  echo "Run: sudo usermod -aG docker $SERVICE_USER, then log out and back in." >&2
  exit 1
fi

PUID_VALUE="$(id -u "$SERVICE_USER")"
PGID_VALUE="$(id -g "$SERVICE_USER")"
if ! grep -Eq '^[[:space:]]*PUID=' "$REPO_DIR/.env"; then
  printf '\nPUID=%s\n' "$PUID_VALUE" >> "$REPO_DIR/.env"
fi
if ! grep -Eq '^[[:space:]]*PGID=' "$REPO_DIR/.env"; then
  printf 'PGID=%s\n' "$PGID_VALUE" >> "$REPO_DIR/.env"
fi

cd "$REPO_DIR"
sudo -u "$SERVICE_USER" "$DOCKER_BIN" compose build trader
sudo -u "$SERVICE_USER" "$DOCKER_BIN" compose run --rm trader python main.py status

cat > /etc/systemd/system/tqqq-va-autotrader.service <<EOF
[Unit]
Description=TQQQ VA AutoTrader single ${MODE} cycle
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStartPre=/bin/sh ${REPO_DIR}/deploy/wait-for-network.sh openapi.tossinvest.com
ExecStart=${DOCKER_BIN} compose run --rm trader python main.py ${MODE}
TimeoutStartSec=15min
Nice=10

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/tqqq-va-autotrader.timer <<'EOF'
[Unit]
Description=TQQQ VA AutoTrader daily at 01:00 KST

[Timer]
OnCalendar=Tue..Sat *-*-* 01:00:00 Asia/Seoul
Persistent=false
AccuracySec=1s
RandomizedDelaySec=0
Unit=tqqq-va-autotrader.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now tqqq-va-autotrader.timer

echo "Installed in ${MODE} mode."
echo "Next run: systemctl list-timers tqqq-va-autotrader.timer"
echo "Logs: journalctl -u tqqq-va-autotrader.service -n 100 --no-pager"

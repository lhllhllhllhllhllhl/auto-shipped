#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_USER="${AUTO_SHIPPED_RUNNER_USER:-guanyi-runner}"
INSTALL_ROOT="${AUTO_SHIPPED_INSTALL_ROOT:-/opt/auto-shipped}"
STATE_ROOT="${AUTO_SHIPPED_STATE_ROOT:-/var/lib/auto-shipped}"
CONFIG_ROOT="${AUTO_SHIPPED_CONFIG_ROOT:-/etc/auto-shipped}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

[[ "${EUID}" -eq 0 ]] || { echo "provision.sh must run as root" >&2; exit 1; }
[[ "$RUNNER_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Invalid runner user" >&2; exit 2; }
[[ "$INSTALL_ROOT" == /opt/* ]] || { echo "INSTALL_ROOT must be below /opt" >&2; exit 2; }
[[ "$STATE_ROOT" == /var/lib/* ]] || { echo "STATE_ROOT must be below /var/lib" >&2; exit 2; }
[[ "$CONFIG_ROOT" == /etc/* ]] || { echo "CONFIG_ROOT must be below /etc" >&2; exit 2; }

. /etc/os-release
[[ "$ID" == "ubuntu" ]] || { echo "Only Ubuntu is supported" >&2; exit 1; }
case "$VERSION_ID" in
  22.04|24.04) ;;
  *) echo "Unsupported Ubuntu version: $VERSION_ID" >&2; exit 1 ;;
esac
[[ "$(uname -m)" == "x86_64" ]] || { echo "Google Chrome deployment requires x86_64" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git gnupg jq python3 python3-pip python3-venv \
  software-properties-common \
  rsync unzip xauth x11-utils xvfb fonts-noto-cjk

if ! command -v python3.11 >/dev/null 2>&1; then
  add-apt-repository -y ppa:deadsnakes/ppa
fi

install -d -m 0755 /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/nodesource.gpg ]]; then
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
  chmod 0644 /etc/apt/keyrings/nodesource.gpg
fi
printf '%s\n' \
  'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main' \
  > /etc/apt/sources.list.d/nodesource.list

if [[ ! -f /etc/apt/keyrings/google-chrome.gpg ]]; then
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
  chmod 0644 /etc/apt/keyrings/google-chrome.gpg
fi
printf '%s\n' \
  'deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main' \
  > /etc/apt/sources.list.d/google-chrome.list

apt-get update
apt-get install -y --no-install-recommends \
  nodejs google-chrome-stable python3.11 python3.11-venv

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$STATE_ROOT" --shell /usr/sbin/nologin "$RUNNER_USER"
fi

install -d -o root -g root -m 0755 "$INSTALL_ROOT" "$INSTALL_ROOT/releases"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 \
  "$STATE_ROOT" \
  "$STATE_ROOT/skill-state" \
  "$STATE_ROOT/inbox" \
  "$STATE_ROOT/work" \
  "$STATE_ROOT/guanyi-browser" \
  "$STATE_ROOT/guanyi-browser/profile" \
  "$STATE_ROOT/guanyi-browser/artifacts" \
  "$STATE_ROOT/guanyi-browser/operations" \
  "$STATE_ROOT/guanyi-browser/audit-operations" \
  "$STATE_ROOT/guanyi-browser/login-operations"

install -d -o root -g "$RUNNER_USER" -m 0750 "$CONFIG_ROOT"
if [[ ! -f "$CONFIG_ROOT/guanyi-server.env" ]]; then
  sed "s#/var/lib/auto-shipped#$STATE_ROOT#g" \
    "$SCRIPT_DIR/guanyi-server.env.example" \
    > "$CONFIG_ROOT/guanyi-server.env"
  chown root:"$RUNNER_USER" "$CONFIG_ROOT/guanyi-server.env"
  chmod 0640 "$CONFIG_ROOT/guanyi-server.env"
fi

sed "s/@@RUNNER_USER@@/$RUNNER_USER/g" \
  "$SCRIPT_DIR/auto-shipped-xvfb.service" \
  > /etc/systemd/system/auto-shipped-xvfb.service
chmod 0644 /etc/systemd/system/auto-shipped-xvfb.service
systemctl daemon-reload
systemctl enable auto-shipped-xvfb.service
systemctl restart auto-shipped-xvfb.service

display_ready=0
for _ in {1..20}; do
  if sudo -u "$RUNNER_USER" env DISPLAY=:99 xdpyinfo >/dev/null 2>&1; then
    display_ready=1
    break
  fi
  sleep 0.25
done
[[ "$display_ready" == "1" ]] || {
  systemctl status auto-shipped-xvfb.service --no-pager -l >&2 || true
  echo "Xvfb display :99 is not reachable by the runner" >&2
  exit 1
}

node_major="$(node -p 'process.versions.node.split(`.`)[0]')"
((node_major >= 18)) || { echo "Node.js 18+ is required" >&2; exit 1; }
google-chrome-stable --version
node --version
npm --version
python3.11 --version
systemctl is-active --quiet auto-shipped-xvfb.service
echo "provision_status=ready"

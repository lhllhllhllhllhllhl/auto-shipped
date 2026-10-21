#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_USER="${AUTO_SHIPPED_RUNNER_USER:-guanyi-runner}"
INSTALL_ROOT="${AUTO_SHIPPED_INSTALL_ROOT:-/opt/auto-shipped}"
STATE_ROOT="${AUTO_SHIPPED_STATE_ROOT:-/var/lib/auto-shipped}"
CONFIG_FILE="${AUTO_SHIPPED_CONFIG_FILE:-/etc/auto-shipped/guanyi-server.env}"

failures=0
check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf '%s=ready\n' "$label"
  else
    printf '%s=failed\n' "$label"
    failures=$((failures + 1))
  fi
}

check current_release test -L "$INSTALL_ROOT/current"
check server_env test -r "$CONFIG_FILE"
check runner_user id "$RUNNER_USER"
check node node --version
check npm npm --version
check python python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
check chrome google-chrome-stable --version
check xvfb systemctl is-active --quiet auto-shipped-xvfb.service
check display_socket test -S /tmp/.X11-unix/X99
check private_state sudo -u "$RUNNER_USER" test -w "$STATE_ROOT"

if [[ -L "$INSTALL_ROOT/current" && -r "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$CONFIG_FILE"
  set +a
  if sudo -u "$RUNNER_USER" env \
    HOME="$STATE_ROOT" \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    SHANGYU_SKILL_STATE_ROOT="$SHANGYU_SKILL_STATE_ROOT" \
    GUANYI_BROWSER_BACKEND="$GUANYI_BROWSER_BACKEND" \
    GUANYI_CHROME_EXECUTABLE="$GUANYI_CHROME_EXECUTABLE" \
    python3 "$INSTALL_ROOT/current/skill/shangyu-order-converter/scripts/doctor.py"; then
    printf 'skill_doctor=ready\n'
  else
    printf 'skill_doctor=failed\n'
    failures=$((failures + 1))
  fi
fi

if ((failures)); then
  printf 'server_doctor=failed\n' >&2
  exit 1
fi
printf 'server_doctor=ready\n'

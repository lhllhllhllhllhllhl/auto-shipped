#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-}"
OPERATION_ID="${2:-}"
RUNNER_USER="${AUTO_SHIPPED_RUNNER_USER:-guanyi-runner}"
INSTALL_ROOT="${AUTO_SHIPPED_INSTALL_ROOT:-/opt/auto-shipped}"
STATE_ROOT="${AUTO_SHIPPED_STATE_ROOT:-/var/lib/auto-shipped}"
CONFIG_FILE="${AUTO_SHIPPED_CONFIG_FILE:-/etc/auto-shipped/guanyi-server.env}"

[[ "${EUID}" -eq 0 ]] || { echo '{"status":"failed","code":"REMOTE_ROOT_REQUIRED"}' >&2; exit 1; }
[[ "$ACTION" =~ ^(health|init|put-manifest|put-artifact|verify|status|prepare|execute|reconcile|resolve-no-task|prepare-retry|probe|login-start|login-status|login-credentials|login-sms-request|login-sms-submit)$ ]] || {
  echo '{"status":"failed","code":"REMOTE_ACTION_INVALID"}'
  exit 2
}
[[ "$OPERATION_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo '{"status":"failed","code":"REMOTE_OPERATION_ID_INVALID"}'
  exit 2
}
[[ -f "$CONFIG_FILE" ]] || { echo '{"status":"failed","code":"REMOTE_CONFIG_MISSING"}'; exit 2; }
[[ -f "$INSTALL_ROOT/current/browser/guanyi/remote/server_runner.py" ]] || {
  echo '{"status":"failed","code":"REMOTE_RUNNER_MISSING"}'
  exit 2
}

set -a
# shellcheck disable=SC1090
. "$CONFIG_FILE"
set +a

cd "$INSTALL_ROOT/current"
exec sudo -n -u "$RUNNER_USER" env -i \
  HOME="$STATE_ROOT" \
  PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  PYTHONPATH="$INSTALL_ROOT/current" \
  SHANGYU_SKILL_STATE_ROOT="${SHANGYU_SKILL_STATE_ROOT:-$STATE_ROOT/skill-state}" \
  GUANYI_REMOTE_JOB_ROOT="${GUANYI_REMOTE_JOB_ROOT:-$STATE_ROOT/inbox}" \
  GUANYI_BROWSER_BACKEND="${GUANYI_BROWSER_BACKEND:-persistent}" \
  GUANYI_CHROME_EXECUTABLE="${GUANYI_CHROME_EXECUTABLE:-/usr/bin/google-chrome-stable}" \
  GUANYI_RUNTIME_ROOT="${GUANYI_RUNTIME_ROOT:-$STATE_ROOT/guanyi-browser}" \
  GUANYI_PROFILE_DIR="${GUANYI_PROFILE_DIR:-$STATE_ROOT/guanyi-browser/profile}" \
  GUANYI_ARTIFACT_DIR="${GUANYI_ARTIFACT_DIR:-$STATE_ROOT/guanyi-browser/artifacts}" \
  GUANYI_OPERATION_DIR="${GUANYI_OPERATION_DIR:-$STATE_ROOT/guanyi-browser/operations}" \
  GUANYI_AUDIT_OPERATION_DIR="${GUANYI_AUDIT_OPERATION_DIR:-$STATE_ROOT/guanyi-browser/audit-operations}" \
  GUANYI_LOGIN_OPERATION_DIR="${GUANYI_LOGIN_OPERATION_DIR:-$STATE_ROOT/guanyi-browser/login-operations}" \
  GUANYI_BROWSER_SESSION_LOCK="${GUANYI_BROWSER_SESSION_LOCK:-$STATE_ROOT/guanyi-browser/profile.lock}" \
  GUANYI_CREDENTIAL_HELPER="${GUANYI_CREDENTIAL_HELPER:-/usr/local/bin/guanyi-credential-helper}" \
  GUANYI_RESIDENT_SESSION_TTL_MS="${GUANYI_RESIDENT_SESSION_TTL_MS:-31536000000}" \
  GUANYI_HUMAN_TAKEOVER_AVAILABLE="${GUANYI_HUMAN_TAKEOVER_AVAILABLE:-0}" \
  GUANYI_WINDOW_WIDTH="${GUANYI_WINDOW_WIDTH:-1440}" \
  GUANYI_WINDOW_HEIGHT="${GUANYI_WINDOW_HEIGHT:-900}" \
  GUANYI_WORKFLOW_RECONCILIATION_TIMEOUT_MS="${GUANYI_WORKFLOW_RECONCILIATION_TIMEOUT_MS:-120000}" \
  NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}" \
  DISPLAY="${DISPLAY:-:99}" \
  python3.11 -m browser.guanyi.remote.server_runner "$ACTION" "$OPERATION_ID"

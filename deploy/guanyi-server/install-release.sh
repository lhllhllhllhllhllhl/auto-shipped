#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE="${1:-}"
RELEASE_ID="${2:-}"
RUNNER_USER="${AUTO_SHIPPED_RUNNER_USER:-guanyi-runner}"
INSTALL_ROOT="${AUTO_SHIPPED_INSTALL_ROOT:-/opt/auto-shipped}"
STATE_ROOT="${AUTO_SHIPPED_STATE_ROOT:-/var/lib/auto-shipped}"
CONFIG_FILE="${AUTO_SHIPPED_CONFIG_FILE:-/etc/auto-shipped/guanyi-server.env}"

[[ "${EUID}" -eq 0 ]] || { echo "install-release.sh must run as root" >&2; exit 1; }
[[ -f "$ARCHIVE" ]] || { echo "Release archive not found" >&2; exit 2; }
[[ "$RELEASE_ID" =~ ^[0-9a-f]{7,64}$ ]] || { echo "Invalid release id" >&2; exit 2; }
[[ -f "$CONFIG_FILE" ]] || { echo "Server environment file missing" >&2; exit 2; }
id "$RUNNER_USER" >/dev/null 2>&1 || { echo "Runner user missing" >&2; exit 2; }

release_dir="$INSTALL_ROOT/releases/$RELEASE_ID"
if [[ ! -d "$release_dir" ]]; then
  staging="$(mktemp -d "$INSTALL_ROOT/releases/.staging.XXXXXX")"
  cleanup() { rm -rf -- "$staging"; }
  trap cleanup EXIT
  tar -xzf "$ARCHIVE" -C "$staging"
  [[ -f "$staging/pyproject.toml" ]] || { echo "Invalid release: pyproject.toml missing" >&2; exit 1; }
  [[ -f "$staging/browser/guanyi/package.json" ]] || { echo "Invalid release: browser runtime missing" >&2; exit 1; }
  [[ -f "$staging/skill/shangyu-order-converter/runtime-manifest.json" ]] || { echo "Invalid release: Skill manifest missing" >&2; exit 1; }
  chown -R root:root "$staging"
  chmod -R go-w "$staging"
  mv "$staging" "$release_dir"
  trap - EXIT
fi

ln -sfn "$release_dir" "$INSTALL_ROOT/current.next"
mv -Tf "$INSTALL_ROOT/current.next" "$INSTALL_ROOT/current"

sudo -u "$RUNNER_USER" env -i \
  HOME="$STATE_ROOT" \
  PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  SHANGYU_SKILL_STATE_ROOT="$STATE_ROOT/skill-state" \
  python3 "$INSTALL_ROOT/current/skill/shangyu-order-converter/scripts/bootstrap_runtime.py"

"$INSTALL_ROOT/current/deploy/guanyi-server/doctor.sh"
printf 'release_status=ready\nrelease_id=%s\n' "$RELEASE_ID"

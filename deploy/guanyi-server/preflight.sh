#!/usr/bin/env bash
set -Eeuo pipefail

HOST=""
SSH_USER="ubuntu"
IDENTITY=""

usage() {
  echo "Usage: $0 --host HOST [--user USER] [--identity PRIVATE_KEY]" >&2
}

while (($#)); do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --user) SSH_USER="${2:-}"; shift 2 ;;
    --identity) IDENTITY="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -n "$HOST" ]] || { usage; exit 2; }
[[ "$SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Invalid SSH user" >&2; exit 2; }
if [[ -n "$IDENTITY" ]]; then
  [[ -f "$IDENTITY" ]] || { echo "Private key not found: $IDENTITY" >&2; exit 2; }
fi

SSH_ARGS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
if [[ -n "$IDENTITY" ]]; then
  SSH_ARGS+=(-o IdentitiesOnly=yes -i "$IDENTITY")
fi

ssh "${SSH_ARGS[@]}" "${SSH_USER}@${HOST}" 'bash -s' <<'REMOTE'
set -Eeuo pipefail
. /etc/os-release
printf 'ssh_ok=yes\n'
printf 'os_id=%s\n' "$ID"
printf 'os_version=%s\n' "$VERSION_ID"
printf 'architecture=%s\n' "$(uname -m)"
printf 'cpu_count=%s\n' "$(getconf _NPROCESSORS_ONLN)"
printf 'memory_kib=%s\n' "$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
printf 'root_free_kib=%s\n' "$(df -Pk / | awk 'NR==2 {print $4}')"
if sudo -n true 2>/dev/null; then
  printf 'passwordless_sudo=yes\n'
else
  printf 'passwordless_sudo=no\n'
fi
REMOTE

#!/usr/bin/env bash
set -Eeuo pipefail

HOST=""
SSH_USER="ubuntu"
IDENTITY=""
REF="HEAD"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"

usage() {
  echo "Usage: $0 --host HOST [--user USER] [--identity PRIVATE_KEY] [--ref COMMIT_OR_TAG]" >&2
}

while (($#)); do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --user) SSH_USER="${2:-}"; shift 2 ;;
    --identity) IDENTITY="${2:-}"; shift 2 ;;
    --ref) REF="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -n "$HOST" ]] || { usage; exit 2; }
[[ "$SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Invalid SSH user" >&2; exit 2; }
if [[ -n "$IDENTITY" ]]; then
  [[ -f "$IDENTITY" ]] || { echo "Private key not found: $IDENTITY" >&2; exit 2; }
fi

release_id="$(git -C "$REPO_ROOT" rev-parse --verify "${REF}^{commit}")"
git -C "$REPO_ROOT" diff --quiet --exit-code -- . || {
  echo "Tracked worktree changes exist; deploy only a reviewed commit." >&2
  exit 1
}

SSH_ARGS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
SCP_ARGS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
if [[ -n "$IDENTITY" ]]; then
  SSH_ARGS+=(-o IdentitiesOnly=yes -i "$IDENTITY")
  SCP_ARGS+=(-o IdentitiesOnly=yes -i "$IDENTITY")
fi

preflight_args=(--host "$HOST" --user "$SSH_USER")
if [[ -n "$IDENTITY" ]]; then
  preflight_args+=(--identity "$IDENTITY")
fi
"$SCRIPT_DIR/preflight.sh" "${preflight_args[@]}"

tmp_dir="$(mktemp -d)"
cleanup() { rm -rf -- "$tmp_dir"; }
trap cleanup EXIT
archive="$tmp_dir/auto-shipped-$release_id.tar.gz"
git -C "$REPO_ROOT" archive --format=tar.gz --output="$archive" "$release_id"

remote_stage="/tmp/auto-shipped-deploy-$release_id"
ssh "${SSH_ARGS[@]}" "${SSH_USER}@${HOST}" "install -d -m 0700 '$remote_stage'"
scp "${SCP_ARGS[@]}" \
  "$SCRIPT_DIR/provision.sh" \
  "$SCRIPT_DIR/install-release.sh" \
  "$SCRIPT_DIR/doctor.sh" \
  "$SCRIPT_DIR/guanyi-server.env.example" \
  "$SCRIPT_DIR/auto-shipped-xvfb.service" \
  "$archive" \
  "${SSH_USER}@${HOST}:$remote_stage/"

ssh "${SSH_ARGS[@]}" "${SSH_USER}@${HOST}" \
  "sudo -n bash '$remote_stage/provision.sh' && sudo -n bash '$remote_stage/install-release.sh' '$remote_stage/$(basename "$archive")' '$release_id'"

printf 'deployment_status=ready\nhost=%s\nrelease_id=%s\n' "$HOST" "$release_id"

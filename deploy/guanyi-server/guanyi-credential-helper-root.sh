#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-}"
CREDENTIAL_ROOT="${AUTO_SHIPPED_CREDENTIAL_ROOT:-/etc/auto-shipped/credentials}"
KEY_FILE="$CREDENTIAL_ROOT/guanyi.key"
SECRET_FILE="$CREDENTIAL_ROOT/guanyi.json.enc"

[[ "${EUID}" -eq 0 ]] || exit 1
[[ "$ACTION" == "get" ]] || exit 2
[[ -f "$KEY_FILE" && -f "$SECRET_FILE" ]] || exit 3

exec openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass "file:$KEY_FILE" \
  -in "$SECRET_FILE"

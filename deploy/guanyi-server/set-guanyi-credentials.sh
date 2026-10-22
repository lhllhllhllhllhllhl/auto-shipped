#!/usr/bin/env bash
set -Eeuo pipefail

ACTION="${1:-set}"
CREDENTIAL_ROOT="${AUTO_SHIPPED_CREDENTIAL_ROOT:-/etc/auto-shipped/credentials}"
KEY_FILE="$CREDENTIAL_ROOT/guanyi.key"
SECRET_FILE="$CREDENTIAL_ROOT/guanyi.json.enc"

[[ "${EUID}" -eq 0 ]] || { echo "set-guanyi-credentials.sh must run as root" >&2; exit 1; }
install -d -o root -g root -m 0700 "$CREDENTIAL_ROOT"

case "$ACTION" in
  set)
    temporary="$(mktemp "$CREDENTIAL_ROOT/.guanyi.XXXXXX")"
    encrypted="$(mktemp "$CREDENTIAL_ROOT/.guanyi.enc.XXXXXX")"
    cleanup() { rm -f -- "$temporary" "$encrypted"; }
    trap cleanup EXIT
    chmod 0600 "$temporary" "$encrypted"
    dd of="$temporary" bs=16385 count=1 status=none
    (( $(wc -c < "$temporary") <= 16384 )) || {
      echo "credential_status=too_large" >&2
      exit 2
    }
    jq -e '
      type == "object" and
      (keys | sort) == ["password", "username"] and
      (.username | type == "string" and length > 0) and
      (.password | type == "string" and length > 0)
    ' "$temporary" >/dev/null || { echo "credential_status=invalid" >&2; exit 2; }
    if [[ ! -f "$KEY_FILE" ]]; then
      umask 077
      openssl rand -hex 32 > "$KEY_FILE"
      chown root:root "$KEY_FILE"
      chmod 0600 "$KEY_FILE"
    fi
    openssl enc -aes-256-cbc -pbkdf2 -salt \
      -pass "file:$KEY_FILE" \
      -in "$temporary" \
      -out "$encrypted"
    chown root:root "$encrypted"
    chmod 0600 "$encrypted"
    mv -f -- "$encrypted" "$SECRET_FILE"
    trap - EXIT
    rm -f -- "$temporary"
    echo "credential_status=configured"
    ;;
  status)
    if [[ -f "$KEY_FILE" && -f "$SECRET_FILE" ]]; then
      echo "credential_status=configured"
    else
      echo "credential_status=missing"
      exit 3
    fi
    ;;
  delete)
    rm -f -- "$SECRET_FILE"
    echo "credential_status=deleted"
    ;;
  *)
    echo "Usage: $0 set|status|delete" >&2
    exit 2
    ;;
esac

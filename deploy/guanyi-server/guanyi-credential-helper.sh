#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${1:-}" == "get" && "$#" -eq 1 ]] || exit 2
exec sudo -n /usr/local/libexec/auto-shipped-guanyi-credential-helper get

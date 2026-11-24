#!/bin/sh
set -eu
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
case "${1:-}" in
  --status|--check) cmd=${1#--}; shift; exec python3 -IB "$ROOT/run.py" "$cmd" "$@" ;;
esac
exec python3 -IB "$ROOT/scripts/bootstrap_install.py" "$@"

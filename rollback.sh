#!/usr/bin/env bash
set -euo pipefail
umask 077
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$BASE/release_tools.py" rollback "$@"

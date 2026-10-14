#!/usr/bin/env bash
set -euo pipefail
umask 077
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV="$BASE/.venv"
MODE="${1:-}"
if [[ -n "$MODE" && "$MODE" != "--upgrade" && "$MODE" != "--check" ]]; then
  printf '%s\n' 'Pemakaian: bash setup.sh [--check | --upgrade]' >&2
  exit 2
fi
mkdir -p -- "$BASE/.state"
export PIP_CACHE_DIR="$BASE/.state/pip-cache"
exec 9>"$BASE/.state/run.lock"
if ! flock -n 9; then
  printf '%s\n' 'Alat masih berjalan. Hentikan atau tunggu proses selesai sebelum setup/pembaruan.' >&2
  exit 1
fi
if [[ -f "$VENV/.tool-location" ]] && [[ "$(cat "$VENV/.tool-location")" != "$BASE" ]]; then
  if [[ "$MODE" == "--check" ]]; then
    printf '%s\n' 'Lokasi berubah. Jalankan bash setup.sh untuk membuat ulang lingkungan Python.' >&2
    exit 1
  fi
  BACKUP="$BASE/.state/venv-before-move-$(date +%Y%m%d-%H%M%S)"
  mv -- "$VENV" "$BACKUP"
  printf '%s\n' 'Lokasi alat berubah; lingkungan Python akan dibuat ulang di lokasi baru.'
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  if [[ "$MODE" == "--check" ]]; then
    printf '%s\n' 'Belum ada lingkungan Python. Jalankan bash setup.sh.' >&2
    exit 1
  fi
  if ! python3 -m venv --without-pip "$VENV"; then
    printf '%s\n' 'Pembuatan lingkungan Python gagal. Di Ubuntu, pasang dukungan venv:' 'sudo apt install python3-venv' 'Lalu jalankan bash setup.sh lagi.' >&2
    exit 1
  fi
fi
PY="$VENV/bin/python"
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  if [[ "$MODE" == "--check" ]]; then
    printf '%s\n' 'pip belum tersedia. Jalankan bash setup.sh.' >&2
    exit 1
  fi
  BOOTSTRAP="$BASE/.bootstrap/pip-26.2.1-py3-none-any.whl"
  if [[ -f "$BOOTSTRAP" ]]; then
    (cd "$BASE/.bootstrap" && sha256sum -c SHA256SUMS)
    "$PY" -c 'import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("pip",run_name="__main__")' "$BOOTSTRAP" install --no-index --no-deps "$BOOTSTRAP"
  elif ! "$PY" -m ensurepip --upgrade; then
    printf '%s\n' 'Folder .bootstrap belum ikut disalin. Salin seluruh paket alat termasuk file tersembunyi, lalu jalankan setup lagi.' >&2
    exit 1
  fi
fi
if [[ "$MODE" == "--upgrade" ]]; then
  "$PY" -m pip install --upgrade -r "$BASE/requirements.txt"
elif ! "$PY" - "$BASE/requirements.txt" >/dev/null 2>&1 <<'PY'
import sys
from importlib.metadata import version
import gallery_dl, requests, rich
for line in open(sys.argv[1]):
    line = line.strip()
    if line and not line.startswith('#'):
        name, _, wanted = line.partition('==')
        assert version(name) == wanted, name
PY
then
  if [[ "$MODE" == "--check" ]]; then
    printf '%s\n' 'Paket belum lengkap. Jalankan bash setup.sh.' >&2
    exit 1
  fi
  "$PY" -m pip install -r "$BASE/requirements.txt"
fi
"$PY" -m pip check
"$PY" -m gallery_dl --version
if [[ "$MODE" != "--check" && ! -e "$BASE/settings.json" ]]; then
  cp -- "$BASE/settings.example.json" "$BASE/settings.json"
fi
printf '%s\n' "$BASE" > "$VENV/.tool-location"
printf '%s\n' 'Setup siap. Berikutnya: ./x-bookmarks status'

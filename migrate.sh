#!/usr/bin/env bash
set -euo pipefail
umask 077
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [[ $# != 1 ]]; then
  printf '%s\n' 'Pemakaian: bash migrate.sh "$HOME/tools/x-bookmarks"' >&2
  exit 2
fi
DEST=$(realpath -m -- "$1")
case "$DEST/" in
  "$BASE/"*) printf '%s\n' 'Pilih folder tujuan di luar folder alat sekarang.' >&2; exit 2 ;;
esac
if ! command -v rsync >/dev/null; then
  printf '%s\n' 'rsync belum tersedia. Pasang dengan sudo apt install rsync lalu ulangi.' >&2
  exit 1
fi
if [[ -e "$DEST" && ! -d "$DEST" ]]; then
  printf '%s\n' 'Tujuan bukan folder.' >&2
  exit 1
fi
if [[ -d "$DEST" && -n "$(find "$DEST" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  printf '%s\n' 'Folder tujuan sudah berisi file. Pilih folder kosong; migrasi tidak menimpa isinya.' >&2
  exit 1
fi
mkdir -p -- "$BASE/.state"
exec 9>"$BASE/.state/run.lock"
if ! flock -n 9; then
  printf '%s\n' 'Alat masih berjalan. Hentikan atau tunggu proses selesai sebelum migrasi.' >&2
  exit 1
fi
mkdir -p -- "$DEST"
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='dist' --exclude='run.lock' "$BASE/" "$DEST/"
# Copied rollback records must point to the copied backup, not the old checkout.
python3 - "$BASE" "$DEST" <<'PY'
import json,sys
from pathlib import Path
source,dest=map(Path,sys.argv[1:])
for name in ('last-update.json','update-transaction.json'):
    record=dest/'.state'/name
    if record.exists():
        info=json.loads(record.read_text())
        backup=Path(info['backup'])
        if backup.is_relative_to(source):
            info['backup']=str(dest/backup.relative_to(source))
            record.write_text(json.dumps(info,indent=2)+'\n')
PY
bash "$DEST/setup.sh"
"$DEST/x-bookmarks" status
printf '\n%s\n' "Salinan siap di: $DEST" 'Folder lama tetap ada. Verifikasi hasil dan gunakan salinan baru sebelum menghapus folder lama.'

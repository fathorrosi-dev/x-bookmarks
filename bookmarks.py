#!/usr/bin/env python3
"""Select X bookmarks by local bookmark date, then download their threads."""
import argparse
import csv
from contextlib import closing, contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
import fcntl
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import select
import shlex
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
UTC = timezone.utc
STATE = BASE / '.state'
MEDIA = BASE / 'media'
VERSION = '3.1.1'

from cli_ui import UI, BLUE, RED, size
from media_layout import ensure_layout, is_partial, media_files, media_kind, organize

UI_INSTANCE = None

READ_ONLY = frozenset({'status', 'list'})  # never write shared state, so a running download must not block them


@contextmanager
def exclusive_lock(path):
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Proses lain masih berjalan. Tunggu atau hentikan proses tersebut dahulu.') from exc
        yield


def say(value, style=None):
    if UI_INSTANCE:
        UI_INSTANCE.say(value, style)
    else:
        print(value, flush=True)


def write_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def numeric_id(value, field, tweet):
    try:
        return str(int(value))
    except (TypeError, ValueError):
        raise ValueError(f'{field} tidak valid pada tweet {tweet}: {value!r}. Jalankan scan ulang; jika tetap sama, periksa snapshot.') from None


def csv_cell(value):
    """Spreadsheet apps read a leading = + - @ (or tab/CR) as a formula; keep such tweet text as text in the CSV."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError('date_bookmarked hilang atau bukan timestamp.')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)  # gallery-dl emits naive UTC.
    return result.astimezone(UTC)


def bounds(settings):
    tz = ZoneInfo(settings['timezone'])
    start = datetime.combine(date.fromisoformat(settings['start']), datetime.min.time(), tz)
    end = datetime.combine(date.fromisoformat(settings['end']) + timedelta(days=1), datetime.min.time(), tz)
    if end <= start:
        raise ValueError('Tanggal akhir harus sama dengan atau setelah tanggal awal.')
    return start.astimezone(UTC), end.astimezone(UTC), tz


def directory_posts(path):
    """Accept JSON arrays and legacy JSONL; reject extractor error records."""
    raw = path.read_text(encoding='utf-8')
    try:
        rows = json.loads(raw)
        if rows and isinstance(rows[0], int):
            rows = [rows]
    except json.JSONDecodeError:
        try:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise ValueError('Snapshot JSON rusak atau scan belum lengkap.') from exc
    if not isinstance(rows, list):
        raise ValueError('Format snapshot tidak dikenali.')
    seen = set()
    for row in rows:
        if not isinstance(row, list) or not row:
            raise ValueError('Format pesan gallery-dl tidak dikenali.')
        if row[0] == -1:
            raise ValueError('gallery-dl melaporkan error; snapshot sebelumnya dipertahankan.')
        if row[0] != 2:
            continue
        post = row[-1]
        if not isinstance(post, dict) or not post.get('tweet_id'):
            raise ValueError('Metadata tweet tidak lengkap.')
        key = str(post['tweet_id'])
        if key not in seen:
            seen.add(key)
            yield post


def select_posts(posts, settings):
    start, end, tz = bounds(settings)
    selected = []
    scanned = 0
    for post in posts:
        scanned += 1
        when = parse_timestamp(post.get('date_bookmarked'))
        if when.year < 2006 or when > datetime.now(UTC) + timedelta(days=1):
            raise ValueError('Tanggal bookmark tidak masuk akal. Periksa sampel sebelum download.')
        if not start <= when < end:
            continue
        tid = numeric_id(post.get('tweet_id'), 'tweet_id', post.get('tweet_id'))
        cid = numeric_id(post.get('conversation_id') or tid, 'conversation_id', tid)
        author = post.get('author') or {}
        if not author.get('id'):
            raise ValueError(f'ID penulis tidak tersedia untuk tweet {tid}.')
        selected.append({
            'tweet_id': tid,
            'conversation_id': cid,
            'author_id': numeric_id(author['id'], 'author.id', tid),
            'author_name': author.get('name', ''),
            'bookmarked_at': when.astimezone(tz).isoformat(),
            'posted_at': post.get('date'),
            'url': f'https://x.com/i/web/status/{tid}',
            'content': post.get('content', ''),
        })
    selected.sort(key=lambda p: (p['bookmarked_at'], p['tweet_id']))
    return selected, scanned


def group_threads(bookmarks):
    threads = {}
    for item in bookmarks:
        cid = str(int(item['conversation_id']))
        thread = threads.setdefault(cid, {'conversation_id': cid, 'author_ids': [], 'bookmarks': []})
        aid = str(int(item['author_id']))
        if aid not in thread['author_ids']:
            thread['author_ids'].append(aid)
        thread['bookmarks'].append(item)
    return list(threads.values())


def resolve_browser(value):
    if value != 'firefox':
        return value
    matches = []
    roots = [Path.home() / 'snap/firefox/common/.mozilla/firefox', Path.home() / '.mozilla/firefox']
    for root in roots:
        for cookie_file in sorted(root.glob('*/cookies.sqlite')):
            try:
                with tempfile.TemporaryDirectory(dir=STATE) as temp:
                    copy = Path(temp) / 'cookies.sqlite'
                    shutil.copyfile(cookie_file, copy)
                    wal = cookie_file.with_name('cookies.sqlite-wal')
                    if wal.exists():
                        shutil.copyfile(wal, copy.with_name('cookies.sqlite-wal'))
                    with closing(sqlite3.connect(copy)) as con:
                        count = con.execute("SELECT COUNT(*) FROM moz_cookies WHERE host IN ('.x.com','x.com') AND name='auth_token'").fetchone()[0]
                    if count:
                        matches.append(cookie_file.parent)
            except (sqlite3.Error, OSError):
                continue
    if not matches:
        raise ValueError('Sesi X tidak ditemukan di Firefox. Login X dalam Firefox biasa, lalu tutup Firefox dan coba lagi. Profil khusus dapat dipilih dengan --browser firefox/.x.com:/path/profil.')
    if len(matches) > 1:
        raise ValueError('Beberapa profil Firefox memiliki sesi X. Pilih dengan --browser firefox/.x.com:/path/profil. Kandidat: ' + ', '.join(map(str, matches)))
    say(f'Profil Firefox: {matches[0]}')
    return f'firefox/.x.com:{matches[0]}'


def gallery_args(settings, options):
    """gallery-dl options only; run_gallery adds the interpreter and worker script."""
    if importlib.util.find_spec('gallery_dl') is None:
        raise ValueError('gallery-dl belum terpasang. Jalankan: bash setup.sh')
    return ['--config-ignore', '-c', str(BASE / 'gallery-dl.json'),
            '--cache-file', str(STATE / 'cache.sqlite3'), '--no-postprocessors',
            '--cookies-from-browser', settings['browser'], '--no-input', '--no-colors', *options]


def load_settings():
    path = BASE / 'settings.json'
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ValueError('settings.json belum ada. Jalankan: bash setup.sh') from None
    except json.JSONDecodeError as exc:
        raise ValueError(f'settings.json tidak valid ({exc.msg}, baris {exc.lineno}). '
                         'Perbaiki berkasnya atau salin ulang dari settings.example.json.') from None
    missing = [key for key in ('start', 'end', 'timezone', 'browser')
               if not isinstance(settings, dict) or key not in settings]
    if missing:
        raise ValueError('settings.json belum memuat: ' + ', '.join(missing)
                         + '. Salin contohnya dari settings.example.json.')
    return settings


def stop_worker(process):
    """Stop the isolated process group, including any child helpers."""
    for sig, timeout in ((signal.SIGINT, 5), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass


def run_gallery(args, log_path, stdout=None, ui=None):
    """Separate native stdout/logs from an inherited structured-event pipe."""
    read_fd, write_fd = os.pipe()
    process = None
    done = None
    protocol_error = False
    pending = b''
    try:
        with log_path.open('a', encoding='utf-8') as log:
            worker = [sys.executable, str(BASE / 'gallery_worker.py'), str(write_fd), *args]
            process = subprocess.Popen(worker, stdout=stdout if stdout is not None else log,
                                       stderr=log, pass_fds=(write_fd,), start_new_session=True)
            os.close(write_fd)
            write_fd = None
            while True:
                ready, _, _ = select.select([read_fd], [], [], 0.25)
                if not ready:
                    if process.poll() is not None:
                        break
                    continue
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                pending += chunk
                if len(pending) > 1024 * 1024:
                    raise ValueError('Kanal event worker terlalu besar; lihat log.')
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                            raise ValueError('Event tidak valid')
                        if event['type'] == 'done':
                            if type(event.get('code')) is not int:
                                raise ValueError('Kode worker tidak valid')
                            done = event['code']
                        elif ui:
                            ui.event(event)
                    except (ValueError, UnicodeError):
                        protocol_error = True
                        log.write('\n[error] Event worker tidak valid.\n')
            code = process.wait()
            if pending or done is None or protocol_error:
                log.write('\n[error] Worker berhenti tanpa laporan lengkap.\n')
                return code or 1
            return code or done
    except BaseException:
        if process:
            stop_worker(process)
        raise
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def extract(settings, path, sample=False, ui=None):
    # Array output includes DataJob's -1 error record. JSONL can omit it
    # when an exception happens after several successful posts.
    options = ['--dump-json', '-o', 'output.jsonl=false']
    if sample:
        options += ['--post-range', '1-5']
    options += ['https://x.com/i/bookmarks']
    args = gallery_args(settings, options)
    temp = path.with_name(path.name + '.partial')
    message = 'Mengambil sampel 5 bookmark' if sample else 'Mengambil metadata bookmark'
    if not ui:
        say(message)
    with (ui.activity(message) if ui else nullcontext()):
        with temp.open('w', encoding='utf-8') as stream:
            kwargs = {'ui': ui} if ui else {}
            code = run_gallery(args, STATE / 'scan.log', stdout=stream, **kwargs)
    if code:
        raise ValueError(f'Scan gagal. Snapshot sebelumnya tetap tersimpan; periksa {STATE / "scan.log"}.')
    posts = list(directory_posts(temp))  # Also detects DataJob errors despite exit code 0.
    if not posts:
        raise ValueError('X tidak mengembalikan bookmark. Periksa akun/profil Firefox yang digunakan.')
    for post in posts:
        when = parse_timestamp(post.get('date_bookmarked'))
        if when.year < 2006 or when > datetime.now(UTC) + timedelta(days=1):
            raise ValueError('Tanggal bookmark tidak valid. Jangan lanjutkan sebelum memeriksa profil/API.')
    temp.replace(path)
    return posts


def create_selection(settings, root):
    path = root / 'bookmarks.json'
    if not path.exists():
        raise ValueError('Belum ada hasil scan. Jalankan: ./x-bookmarks scan')
    selected, scanned = select_posts(directory_posts(path), settings)
    dest = root / 'selections' / range_key(settings)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {
        'start': settings['start'], 'end_inclusive': settings['end'], 'timezone': settings['timezone'],
        'generated_at': datetime.now(UTC).isoformat(), 'scanned_bookmarks': scanned,
        'snapshot_at': datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        'selected_bookmarks': selected, 'threads': group_threads(selected),
    }
    write_json(dest / 'selection.json', manifest)
    with (dest / 'daftar-bookmark.csv').open('w', newline='', encoding='utf-8') as stream:
        cols = ['bookmarked_at', 'author_name', 'url', 'conversation_id', 'posted_at', 'content']
        writer = csv.DictWriter(stream, fieldnames=cols, extrasaction='ignore')
        writer.writeheader()
        writer.writerows({key: csv_cell(value) for key, value in row.items()} for row in selected)
    say(f"{scanned} bookmark diperiksa · {len(selected)} dipilih · {len(manifest['threads'])} utas", BLUE)
    say(f"Daftar: {dest / 'daftar-bookmark.csv'}")
    return dest


def range_key(settings):
    zone = settings['timezone'].replace('/', '-')
    return f"{settings['start']}_sampai_{settings['end']}_{zone}"


def thread_filter(thread, all_authors=False):
    cid = int(thread['conversation_id'])
    expr = f'conversation_id == {cid}'
    if not all_authors:
        ids = tuple(int(v) for v in thread['author_ids'])
        expr += f" and author['id'] in {ids!r}"
    return expr


RECONCILE_MIN = 50  # fewer missing entries than this are always reconciled quietly
RECONCILE_SHARE = 0.2  # ... and so is any number below this share of the archive


def reconcile_archive(archive_path, media, force=False, backup_dir=None):
    """Drop only this tool's archive entries whose final media file is gone.

    A large share of missing files usually means the collection was moved, not deleted. Then nothing changes
    until the caller passes force=True. Removed entries are listed in backup_dir first so they can be restored.
    """
    if not archive_path.exists():
        return 0
    existing = set()
    for path in media_files(media):
        if not is_partial(path):
            match = re.search(r'_(\d+)_(\d+)\.[^.]+$', path.name)
            if match:
                existing.add(f'twitter_{match[1]}_{match[2]}')
    with closing(sqlite3.connect(archive_path)) as con, con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive'").fetchone():
            return 0
        keys = [row[0] for row in con.execute('SELECT entry FROM archive')]
        managed = [key for key in keys if re.fullmatch(r'twitter_\d+_\d+', key)]
        missing = [key for key in managed if key not in existing]
        if not force and len(missing) >= RECONCILE_MIN and len(missing) >= RECONCILE_SHARE * len(managed):
            raise ValueError(
                f'{len(missing)} dari {len(managed)} entri arsip ({len(missing) / len(managed):.0%}) tidak punya file di media/. '
                'Biasanya folder dipindah, bukan dihapus; tidak ada yang diubah. '
                'Untuk mengunduh ulang semuanya: ./x-bookmarks download --reconcile. '
                'Jika tidak, kembalikan folder media/ ke lokasi semula.')
        if missing and backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f'archive-removed-{time.time_ns()}.txt').write_text('\n'.join(missing) + '\n', encoding='utf-8')
        con.executemany('DELETE FROM archive WHERE entry=?', [(key,) for key in missing])
    return len(missing)


def download(settings, dest, args, ui=None):
    manifest_path = dest / 'selection.json'
    if not manifest_path.exists():
        raise ValueError('Belum ada daftar terpilih. Jalankan: ./x-bookmarks scan')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (manifest['start'], manifest['end_inclusive'], manifest['timezone']) != (
            settings['start'], settings['end'], settings['timezone']):
        raise ValueError('Rentang manifest berbeda. Jalankan select dahulu.')
    threads = manifest['threads']
    if args.limit:
        threads = threads[:args.limit]
    if not threads:
        raise ValueError('Tidak ada utas terpilih; periksa hasil scan dan rentang tanggal.')
    mode = 'all-authors' if args.all_authors else 'bookmarked-authors'
    ensure_layout(MEDIA)
    if any(p.is_file() and not is_partial(p) and media_kind(p) != 'other'
           for p in MEDIA.iterdir()):
        raise ValueError('Ada hasil lama di akar media. Jalankan ./x-bookmarks organize terlebih dahulu.')
    archive_path = STATE / 'downloaded.sqlite3'
    missing = reconcile_archive(archive_path, MEDIA, force=getattr(args, 'reconcile', False), backup_dir=STATE / 'archive-removed')
    if missing:
        say(f'{missing} file hilang dapat diunduh kembali.')
    ui = ui or UI(plain=True)
    failures = []
    interrupted = False
    completed = 0
    with ui.downloading(len(threads), MEDIA, STATE / 'download.log') as session:
        try:
            for thread in threads:
                cid = str(int(thread['conversation_id']))
                options = ['-o', 'conversations=true', '--post-filter', thread_filter(thread, args.all_authors),
                           '-o', 'base-directory=' + json.dumps(str(MEDIA)),
                           '--download-archive', str(archive_path),
                           f'https://x.com/i/web/status/{cid}']
                args_for_thread = gallery_args(settings, options)
                session.thread_start(cid)
                with (STATE / 'download.log').open('a', encoding='utf-8') as log:
                    log.write(f'\n{datetime.now(UTC).isoformat()} utas={cid} mode={mode}\n')
                code = run_gallery(args_for_thread, STATE / 'download.log', ui=ui)
                session.thread_end(code)
                completed += 1
                if code:
                    failures.append({'conversation_id': cid, 'exit_code': code})
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            write_json(dest / f'last-run-{mode}.json', {
                'finished_at': datetime.now(UTC).isoformat(), 'planned_threads': len(threads),
                'attempted_threads': completed + int(interrupted),
                'completed_threads': completed, 'failed_threads': failures, 'mode': mode,
                'interrupted': interrupted,
                'downloaded_photos': session.photos, 'downloaded_videos': session.videos,
                'downloaded_other': session.other, 'skipped_files': session.skipped,
            })
    if failures:
        raise ValueError(f'{len(failures)} utas mengalami error. Jalankan lagi untuk mencoba ulang.')


def last_run(settings):
    """Newest last-run-*.json of the active range (written by download), or None."""
    folder = STATE / 'selections' / range_key(settings)
    for path in sorted(folder.glob('last-run-*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(report, dict):
            return report
    return None


def last_run_row(settings, report):
    if report is None:
        return ('info', 'Run terakhir', 'belum ada untuk rentang ini')
    try:
        when = datetime.fromisoformat(report['finished_at']).astimezone(ZoneInfo(settings['timezone']))
        done, planned = report['completed_threads'], report.get('planned_threads')
        parts = [f'{when:%Y-%m-%d %H:%M}', f'{done}/{planned} utas' if planned else f'{done} utas',
                 f"{len(report['failed_threads'])} gagal", f"{report['downloaded_photos']} foto",
                 f"{report['downloaded_videos']} video", f"{report['skipped_files']} dilewati"]
        if report.get('interrupted'):
            parts.append('dihentikan')
        parts.append(f"mode {report['mode']}")
        return ('warn' if report['failed_threads'] or report.get('interrupted') else 'ok', 'Run terakhir', ' · '.join(parts))
    except (KeyError, TypeError, ValueError):
        return ('warn', 'Run terakhir', 'berkas last-run tidak terbaca; jalankan download lagi')


def status(settings, as_json=False):
    versions = {}
    for name in ('gallery-dl', 'rich'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    packages = [f'{name} {version}' if version else f'{name} belum terpasang' for name, version in versions.items()]
    packages_ready = all(versions.values())
    path = STATE / 'bookmarks.json'
    snapshot = datetime.fromtimestamp(path.stat().st_mtime, UTC).astimezone(
        ZoneInfo(settings['timezone'])).isoformat() if path.exists() else None
    files = list(media_files(MEDIA))
    finished = [p for p in files if not is_partial(p)]
    at_root = sum(p.parent == MEDIA for p in finished)
    photos = sum(media_kind(p) == 'photo' for p in finished)
    videos = sum(media_kind(p) == 'video' for p in finished)
    report = last_run(settings)
    if as_json:
        emit_json({'version': VERSION, 'python': sys.version.split()[0], 'packages': versions,
                   'packages_ready': packages_ready, 'tool': str(BASE), 'state_dir': str(STATE),
                   'photo_dir': str(MEDIA / 'photo'), 'video_dir': str(MEDIA / 'video'), 'browser': settings['browser'],
                   'snapshot': snapshot,
                   'media': {'photos': photos, 'videos': videos, 'partial': len(files) - len(finished), 'at_root': at_root},
                   'last_run': report})
        return
    # State drives the marker in the terminal view: ok, warn (needs attention) or info.
    rows = [('info', 'Versi', f'{VERSION} · Python {sys.version.split()[0]}'),
            ('ok' if packages_ready else 'warn', 'Paket', ' · '.join(packages)),
            ('info', 'Alat', BASE),
            ('info', 'Foto', MEDIA / 'photo'), ('info', 'Video', MEDIA / 'video'), ('info', 'Catatan', STATE),
            ('info', 'Browser', settings['browser']),
            ('ok' if path.exists() else 'warn', 'Snapshot', snapshot or 'belum ada'),
            ('info', 'Media', f'{photos} foto · {videos} video · {len(files) - len(finished)} sementara'),
            last_run_row(settings, report),
            ('warn' if at_root else 'ok', 'Akar media', f'{at_root} file; gunakan organize untuk hasil lama')]
    UI_INSTANCE.checklist('Status alat', rows)


def clean_state(args, ui):
    """Dry run unless --yes: old program backups, old virtualenvs, dropped archive entries and the pip cache."""
    from release_tools import state_cleanup
    STATE.mkdir(exist_ok=True)
    with exclusive_lock(STATE / 'run.lock'):
        rows = state_cleanup(BASE, args.keep, apply=args.yes)
    ui.say('Pembersihan .state' + ('' if args.yes else ' (dry-run)') + f' · simpan {args.keep} terbaru per kategori', BLUE)
    ui.table(('Kategori', 'Dihapus' if args.yes else 'Akan dihapus', 'Dipertahankan', 'Ukuran', 'Catatan'),
             [(row['label'], row['removed'], row['kept'], size(row['bytes']), row['note'] or '-') for row in rows])
    say('Selesai.' if args.yes else 'Dry-run: belum ada yang dihapus. Tambahkan --yes untuk menjalankannya.')


def organize_media():
    result = organize(MEDIA, STATE)
    write_json(STATE / 'last-organize.json', {**result, 'finished_at': datetime.now(UTC).isoformat()})
    say(f"Organize selesai · {result['moved']} dipindahkan · {result['duplicates']} duplikat dicadangkan", BLUE)
    say(f"Foto: {MEDIA / 'photo'}")
    say(f"Video: {MEDIA / 'video'}")
    if result['partial']:
        say(f"{result['partial']} file sementara dilewati.")
    if result['unknown']:
        say(f"Peringatan: {len(result['unknown'])} format belum dikenali tetap di akar media; lihat .state/last-organize.json.")
    if result['backup']:
        say(f"Cadangan duplikat: {result['backup']}")


JSON_SCHEMA = 1  # raise it whenever a --json field is renamed or removed
EPILOG = """Alur biasa (detail di PANDUAN.md):
  1. ./x-bookmarks status                    periksa kondisi alat
  2. ./x-bookmarks check                     cocokkan 5 bookmark sampel dengan ingatanmu
  3. ./x-bookmarks scan                      ambil metadata dan pilih rentang tanggal
  4. ./x-bookmarks list --limit 10           tinjau pilihan
  5. ./x-bookmarks download --limit 3        unduh sedikit dulu, lalu ulangi tanpa --limit

Rentang dan browser berasal dari settings.json; --start, --end, --browser menimpanya.
Hasil (tabel, kartu status, --json) ke stdout; progres dan pesan ke stderr."""
ACTION_HELP = {
    'status': 'Kondisi alat, snapshot, media, dan run terakhir',
    'check': 'Ambil 5 bookmark sampel untuk memastikan akun dan profil benar',
    'scan': 'Ambil metadata semua bookmark lalu pilih rentang tanggal',
    'select': 'Pilih ulang rentang dari snapshot tanpa mengakses X',
    'list': 'Tampilkan bookmark terpilih (CSV lengkap tetap disimpan)',
    'download': 'Unduh media utas terpilih',
    'organize': 'Pindahkan hasil lama ke folder photo/ dan video/',
    'clean': 'Hapus cadangan lama di .state (dry-run kecuali --yes)',
    'update': 'Periksa atau pasang rilis stabil terbaru',
}
CURRENT_ACTION = None


def bounded_int(minimum, requirement):
    def parse(text):
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f'{text!r} bukan bilangan bulat') from None
        if value < minimum:
            raise argparse.ArgumentTypeError(requirement)
        return value
    return parse


def add_common(parser, nested):
    """--start/--end/--browser/--plain. They sit on the main parser (so they may still precede the action) and on
    each action; the action copies use SUPPRESS so they never overwrite a value given earlier on the line."""
    unset = argparse.SUPPRESS if nested else None
    parser.add_argument('--start', default=unset, help='Tanggal awal YYYY-MM-DD, termasuk hari ini')
    parser.add_argument('--end', default=unset, help='Tanggal akhir YYYY-MM-DD, termasuk seluruh hari ini')
    parser.add_argument('--browser', default=unset, help='Override cookies-from-browser, misalnya firefox/.x.com:/path/profil')
    add_plain(parser, nested)


def add_plain(parser, nested):
    parser.add_argument('--plain', action='store_true', default=argparse.SUPPRESS if nested else False,
                        help='Teks sederhana tanpa panel atau warna')


def build_parser():
    parser = argparse.ArgumentParser(prog='x-bookmarks', epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description='Arsip utas X berdasarkan tanggal nge-bookmark, dengan batas tanggal WIB inklusif.')
    parser.add_argument('--version', action='version', version=f'x-bookmarks {VERSION}')
    add_common(parser, nested=False)
    actions = parser.add_subparsers(dest='action', required=True, metavar='aksi', title='aksi')

    def action(name, common=True):
        sub = actions.add_parser(name, help=ACTION_HELP[name], description=ACTION_HELP[name])
        if common:
            add_common(sub, nested=True)
        return sub

    def json_flag(sub):
        sub.add_argument('--json', action='store_true', help='Hasil sebagai JSON di stdout (skema bernomor)')

    for name in ('status', 'check'):
        json_flag(action(name))
    action('scan')
    action('select')
    listing = action('list')
    listing.add_argument('--limit', type=bounded_int(1, 'harus lebih besar dari 0'),
                         help='Jumlah bookmark ditampilkan (default 20; --json tanpa --limit memuat semuanya)')
    json_flag(listing)
    download = action('download')
    download.add_argument('--limit', type=bounded_int(1, 'harus lebih besar dari 0'), help='Jumlah utas yang diunduh')
    download.add_argument('--all-authors', action='store_true', help='Sertakan balasan penulis lain dalam percakapan')
    download.add_argument('--reconcile', action='store_true',
                          help='Lepas entri arsip yang filenya hilang walau jumlahnya besar (biasanya folder dipindah)')
    action('organize')
    clean = action('clean', common=False)
    add_plain(clean, nested=True)
    clean.add_argument('--keep', type=bounded_int(0, 'harus 0 atau lebih'), default=3, help='Cadangan terbaru yang disimpan (default 3)')
    clean.add_argument('--yes', action='store_true', help='Hapus sungguhan; tanpa ini hanya rencana')
    update = action('update', common=False)
    add_plain(update, nested=True)
    update.add_argument('--check', action='store_true', help='Periksa rilis tanpa memasang')
    update.add_argument('--wait', action='store_true', help='Tunggu kunci alat bebas')
    return parser


def emit_json(value):
    """Machine-readable result on stdout, ASCII-escaped so it stays valid whatever the locale."""
    sys.stdout.write(json.dumps({'schema': JSON_SCHEMA, **value}, ensure_ascii=True, indent=2) + '\n')
    sys.stdout.flush()


def next_flags(settings, args):
    """The range (and browser override) of this run, so the suggested next command does the same thing."""
    parts = [f"--start {settings['start']}", f"--end {settings['end']}"]
    if args.browser:
        parts.append(f'--browser {shlex.quote(args.browser)}')
    return ' ' + ' '.join(parts)


def log_crash():
    path = STATE / 'crash.log'
    try:
        STATE.mkdir(exist_ok=True)
        with path.open('a', encoding='utf-8') as log:
            log.write(f'\n{datetime.now(UTC).isoformat()} v{VERSION} argv={sys.argv[1:]}\n{traceback.format_exc()}')
        return path
    except OSError:
        return None


def main():
    global UI_INSTANCE, CURRENT_ACTION
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args()
    CURRENT_ACTION = args.action
    UI_INSTANCE = ui = UI(plain=args.plain)
    if args.action == 'update':
        if any(value is not None for value in (args.start, args.end, args.browser)):
            parser.error('Gunakan update [--check] [--wait] [--plain]')
        from self_update import perform
        try:
            return perform(BASE, args.check, args.wait, lambda value: ui.say(value, BLUE), ui.activity)
        except Exception as exc:
            raise ValueError(str(exc)) from None
    if args.action == 'clean':
        return clean_state(args, ui)
    as_json = getattr(args, 'json', False)
    settings = load_settings()
    for key in ['start', 'end', 'browser']:
        if getattr(args, key):
            settings[key] = getattr(args, key)
    start, end, tz = bounds(settings)
    ui.heading(settings)
    root = STATE
    root.mkdir(exist_ok=True)
    MEDIA.mkdir(exist_ok=True)
    dest = root / 'selections' / range_key(settings)
    with nullcontext() if args.action in READ_ONLY else exclusive_lock(root / 'run.lock'):
        if args.action in {'check', 'scan', 'download'}:
            with ui.activity('Mencari sesi X di browser'):
                settings['browser'] = resolve_browser(settings['browser'])
        if args.action == 'status':
            status(settings, as_json)
        elif args.action == 'check':
            posts = extract(settings, root / 'sample.json', sample=True, ui=ui)
            samples = [{'bookmarked_at': parse_timestamp(p.get('date_bookmarked')).astimezone(tz).isoformat(),
                        'author_name': p.get('author', {}).get('name', '?'),
                        'url': f"https://x.com/i/web/status/{p['tweet_id']}"} for p in posts]
            if as_json:
                emit_json({'samples': samples})
            else:
                ui.table(('Tanggal bookmark', 'Penulis', 'URL'), [
                    (f"{parse_timestamp(p.get('date_bookmarked')).astimezone(tz):%Y-%m-%d %H:%M:%S %Z}",
                     '@' + sample['author_name'], sample['url']) for p, sample in zip(posts, samples)])
            say('Cocokkan sampel dengan bookmark yang kamu ingat. Media belum diunduh.')
            ui.hint('./x-bookmarks scan' + next_flags(settings, args))
        elif args.action == 'scan':
            extract(settings, root / 'bookmarks.json', ui=ui)
            create_selection(settings, root)
            ui.hint('./x-bookmarks list --limit 10' + next_flags(settings, args))
        elif args.action == 'select':
            create_selection(settings, root)
            ui.hint('./x-bookmarks list --limit 10' + next_flags(settings, args))
        elif args.action == 'list':
            path = dest / 'selection.json'
            if not path.exists():
                raise ValueError('Belum ada pilihan untuk rentang ini. Jalankan scan atau select dengan rentang yang sama.')
            manifest = json.loads(path.read_text(encoding='utf-8'))
            items = manifest['selected_bookmarks']
            say(f'{len(items)} bookmark · {len(manifest["threads"])} utas · snapshot {manifest.get("snapshot_at", "tidak tercatat")}', BLUE)
            if as_json:
                emit_json({'range': {'start': settings['start'], 'end': settings['end'], 'timezone': settings['timezone']},
                           'snapshot_at': manifest.get('snapshot_at'), 'total_bookmarks': len(items),
                           'total_threads': len(manifest['threads']), 'bookmarks': items[:args.limit] if args.limit else items})
            else:
                ui.table(('Tanggal bookmark', 'Penulis', 'URL'), [(item['bookmarked_at'], '@' + item['author_name'], item['url']) for item in items[:args.limit or 20]])
            say(f'Daftar lengkap: {dest / "daftar-bookmark.csv"}')
        elif args.action == 'organize':
            organize_media()
        else:
            download(settings, dest, args, ui=ui)


if __name__ == '__main__':
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        say('Dihentikan sebelum pemasangan.' if CURRENT_ACTION == 'update' else 'Dihentikan. Jalankan perintah yang sama untuk melanjutkan; file selesai tercatat dalam arsip.')
        sys.exit(130)
    except (ValueError, OSError, KeyError, sqlite3.Error, json.JSONDecodeError) as exc:
        say(f'! GAGAL · {exc}', RED)
        sys.exit(1)
    except Exception as exc:  # a bug, not a user mistake: keep the traceback for the report and show one line
        crash = log_crash()
        say(f'! GAGAL · Kesalahan tak terduga ({type(exc).__name__}: {exc}).' + (f' Detail teknis: {crash}' if crash else ''), RED)
        sys.exit(1)

"""Media routing and restartable migration; never change archive identities."""
import hashlib
from pathlib import Path
import time

PHOTO = frozenset({'jpg', 'jpeg', 'png', 'webp', 'gif', 'avif'})
VIDEO = frozenset({'mp4', 'webm', 'mov', 'm4v', 'mkv', 'ts'})


def media_kind(path):
    ext = Path(path).suffix.lstrip('.').lower()
    return 'photo' if ext in PHOTO else 'video' if ext in VIDEO else 'other'


def ensure_layout(media):
    if media.is_symlink():
        raise ValueError(f'Folder {media} berupa symlink; pindahkan seluruh alat dengan migrate.sh.')
    media.mkdir(parents=True, exist_ok=True)
    for name in ('photo', 'video'):
        folder = media / name
        if folder.is_symlink():
            raise ValueError(f'Folder {folder} berupa symlink; gunakan folder biasa.')
        folder.mkdir(exist_ok=True)
        if folder.stat().st_dev != media.stat().st_dev:
            raise ValueError(f'Folder {folder} harus berada pada filesystem yang sama dengan media.')


def media_files(media):
    """Only managed locations, excluding symlinks and incomplete downloads."""
    if not media.exists():
        return
    for folder in (media, media / 'photo', media / 'video'):
        if folder.is_dir() and not folder.is_symlink():
            for path in folder.iterdir():
                if path.is_file() and not path.is_symlink():
                    yield path


def is_partial(path):
    return path.name.lower().endswith(('.part', '.tmp'))


def digest(path):
    with path.open('rb') as stream:
        if callable(getattr(hashlib, 'file_digest', None)):
            return hashlib.file_digest(stream, 'sha256').digest()
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
        return value.digest()


def organize(media, state):
    """Preflight every collision before moving anything; reruns are safe."""
    ensure_layout(media)
    moves = []
    unknown = []
    partial = 0
    for source in sorted(media.iterdir()):
        if not source.is_file() or source.is_symlink():
            continue
        if is_partial(source):
            partial += 1
            continue
        kind = media_kind(source)
        if kind == 'other':
            unknown.append(source.name)
            continue
        target = media / kind / source.name
        duplicate = False
        if target.exists() or target.is_symlink():
            if (target.is_symlink() or not target.is_file()
                    or source.stat().st_size != target.stat().st_size
                    or digest(source) != digest(target)):
                raise ValueError(f'Benturan nama dengan isi berbeda: {target}. '
                                 'Tidak ada file dipindahkan; rapikan benturan lalu ulangi organize.')
            duplicate = True
        moves.append((source, target, duplicate))
    # Keep identical originals rather than deleting data.
    backup = None
    moved = duplicates = 0
    for source, target, duplicate in moves:
        if duplicate:
            if backup is None:
                backup = state / 'layout-backups' / str(time.time_ns())
                backup.mkdir(parents=True)
            source.rename(backup / source.name)
            duplicates += 1
        else:
            source.rename(target)
            moved += 1
    return {'moved': moved, 'duplicates': duplicates, 'partial': partial,
            'unknown': unknown, 'backup': str(backup) if backup else None}

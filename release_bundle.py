"""Create an explicit program-only ZIP and its SHA-256 checksum."""

import argparse, hashlib, zipfile
from pathlib import Path
from release_tools import validate_package


def build(source, dest):
    source = Path(source).resolve()
    meta = validate_package(source)
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / ('x-bookmarks-' + meta['version'] + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as out:
        for name in sorted(meta['files']):
            path = source / name
            info = zipfile.ZipInfo('x-bookmarks/' + name, (2026, 1, 1, 0, 0, 0))
            mode = 0o755 if path.name == 'x-bookmarks' or path.suffix == '.sh' else 0o644
            info.create_system = 3
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, path.read_bytes())
    checksum = archive.with_suffix('.zip.sha256')
    checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n', encoding='utf-8')
    return archive, checksum


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('dest')
    args = p.parse_args()
    for path in build(Path(__file__).resolve().parent, args.dest):
        print(path)

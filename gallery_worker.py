"""Isolated gallery-dl adapter. Events use an inherited FD, never stdout."""
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

from media_layout import media_kind


class Events:
    def __init__(self, fd):
        self.stream = os.fdopen(fd, 'w', buffering=1)
        self.lock = threading.Lock()
        self.had_error = False
        self.last_progress = 0

    def send(self, kind, **values):
        with self.lock:
            self.stream.write(json.dumps({'type': kind, **values}, ensure_ascii=True) + '\n')
            self.stream.flush()


def install(events):
    """Patch only this worker; leave gallery config, jobs and archive native."""
    import gallery_dl
    from gallery_dl import job, output, path
    from gallery_dl.extractor.common import Extractor
    if gallery_dl.__version__ != '1.32.15':
        raise ValueError('Adapter membutuhkan gallery-dl 1.32.15; jalankan bash setup.sh.')

    class RoutedPath(path.PathFormat):
        def set_filename(self, kwdict):
            super().set_filename(kwdict)
            self.set_directory(kwdict)

        def set_extension(self, extension, real=True):
            super().set_extension(extension, real)
            self.set_directory(self.kwdict)

    path.PathFormat = RoutedPath

    class EventOutput(output.NullOutput):
        def __init__(self, current_job):
            self.job = current_job

        def start(self, filename):
            metadata = self.job.pathfmt.kwdict
            events.last_progress = 0
            events.send('start', path=filename, author=metadata.get('author', {}).get('name', ''))
            logging.getLogger('x-bookmarks').info('Starting %s', filename)

        def progress(self, total, downloaded, speed):
            now = time.monotonic()
            if now - events.last_progress >= 0.25:
                events.last_progress = now
                events.send('progress', total=total, downloaded=downloaded, speed=speed)

        def success(self, filename):
            events.send('success', path=filename)
            logging.getLogger('x-bookmarks').info('Saved %s', filename)
            if media_kind(filename) == 'other':
                logging.getLogger('x-bookmarks').warning(
                    'Format belum dikenali; file disimpan di akar media: %s', filename)

        def skip(self, filename):
            events.send('skip', path=filename)
            logging.getLogger('x-bookmarks').info('Skipped %s', filename)

    class EventJob(job.DownloadJob):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.out = EventOutput(self)

        def initialize(self, kwdict=None):
            super().initialize(kwdict)
            hooks = dict(self.hooks)
            hooks['error'] = list(hooks.get('error', ())) + [
                lambda pf: events.send('file_error', path=pf.path)]
            self.hooks = hooks

    job.DownloadJob = EventJob
    class EventDataJob(job.DataJob):
        def run(self):
            code = super().run()
            if getattr(self, 'exception', None):
                events.had_error = True
                events.send('notice', level='error')
                return code or 1
            return code
    job.DataJob = EventDataJob

    native_sleep, native_wait = Extractor.sleep, Extractor.wait

    def sleep(self, seconds, reason):
        if seconds < 5:
            return native_sleep(self, seconds, reason)
        events.send('wait', until=time.time() + seconds, reason=reason)
        try:
            return native_sleep(self, seconds, reason)
        finally:
            events.send('wait_end')

    def wait(self, seconds=None, until=None, adjust=1.0, reason='rate limit'):
        if seconds:
            deadline = time.time() + float(seconds) + adjust
        elif until is not None:
            deadline = (until.timestamp() if hasattr(until, 'timestamp') else float(until)) + adjust
        else:
            return native_wait(self, seconds, until, adjust, reason)
        if deadline <= time.time():
            return native_wait(self, seconds, until, adjust, reason)
        events.send('wait', until=deadline, reason=reason)
        try:
            return native_wait(self, seconds, until, adjust, reason)
        finally:
            events.send('wait_end')

    Extractor.sleep, Extractor.wait = sleep, wait

    class EventLog(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                events.had_error = True
            if record.levelno >= logging.WARNING:
                # Full error text remains in the private log, never in protocol.
                events.send('notice', level=record.levelname.lower())
            message = record.getMessage().lower()
            native_http_retry = (record.msg == '%s (%s/%s)' and len(record.args) == 3
                                 and record.args[1] < record.args[2])
            if native_http_retry or 'retry' in message or 'fallback url' in message:
                events.send('retry')

    native_initialize = output.initialize_logging
    def initialize_logging(level):
        result = native_initialize(level)
        logging.getLogger().addHandler(EventLog())
        return result
    output.initialize_logging = initialize_logging
    return gallery_dl


def main():
    os.umask(0o077)
    # FD is a positional argument owned by the parent, not a global env setting.
    events = Events(int(sys.argv.pop(1)))
    code = 1
    try:
        gallery_dl = install(events)
        code = gallery_dl.main() or 0
    except KeyboardInterrupt:
        code = 130
    except Exception:
        logging.exception('Worker gagal')
        events.had_error = True
    finally:
        code = code or (1 if events.had_error else 0)
        events.send('done', code=code)
        events.stream.close()
    return code


if __name__ == '__main__':
    sys.exit(main())

"""One bounded live display. Plain output never prints byte-by-byte progress."""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import time

from rich.console import Console, Group
from rich.live import Live
from rich.measure import Measurement
from rich.progress import Progress, ProgressColumn, TextColumn
from rich.spinner import Spinner
from rich.table import Table, Column
from rich.text import Text

from media_layout import media_kind

PINK = '#FFC2D4'
RED = '#E8384F'
BLUE = '#A8D8F0'
DIM = '#94A3B8'
EMPTY = '#334155'


class AnimationClock:
    """Animation time advances only while a file is actively downloading."""
    def __init__(self):
        self.elapsed = 0.0
        self.resumed_at = None

    def __call__(self):
        return self.elapsed + (time.monotonic() - self.resumed_at
                               if self.resumed_at is not None else 0)

    def active(self, value, reset=False):
        self.elapsed = 0.0 if reset else self()
        self.resumed_at = time.monotonic() if value else None


class SolidBar:
    """A one-cell-high bar measured against the space Rich allocates to it."""
    def __init__(self, completed, total, color, animation_time=0, indicator=False):
        self.completed, self.total, self.color = completed, total, color
        self.animation_time, self.indicator = animation_time, indicator

    def text(self, width):
        width = max(0, width)
        bar = Text(no_wrap=True, overflow='crop')
        if self.total is None:
            count = min(3, width) if self.indicator else 0
            travel = width - count
            step = int(self.animation_time * 8) % (2 * travel) if travel else 0
            start = min(step, 2 * travel - step) if travel else 0
            bar.append('░' * start, EMPTY)
            bar.append('█' * count, self.color)
            bar.append('░' * (width - start - count), EMPTY)
            return bar
        ratio = min(1.0, max(0.0, self.completed / self.total)) if self.total > 0 else 0
        eighths = int(ratio * width * 8)
        full, fraction = divmod(eighths, 8)
        bar.append('█' * full, self.color)
        if fraction:
            # A dark background keeps the fractional cell aligned with the track.
            bar.append('▏▎▍▌▋▊▉'[fraction - 1], f'{self.color} on {EMPTY}')
        bar.append('░' * (width - full - bool(fraction)), EMPTY)
        return bar

    def __rich_measure__(self, console, options):
        return Measurement(1, max(1, options.max_width))

    def __rich_console__(self, console, options):
        yield self.text(options.max_width)


class SolidBarColumn(ProgressColumn):
    def __init__(self, color, clock=None):
        super().__init__(table_column=Column(ratio=1, no_wrap=True))
        self.color, self.clock = color, clock

    def render(self, task):
        return SolidBar(task.completed, task.total, self.color,
                        self.clock() if self.clock else 0,
                        task.fields.get('indicator', False))


def duration(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}j {minutes}m' if hours else f'{minutes}m {seconds}d' if minutes else f'{seconds}d'


def size(number):
    number = max(0, number or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if number < 1024 or unit == 'TB':
            return f'{number:.1f} {unit}' if unit != 'B' else f'{number:.0f} B'
        number /= 1024


class UI:
    def __init__(self, plain=False):
        self.interactive = (not plain and sys.stdout.isatty()
                            and sys.stderr.isatty() and os.environ.get('TERM') != 'dumb')
        self.console = Console(stderr=True, force_terminal=self.interactive,
                               color_system='truecolor' if self.interactive else None,
                               no_color='NO_COLOR' in os.environ, highlight=False)
        self.live = None
        self.session = None
        self.message = ''
        self.wait_until = None
        self.wait_message = ''
        self.last_plain_status = None

    def say(self, value, style=None):
        if not self.interactive:
            sys.stderr.write(str(value) + '\n')
            sys.stderr.flush()
            return
        self.console.print(Text(str(value), style=style), overflow='fold')

    def heading(self, settings):
        self.title = (f"X Bookmarks · {settings['start']}–{settings['end']} · "
                      f"{settings['timezone']} · tanggal bookmark")
        self.say(self.title, BLUE)

    def table(self, headings, rows):
        if not self.interactive:
            self.say(' | '.join(headings))
            for row in rows:
                self.say(' | '.join(map(str, row)))
            return
        table = Table(box=None, padding=(0, 1), show_edge=False)
        for name in headings:
            table.add_column(name, header_style=BLUE, overflow='fold')
        for row in rows:
            table.add_row(*(Text(str(value)) for value in row))
        self.console.print(table)

    def status(self, message):
        self.message = message
        if self.session:
            self.session.message = message
        if not self.interactive and message != self.last_plain_status:
            self.say(message)
            self.last_plain_status = message

    def event(self, event):
        kind = event.get('type')
        if kind == 'wait':
            self.wait_until = event.get('until')
            reason = event.get('reason', 'permintaan')
            self.wait_message = f'Menunggu · {reason}'
            self.status(self.wait_message)
        elif kind == 'wait_end':
            self.wait_until = None
            self.status('Melanjutkan')
        elif kind == 'notice':
            level = event.get('level', '')
            if level in ('error', 'critical'):
                self.status('! GAGAL · detail tersedia di log')
            else:
                self.status('Peringatan · detail tersedia di log')
        if self.session:
            self.session.event(event)

    def status_text(self):
        message = self.session.message if self.session else self.message
        if self.wait_until is not None:
            left = self.wait_until - time.time()
            message = f'{self.wait_message} · sisa {duration(left)}'
        return Text('Status     ' + message, style=RED if 'GAGAL' in message else DIM,
                    overflow='ellipsis', no_wrap=True)

    @contextmanager
    def activity(self, message):
        self.status(message)
        if not self.interactive:
            yield
            return
        ui = self
        class Activity:
            def __rich__(self):
                return Group(Spinner('dots', text=Text(ui.message, style=BLUE), style=PINK),
                             ui.status_text())
        with Live(Activity(), console=self.console, refresh_per_second=4, transient=True,
                  redirect_stdout=False, redirect_stderr=False) as live:
            self.live = live
            try:
                yield
            finally:
                self.live = None

    def downloading(self, total, media, log):
        return DownloadSession(self, total, media, log)


class DownloadSession:
    def __init__(self, ui, total, media, log):
        self.ui, self.total, self.media, self.log = ui, total, media, log
        self.started = time.monotonic()
        self.completed = self.failed = self.photos = self.videos = self.other = self.skipped = 0
        self.cid = self.filename = self.author = ''
        self.bytes = self.byte_total = self.speed = 0
        self.message = 'Mengambil metadata'
        self.seen = set()
        self.animation = AnimationClock()
        self.overall = Progress(TextColumn('Utas'),
                                SolidBarColumn(PINK),
                                TextColumn('{task.percentage:.0f}% · {task.completed:.0f}/{task.total:.0f}'),
                                expand=True)
        self.filebar = Progress(TextColumn('File'),
                                SolidBarColumn(RED, self.animation),
                                TextColumn('{task.fields[label]}'), expand=True)
        self.overall_id = self.overall.add_task('', total=total)
        self.file_id = self.filebar.add_task('', total=None, label='Menyiapkan')

    def __enter__(self):
        self.ui.session = self
        if self.ui.interactive:
            self.ui.live = Live(self, console=self.ui.console, refresh_per_second=4,
                                transient=True, redirect_stdout=False, redirect_stderr=False)
            self.ui.live.start()
        else:
            self.ui.say(f'Download · {self.total} utas')
        return self

    def thread_start(self, cid):
        self.cid = cid
        self.filename = self.author = ''
        self.bytes = self.byte_total = self.speed = 0
        self.ui.wait_until = None
        self.animation.active(False, reset=True)
        self.filebar.reset(self.file_id, total=None, indicator=False)
        self.filebar.update(self.file_id, label='Mengambil metadata')
        self.ui.status(f'Mengambil metadata · utas {cid}')

    def thread_end(self, code):
        self.animation.active(False)
        self.completed += 1
        self.failed += bool(code)
        self.overall.update(self.overall_id, completed=self.completed)
        if not self.ui.interactive:
            self.ui.say(f'Utas {self.completed}/{self.total} · '
                        f'{"GAGAL" if code else "selesai"} · {self.cid}')

    def event(self, event):
        kind = event.get('type')
        if kind == 'start':
            self.filename = event.get('path', '')
            self.author = event.get('author', '')
            self.bytes = self.byte_total = self.speed = 0
            self.animation.active(True, reset=True)
            self.filebar.reset(self.file_id, total=None, indicator=True)
            self.filebar.update(self.file_id, label='Memulai')
            self.message = 'Mengunduh'
        elif kind == 'progress':
            self.animation.active(True)
            self.bytes = event.get('downloaded') or 0
            self.byte_total = event.get('total') or 0
            self.speed = event.get('speed') or 0
            self.filebar.update(self.file_id, completed=self.bytes,
                                total=self.byte_total if self.byte_total > 0 else None,
                                indicator=True)
            # Unknown-total update() leaves a previous total intact; clear explicitly.
            if self.byte_total <= 0:
                self.filebar.tasks[0].total = None
            label = (f'{min(100, self.bytes * 100 / self.byte_total):.0f}% · '
                     f'{size(self.bytes)}/{size(self.byte_total)}'
                     if self.byte_total > 0 else size(self.bytes))
            self.filebar.update(self.file_id, label=label)
            self.message = 'Mengunduh'
        elif kind == 'wait_end':
            self.message = 'Melanjutkan'
        elif kind in ('success', 'skip'):
            self.animation.active(False)
            key = (self.cid, event.get('path'), kind)
            if key not in self.seen:
                self.seen.add(key)
                if kind == 'skip':
                    self.skipped += 1
                else:
                    category = media_kind(event.get('path', ''))
                    if category == 'photo':
                        self.photos += 1
                    elif category == 'video':
                        self.videos += 1
                    else:
                        self.other += 1
            self.message = 'File tersimpan' if kind == 'success' else 'File sudah tersedia · dilewati'
            if kind == 'success':
                self.filebar.update(self.file_id, total=max(1, self.bytes),
                                    completed=max(1, self.bytes), label='Tersimpan')
            else:
                self.filebar.update(self.file_id, indicator=False, label='Dilewati')
        elif kind == 'file_error':
            self.animation.active(False)
            self.message = '! GAGAL · file tidak tersimpan; lihat log'
        elif kind == 'retry':
            self.animation.active(False)
            self.message = 'Mencoba kembali'
        elif kind == 'wait' or (kind == 'notice' and event.get('level') in ('error', 'critical')):
            self.animation.active(False)

    def __rich_console__(self, console, options):
        compact = options.max_width < 60
        task = self.filebar.tasks[0]
        if task.total is not None:
            percent = min(100, max(0, task.percentage))
            label = f'{percent:.0f}%'
            if not compact:
                label += f' · {size(task.completed)}/{size(task.total)}'
        elif task.fields.get('indicator'):
            label = '—%' if compact else size(self.bytes)
        else:
            label = 'Metadata' if not self.filename else 'Dilewati'
        self.filebar.update(self.file_id, label=label)
        name = Path(self.filename).name if self.filename else f'utas {self.cid}'
        category = media_kind(self.filename) if self.filename else ''
        detail = f'           @{self.author} · {category} · {name}' if self.author else f'           {name}'
        rate = ('      ' if compact else '           ') + f'{size(self.speed)}/s'
        if compact:
            rate += f' · {size(self.bytes)}' + (f'/{size(self.byte_total)}' if self.byte_total > 0 else '')
        if self.speed > 0 and self.byte_total > 0:
            rate += f' · sisa {duration((self.byte_total - self.bytes) / self.speed)}'
        yield Group(
            self.overall, self.filebar,
            Text(detail, style=BLUE, overflow='ellipsis', no_wrap=True),
            Text(rate, style=BLUE, overflow='ellipsis', no_wrap=True),
            Text(f'Hasil      Foto {self.photos} · Video {self.videos} · Dilewati {self.skipped}'
                 f' · Utas gagal {self.failed}' + (f' · Lainnya {self.other}' if self.other else ''),
                 overflow='ellipsis', no_wrap=True),
            self.ui.status_text(),
            Text('Durasi     ' + duration(time.monotonic() - self.started), style=DIM))

    def __exit__(self, exc_type, exc, tb):
        self.animation.active(False)
        if self.ui.live:
            self.ui.live.stop()
            self.ui.live = None
        self.ui.session = None
        self.ui.wait_until = None
        if exc_type is KeyboardInterrupt:
            title = 'Dihentikan'
        elif exc_type or self.failed:
            title = '! Selesai dengan masalah'
        else:
            title = 'Selesai'
        self.ui.say(f'{title} · {self.completed}/{self.total} utas diproses · '
                    f'{duration(time.monotonic() - self.started)}', RED if exc_type or self.failed else BLUE)
        self.ui.say(f'Foto {self.photos} · Video {self.videos} · Dilewati {self.skipped}'
                    f' · Utas gagal {self.failed}' + (f' · Lainnya {self.other}' if self.other else ''))
        self.ui.say(f'Foto: {self.media / "photo"}')
        self.ui.say(f'Video: {self.media / "video"}')
        self.ui.say(f'Log: {self.log}', DIM)

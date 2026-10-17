"""One bounded live display. Plain output never prints byte-by-byte progress.

Motion is tied to real activity: the comet bar and the status spinner stop when the
worker waits, retries, fails or stops sending data. Plain, redirected, TERM=dumb and
NO_COLOR runs never receive animation or colour; X_BOOKMARKS_STATIC=1 turns motion off
in a normal terminal and X_BOOKMARKS_NO_TITLE=1 stops the terminal title updates.
X_BOOKMARKS_THEME=light|dark picks the palette (otherwise COLORFGBG decides, dark by default).

Output contract: results (tables, the status card) go to stdout; progress, status lines, hints and errors go to
stderr. Redirect stdout to keep results only, or `> file 2>&1` to keep everything.
"""
from collections import deque
from contextlib import contextmanager
from datetime import date
import os
from pathlib import Path
import sys
import time

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.measure import Measurement
from rich.panel import Panel
from rich.progress import Progress
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from media_layout import media_kind

# Semantic roles: PINK activity, FILE the active file bar, RED failure only, BLUE info, MINT success, AMBER wait or
# warning, DIM secondary text, EMPTY the bar track. In the dark palette FILE equals RED (the original look); the light
# palette keeps failures the only red thing on screen.
PALETTES = {
    'dark': dict(pink='#FFC2D4', red='#E8384F', file='#E8384F', blue='#A8D8F0', dim='#94A3B8', empty='#334155',
                 mint='#8EE3B0', amber='#FFD27A'),
    'light': dict(pink='#B4325E', red='#C4243C', file='#0E7490', blue='#1F6FA3', dim='#586678', empty='#CBD5E1',
                  mint='#1E7F55', amber='#8A5600'),
}


def detect_theme(env):
    """X_BOOKMARKS_THEME wins; otherwise COLORFGBG ('fg;bg' or 'fg;default;bg') with a light background; else dark."""
    chosen = env.get('X_BOOKMARKS_THEME', '').strip().lower()
    if chosen in PALETTES:
        return chosen
    background = env.get('COLORFGBG', '').rsplit(';', 1)[-1]
    return 'light' if background in ('7', '15') else 'dark'


THEME = detect_theme(os.environ)
PINK, RED, FILE, BLUE, DIM, EMPTY, MINT, AMBER = (
    PALETTES[THEME][name] for name in ('pink', 'red', 'file', 'blue', 'dim', 'empty', 'mint', 'amber'))

# One cell wide in every terminal font checked so far; emoji and U+FE0F forms are avoided.
GLYPHS = {
    True: dict(head='◆', run='▸', file='↓', ok='✓', fail='✗', skip='↷', warn='▲', pause='○',
               dot='●', time='◷', retry='↻', media='▣', speed='⇣', rule='─', arrow='→', more='…',
               spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏', spark='▁▂▃▄▅▆▇█'),
    False: dict(head='*', run='>', file='v', ok='+', fail='x', skip='-', warn='!', pause='o',
                dot='*', time='~', retry='@', media='#', speed='v', rule='-', arrow='>', more='...',
                spin='|/-\\', spark='_.-~^'),
}
BARS = {
    True: dict(full='█', empty='░', part='▏▎▍▌▋▊▉', comet='░▒▓█'),
    False: dict(full='#', empty='.', part='', comet='.:+#'),
}
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des')
ZONES = {'Asia/Jakarta': 'WIB', 'Asia/Makassar': 'WITA', 'Asia/Jayapura': 'WIT'}
KINDS = {'photo': 'foto', 'video': 'video', 'other': 'lain'}

COMET_SPEED = 12        # cells per second; matches the 12 fps refresh so each frame moves one cell
SPIN_SPEED = 12.5       # frames per second, the same pace as Rich's own dots spinner
SPARK_WIDTH = 10        # speed samples shown, roughly the last few seconds of data
STALL_AFTER = 4.0       # seconds without data before motion stops and the status says so
FINISH_PAUSE = 0.35     # seconds the completed panel stays up before the summary replaces it
RECEIPT_ALL_UP_TO = 40  # more threads than this: only threads with problems get a receipt line
SHOWN_FAILURES = 5


class AnimationClock:
    """Animation time advances only while a file is actively downloading and data arrives."""
    def __init__(self, stale_after=None):
        self.elapsed = 0.0
        self.resumed_at = None
        self.beat = None
        self.stale_after = stale_after

    def __call__(self):
        if self.resumed_at is None:
            return self.elapsed
        now = time.monotonic()
        if self.stale_after is not None and self.beat is not None:
            now = min(now, self.beat + self.stale_after)
        return self.elapsed + max(0.0, now - self.resumed_at)

    def idle(self):
        """Seconds since the last sign of data, or 0 while the clock is stopped."""
        return time.monotonic() - self.beat if self.resumed_at is not None and self.beat is not None else 0.0

    def active(self, value, reset=False):
        self.elapsed = 0.0 if reset else self()
        now = time.monotonic()
        self.resumed_at = now if value else None
        self.beat = now if value else None


def blend(start, end, amount):
    first = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    second = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02X%02X%02X' % tuple(round(a + (b - a) * amount) for a, b in zip(first, second))


class SolidBar:
    """A one-cell-high bar measured against the space Rich allocates to it."""
    def __init__(self, completed, total, color, animation_time=0, indicator=False,
                 gradient=None, unicode_ok=True, motion=True):
        self.completed, self.total, self.color = completed, total, color
        self.animation_time, self.indicator = animation_time, indicator
        self.gradient, self.unicode_ok, self.motion = gradient, unicode_ok, motion

    def shade(self, index, width):
        if not self.gradient:
            return self.color
        return blend(self.gradient[0], self.gradient[1], index / max(1, width - 1))

    def comet(self, width, glyph):
        """A bright head with a fading tail. The head moves one cell per step and bounces
        off both ends; the tail trails behind it and is clipped by the ends of the track."""
        bar = Text(no_wrap=True, overflow='crop')
        if not (self.indicator and self.motion) or width < 1:
            bar.append(glyph['empty'] * width, EMPTY)
            return bar
        length = min(len(glyph['comet']), width)
        period = 2 * (width - 1)
        step = int(self.animation_time * COMET_SPEED) % period if period else 0
        head = min(step, period - step) if period else 0
        rightward = step <= width - 1
        cells = [(glyph['empty'], EMPTY)] * width
        for age in range(length):                      # age 0 is the head
            place = head - age if rightward else head + age
            if 0 <= place < width:
                cells[place] = (glyph['comet'][-1 - age],
                                blend(EMPTY, self.color, 1 - 0.6 * age / max(1, length - 1)))
        for cell, style in cells:
            bar.append(cell, style)
        return bar

    def text(self, width):
        width = max(0, width)
        glyph = BARS[self.unicode_ok]
        if self.total is None:
            return self.comet(width, glyph)
        bar = Text(no_wrap=True, overflow='crop')
        ratio = min(1.0, max(0.0, self.completed / self.total)) if self.total > 0 else 0
        eighths = int(ratio * width * 8)
        full, fraction = divmod(eighths, 8)
        if not glyph['part']:
            fraction = 0
        if self.gradient:
            for index in range(full):
                bar.append(glyph['full'], self.shade(index, width))
        else:
            bar.append(glyph['full'] * full, self.color)
        if fraction:
            # A dark background keeps the fractional cell aligned with the track.
            bar.append(glyph['part'][fraction - 1], f'{self.shade(full, width)} on {EMPTY}')
        bar.append(glyph['empty'] * (width - full - bool(fraction)), EMPTY)
        return bar

    def __rich_measure__(self, console, options):
        return Measurement(1, max(1, options.max_width))

    def __rich_console__(self, console, options):
        yield self.text(options.max_width)


def duration(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}j {minutes}m' if hours else f'{minutes}m {seconds}d' if minutes else f'{seconds}d'


def elapsed_label(seconds):
    """Short wall time for finished steps: '240 ms', '3.2d', '1m 7d'."""
    if seconds < 1:
        return f'{seconds * 1000:.0f} ms'
    return f'{seconds:.1f}d' if seconds < 10 else duration(seconds)


def size(number):
    number = max(0, number or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if number < 1024 or unit == 'TB':
            return f'{number:.1f} {unit}' if unit != 'B' else f'{number:.0f} B'
        number /= 1024


def short_path(path):
    """Show paths under the home folder as ~/..., which also keeps screenshots tidy."""
    path = Path(path)
    try:
        return '~/' + str(path.relative_to(Path.home()))
    except (ValueError, RuntimeError):
        return str(path)


def range_label(start, end, zone, arrow='→'):
    """'31 Agu → 06 Okt 2026 · WIB'; raw text if the dates cannot be read."""
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return f'{start}–{end} · {zone}'
    def day(value, year):
        return f'{value.day:02d} {MONTHS[value.month - 1]}' + (f' {value.year}' if year else '')
    span = (f'{day(first, False)} {arrow} {day(last, False)} {last.year}' if first.year == last.year
            else f'{day(first, True)} {arrow} {day(last, True)}')
    return f'{span} · {ZONES.get(zone, zone)}'


class UI:
    def __init__(self, plain=False):
        self.interactive = (not plain and sys.stdout.isatty()
                            and sys.stderr.isatty() and os.environ.get('TERM') != 'dumb')
        self.console = self.make_console(stderr=True)       # progress, status, errors
        self.data_console = self.make_console(stderr=False)  # results: tables and the status card
        self.static = os.environ.get('X_BOOKMARKS_STATIC', '') not in ('', '0')
        self.title_enabled = os.environ.get('X_BOOKMARKS_NO_TITLE', '') in ('', '0')
        self.live = None
        self.session = None
        self.message = ''
        self.level = 'busy'
        self.range = ''
        self.wait_until = None
        self.wait_total = None
        self.wait_message = ''
        self.last_plain_status = None

    def make_console(self, stderr):
        return Console(stderr=stderr, force_terminal=self.interactive,
                       color_system='truecolor' if self.interactive else None,
                       no_color='NO_COLOR' in os.environ, highlight=False)

    # ----- terminal capabilities, decided from the console that actually prints -----
    @property
    def unicode(self):
        return not self.console.options.ascii_only and os.environ.get('TERM') not in ('dumb', 'linux')

    @property
    def g(self):
        return GLYPHS[self.unicode]

    @property
    def motion(self):
        return not self.static

    @property
    def gradient(self):
        return self.console.color_system == 'truecolor' and not self.console.no_color

    def refresh_rate(self):
        if self.static:
            return 4
        return 4 if os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_TTY') else 12

    def spinner(self):
        frames = self.g['spin']
        return frames[int(time.monotonic() * SPIN_SPEED) % len(frames)] if self.motion else self.g['run']

    def bar(self, completed, total, color, clock=None, indicator=False, gradient=None):
        return SolidBar(completed, total, color, clock() if clock else 0, indicator,
                        gradient if self.gradient else None, self.unicode, self.motion)

    def rule(self):
        return Rule(style=EMPTY, characters=self.g['rule'])

    def panel(self, body, title, border=EMPTY, subtitle=None):
        return Panel(body, title=title, title_align='left', box=box.ROUNDED if self.unicode else box.ASCII,
                     border_style=border,
                     subtitle=Text(subtitle, style=DIM) if subtitle else None,
                     subtitle_align='right', padding=(0, 1))

    # ----- terminal title: progress while running, restored through the title stack -----
    def title_ok(self):
        return self.interactive and self.title_enabled

    def window_title(self, text):
        if self.title_ok():
            self.console.set_window_title(text)

    def push_title(self):
        if self.title_ok():
            self.console.file.write('\x1b[22;0t')
            self.console.file.flush()

    def pop_title(self, final):
        if self.title_ok():
            self.console.set_window_title(final)   # shown if the terminal ignores the title stack
            self.console.file.write('\x1b[23;0t')
            self.console.file.flush()

    # ----- plain lines and tables -----
    def say(self, value, style=None):
        if not self.interactive:
            sys.stderr.write(str(value) + '\n')
            sys.stderr.flush()
            return
        self.console.print(Text(str(value), style=style), overflow='fold')

    def heading(self, settings):
        self.title = (f"X Bookmarks · {settings['start']}–{settings['end']} · "
                      f"{settings['timezone']} · tanggal bookmark")
        if not self.interactive:
            self.say(self.title, BLUE)
            return
        self.range = range_label(settings['start'], settings['end'], settings['timezone'], self.g['arrow'])
        self.say(f"{self.g['head']} X Bookmarks · {self.range} · tanggal bookmark", BLUE)

    def out(self, value):
        """A line of result data on stdout, so it survives `> file` and pipes."""
        sys.stdout.write(str(value) + '\n')
        sys.stdout.flush()

    def hint(self, command):
        self.say(f"{self.g['run']} Berikutnya: {command}" if self.interactive else f'Berikutnya: {command}', BLUE)

    def table(self, headings, rows):
        if not self.interactive:
            self.out(' | '.join(headings))
            for row in rows:
                self.out(' | '.join(map(str, row)))
            return
        table = Table(box=None, padding=(0, 1), show_edge=False)
        for name in headings:
            table.add_column(name, header_style=BLUE, overflow='fold')
        for row in rows:
            table.add_row(*(Text(str(value)) for value in row))
        self.data_console.print(table)

    def checklist(self, title, rows):
        """rows: (state, label, value) with state 'ok', 'warn' or 'info'. Plain output is the old table."""
        if not self.interactive:
            self.table(('Informasi', 'Nilai'), [(label, value) for _, label, value in rows])
            return
        g = self.g
        marks = {'ok': (g['ok'], MINT), 'warn': (g['warn'], AMBER), 'info': (g['run'], BLUE)}
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column(no_wrap=True, style=DIM)
        grid.add_column(overflow='fold')
        for state, label, value in rows:
            glyph, color = marks[state]
            text = short_path(value) if isinstance(value, Path) else str(value)
            grid.add_row(Text(glyph, style=color), label, Text(text))
        self.data_console.print(self.panel(grid, Text.assemble((f'{g["head"]} ', BLUE), (title, f'bold {BLUE}'))))

    # ----- status shown in the last row of every live display -----
    def status(self, message, level='busy'):
        self.message, self.level = message, level
        if self.session:
            self.session.message, self.session.level = message, level
        if not self.interactive and message != self.last_plain_status:
            self.say(message)
            self.last_plain_status = message

    def event(self, event):
        kind = event.get('type')
        if kind == 'wait':
            self.wait_until = event.get('until')
            self.wait_total = max(1.0, self.wait_until - time.time()) if self.wait_until is not None else None
            reason = event.get('reason', 'permintaan')
            self.wait_message = f'Menunggu · {reason}'
            self.status(self.wait_message, 'wait')
        elif kind == 'wait_end':
            self.wait_until = None
            self.status('Melanjutkan')
        elif kind == 'notice':
            level = event.get('level', '')
            if level in ('error', 'critical'):
                self.status('! GAGAL · detail tersedia di log', 'error')
            else:
                self.status('Peringatan · detail tersedia di log', 'warn')
        if self.session:
            self.session.event(event)

    def status_text(self, compact=False):
        g = self.g
        session = self.session
        message, level = (session.message, session.level) if session else (self.message, self.level)
        if self.wait_until is not None:
            left = max(0, self.wait_until - time.time())
            text = Text.assemble((f'  {g["time"]} ', AMBER), (self.wait_message, AMBER),
                                 no_wrap=True, overflow='ellipsis')
            if not compact and self.wait_total:
                text.append('  ')
                text.append_text(self.bar(left, self.wait_total, AMBER).text(10))
            text.append(f'  lanjut {duration(left)}', style=AMBER)
            return text
        if session and session.stalled():
            return Text.assemble((f'  {g["time"]} ', AMBER),
                                 (f'Menunggu data · {duration(session.animation.idle())}', AMBER),
                                 no_wrap=True, overflow='ellipsis')
        glyph, color, style = {
            'busy': (self.spinner(), PINK, DIM), 'ok': (g['ok'], MINT, DIM),
            'skip': (g['skip'], DIM, DIM), 'warn': (g['warn'], AMBER, AMBER),
            'retry': (g['retry'], AMBER, AMBER), 'error': (g['fail'], RED, RED),
            'wait': (g['time'], AMBER, AMBER),
        }.get(level, (g['dot'], DIM, DIM))
        return Text.assemble((f'  {glyph} ', color), (message, style), no_wrap=True, overflow='ellipsis')

    def receipt(self, ok, message, seconds):
        """A finished step stays in the scrollback as one line."""
        if not self.interactive:
            return
        glyph, color = (self.g['ok'], MINT) if ok else (self.g['fail'], RED)
        self.console.print(Text.assemble((f'{glyph} ', color), message, (f' · {elapsed_label(seconds)}', DIM),
                                         no_wrap=True, overflow='ellipsis'))

    @contextmanager
    def activity(self, message):
        self.status(message)
        if not self.interactive:
            yield
            return
        ui, started, finished = self, time.monotonic(), False
        class Activity:
            def __rich_console__(self, console, options):
                yield Text.assemble((ui.spinner() + ' ', PINK), (ui.message, BLUE),
                                    (f'  {elapsed_label(time.monotonic() - started)}', DIM),
                                    no_wrap=True, overflow='ellipsis')
                if ui.wait_until is not None:
                    yield ui.status_text(options.max_width < 60)
        try:
            with Live(Activity(), console=self.console, refresh_per_second=self.refresh_rate(),
                      transient=True, redirect_stdout=False, redirect_stderr=False) as live:
                self.live = live
                try:
                    yield
                    finished = True
                finally:
                    self.live = None
        finally:
            self.receipt(finished, message, time.monotonic() - started)

    def downloading(self, total, media, log):
        return DownloadSession(self, total, media, log)


class DownloadSession:
    def __init__(self, ui, total, media, log):
        self.ui, self.total, self.media, self.log = ui, total, media, log
        self.started = time.monotonic()
        self.completed = self.failed = self.photos = self.videos = self.other = self.skipped = 0
        self.failures = []
        self.cid = self.filename = self.author = ''
        self.bytes = self.byte_total = self.speed = 0
        self.speeds = deque(maxlen=SPARK_WIDTH)
        self.message, self.level = 'Mengambil metadata', 'busy'
        self.finishing = False
        self.seen = set()
        self.reset_thread()
        self.animation = AnimationClock(stale_after=STALL_AFTER)
        # Progress objects only hold the numbers; the rows are drawn by __rich_console__.
        self.overall = Progress(auto_refresh=False)
        self.filebar = Progress(auto_refresh=False)
        self.overall_id = self.overall.add_task('', total=total)
        self.file_id = self.filebar.add_task('', total=None)

    @property
    def file_task(self):
        return next(task for task in self.filebar.tasks if task.id == self.file_id)

    @property
    def overall_task(self):
        return next(task for task in self.overall.tasks if task.id == self.overall_id)

    def reset_thread(self):
        self.thread_photos = self.thread_videos = self.thread_skipped = self.thread_errors = 0
        self.thread_started = time.monotonic()

    def stalled(self):
        return self.level == 'busy' and self.animation.idle() > STALL_AFTER

    def __enter__(self):
        self.ui.session = self
        if self.ui.interactive:
            self.ui.push_title()
            self.ui.window_title(f'x-bookmarks · 0/{self.total} utas')
            self.ui.live = Live(self, console=self.ui.console, refresh_per_second=self.ui.refresh_rate(),
                                transient=True, redirect_stdout=False, redirect_stderr=False)
            self.ui.live.start()
        else:
            self.ui.say(f'Download · {self.total} utas')
        return self

    def thread_start(self, cid):
        self.cid = cid
        self.filename = self.author = ''
        self.bytes = self.byte_total = self.speed = 0
        self.speeds.clear()
        self.reset_thread()
        self.ui.wait_until = None
        self.animation.active(False, reset=True)
        self.filebar.reset(self.file_id, total=None, indicator=False)
        self.ui.status(f'Mengambil metadata · utas {cid}')

    def thread_end(self, code):
        self.animation.active(False)
        self.completed += 1
        self.failed += bool(code)
        if code:
            self.failures.append((self.cid, code))
        self.overall.update(self.overall_id, completed=self.completed)
        if not self.ui.interactive:
            self.ui.say(f'Utas {self.completed}/{self.total} · '
                        f'{"GAGAL" if code else "selesai"} · {self.cid}')
            return
        self.ui.window_title(f'x-bookmarks · {self.completed}/{self.total} utas')
        problem = bool(code) or self.thread_errors
        if problem or self.total <= RECEIPT_ALL_UP_TO:
            self.print_receipt(code)

    def print_receipt(self, code):
        g = self.ui.g
        parts = [f'{count} {label}' for count, label in ((self.thread_photos, 'foto'),
                                                          (self.thread_videos, 'video'),
                                                          (self.thread_skipped, 'dilewati'),
                                                          (self.thread_errors, 'file gagal')) if count]
        if code:
            glyph, color = g['fail'], RED
            parts.append(f'kode keluar {code}')
        elif self.thread_errors:
            glyph, color = g['warn'], AMBER
        elif self.thread_photos or self.thread_videos:
            glyph, color = g['ok'], MINT
        else:
            glyph, color = g['skip'], DIM
        parts = parts or ['tidak ada file baru']
        parts.append(elapsed_label(time.monotonic() - self.thread_started))
        self.ui.console.print(Text.assemble((f'{glyph} ', color), f'utas {self.cid} ',
                                            ('· ' + ' · '.join(parts), DIM), no_wrap=True, overflow='ellipsis'))

    def event(self, event):
        kind = event.get('type')
        if kind == 'start':
            self.filename = event.get('path', '')
            self.author = event.get('author', '')
            self.bytes = self.byte_total = self.speed = 0
            self.speeds.clear()
            self.animation.active(True, reset=True)
            self.filebar.reset(self.file_id, total=None, indicator=True)
            self.message, self.level = 'Mengunduh', 'busy'
        elif kind == 'progress':
            self.animation.active(True)
            self.bytes = event.get('downloaded') or 0
            self.byte_total = event.get('total') or 0
            self.speed = event.get('speed') or 0
            self.speeds.append(self.speed)
            self.filebar.update(self.file_id, completed=self.bytes,
                                total=self.byte_total if self.byte_total > 0 else None,
                                indicator=True)
            # Unknown-total update() leaves a previous total intact; clear explicitly.
            if self.byte_total <= 0:
                self.file_task.total = None
            self.message, self.level = 'Mengunduh', 'busy'
        elif kind == 'wait_end':
            self.message, self.level = 'Melanjutkan', 'busy'
        elif kind in ('success', 'skip'):
            self.animation.active(False)
            key = (self.cid, event.get('path'), kind)
            if key not in self.seen:
                self.seen.add(key)
                if kind == 'skip':
                    self.skipped += 1
                    self.thread_skipped += 1
                else:
                    category = media_kind(event.get('path', ''))
                    if category == 'photo':
                        self.photos += 1
                        self.thread_photos += 1
                    elif category == 'video':
                        self.videos += 1
                        self.thread_videos += 1
                    else:
                        self.other += 1
            if kind == 'success':
                self.message, self.level = 'File tersimpan', 'ok'
                self.filebar.update(self.file_id, total=max(1, self.bytes), completed=max(1, self.bytes))
            else:
                self.message, self.level = 'File sudah tersedia · dilewati', 'skip'
                self.filebar.update(self.file_id, indicator=False)
        elif kind == 'file_error':
            self.animation.active(False)
            self.thread_errors += 1
            self.message, self.level = '! GAGAL · file tidak tersimpan; lihat log', 'error'
        elif kind == 'retry':
            self.animation.active(False)
            self.message, self.level = 'Mencoba kembali', 'retry'
        elif kind == 'wait' or (kind == 'notice' and event.get('level') in ('error', 'critical')):
            self.animation.active(False)

    # ----- drawing: reads state, never changes it -----
    def sparkline(self, glyph):
        peak = max(self.speeds, default=0)
        if peak <= 0:
            return ' ' * SPARK_WIDTH
        top = len(glyph) - 1
        cells = ''.join(glyph[min(top, int(value / peak * top + 0.5))] for value in self.speeds)
        return cells.rjust(SPARK_WIDTH)

    def bar_row(self, glyph, color, name, bar, tail, tail_width):
        row = Table.grid(padding=(0, 1), expand=True)
        row.add_column(no_wrap=True)
        row.add_column(ratio=1, no_wrap=True)
        row.add_column(width=tail_width, no_wrap=True)
        row.add_row(Text.assemble((f'{glyph} ', color), name), bar,
                    Text(tail, no_wrap=True, overflow='ellipsis'))
        return row

    def counts(self, compact):
        g = self.ui.g
        gap = '  ' if compact else '   '
        text = Text.assemble((f'  {g["ok"]} ', MINT), f'{self.photos} foto{gap}',
                             (f'{g["ok"]} ', MINT), f'{self.videos} video{gap}',
                             (f'{g["skip"]} {self.skipped}{"" if compact else " dilewati"}{gap}', DIM),
                             (f'{g["fail"]} {self.failed}{"" if compact else " utas gagal"}',
                              RED if self.failed else DIM),
                             no_wrap=True, overflow='ellipsis')
        if self.other:
            text.append(f'{gap}{g["warn"]} {self.other}{"" if compact else " lainnya"}', style=AMBER)
        return text

    def __rich_console__(self, console, options):
        ui, g = self.ui, self.ui.g
        compact = options.max_width < 60
        task = self.file_task
        if task.total is not None:
            tail = f'{min(100, max(0, task.percentage)):.0f}%'
            if not compact:
                tail += f'  {size(task.completed)}/{size(task.total)}'
        elif task.fields.get('indicator'):
            tail = '—%' if compact else size(self.bytes)
        else:
            tail = 'Metadata' if not self.filename else 'Dilewati'
        done = self.overall_task
        overall_tail = f'{done.percentage:.0f}%  {done.completed:.0f}/{done.total:.0f}'
        width = max(len(tail), len(overall_tail))
        overall = self.bar_row(g['run'], PINK, 'Utas',
                               ui.bar(done.completed, done.total, PINK, gradient=(PINK, BLUE)),
                               overall_tail, width)
        current = self.bar_row(g['file'], FILE, 'File',
                               ui.bar(task.completed, task.total, FILE, self.animation,
                                      task.fields.get('indicator', False), gradient=(FILE, PINK)),
                               tail, width)
        if self.filename:
            detail = Text.assemble((f'  {g["media"]} ', BLUE), f'{KINDS[media_kind(self.filename)]}  ',
                                   (f'@{self.author}  ' if self.author else '', DIM), Path(self.filename).name,
                                   no_wrap=True, overflow='ellipsis')
        else:
            detail = Text.assemble((f'  {g["run"]} ', BLUE), f'utas {self.cid}', no_wrap=True, overflow='ellipsis')
        eta = (self.byte_total - self.bytes) / self.speed if self.speed > 0 and self.byte_total > 0 else None
        rate = Text.assemble((f'  {g["speed"]} ', BLUE), (f'{size(self.speed)}/s', BLUE),
                             no_wrap=True, overflow='ellipsis')
        if compact:
            rate.append(f' · {size(self.bytes)}' + (f'/{size(self.byte_total)}' if self.byte_total > 0 else ''),
                        style=BLUE)
            if eta is not None:
                rate.append(f' · sisa {duration(eta)}', style=BLUE)
        else:
            rate.append(' ' * max(0, 10 - len(f'{size(self.speed)}/s')))
            rate.append('  ' + self.sparkline(g['spark']), style=PINK)
            if eta is not None:
                rate.append(f'   {g["time"]} sisa {duration(eta)}', style=BLUE)
        title = Text.assemble((f'{g["head"]} ', BLUE), ('X Bookmarks', f'bold {BLUE}'),
                              (f' · {ui.range}' if ui.range and not compact else '', DIM))
        yield ui.panel(Group(overall, current, Text(''), detail, rate, ui.rule(),
                             self.counts(compact), ui.status_text(compact)),
                       title, MINT if self.finishing else EMPTY,
                       f'{g["time"]} {duration(time.monotonic() - self.started)}')

    # ----- final summary -----
    def summary(self, kind, compact):
        ui, g = self.ui, self.ui.g
        color, glyph, name = {'done': (MINT, g['ok'], 'Selesai'),
                              'warn': (AMBER, g['warn'], 'Selesai dengan masalah'),
                              'stopped': (AMBER, g['pause'], 'Dihentikan')}[kind]
        def line(mark, mark_color, label, text, dim=False):
            return Text.assemble((f'  {mark} ', mark_color), (label, DIM), (text, DIM if dim else ''),
                                 no_wrap=True, overflow='ellipsis')
        rows = [Text.assemble((f'  {g["head"]} ', BLUE), f'{self.completed}/{self.total} utas diproses'),
                self.counts(compact), ui.rule()]
        if self.failures:
            for cid, code in self.failures[:SHOWN_FAILURES]:
                rows.append(Text.assemble((f'  {g["fail"]} ', RED), f'utas {cid} ', (f'· kode keluar {code}', DIM),
                                          no_wrap=True, overflow='ellipsis'))
            if len(self.failures) > SHOWN_FAILURES:
                rows.append(Text(f'  {g["more"]} dan {len(self.failures) - SHOWN_FAILURES} lainnya', style=DIM))
            rows.append(ui.rule())
        else:
            rows.append(line(g['run'], BLUE, 'foto   ', short_path(self.media / 'photo')))
            rows.append(line(g['run'], BLUE, 'video  ', short_path(self.media / 'video')))
        rows.append(Text.assemble((f'  {g["run"]} ', BLUE), (f'log    {short_path(self.log)}', DIM),
                                  no_wrap=True, overflow='ellipsis'))
        return ui.panel(Group(*rows), Text.assemble((f'{glyph} ', color), (name, f'bold {color}')), color,
                        f'{g["time"]} {duration(time.monotonic() - self.started)}')

    def __exit__(self, exc_type, exc, tb):
        self.animation.active(False)
        interrupted = exc_type is KeyboardInterrupt
        problem = bool(exc_type or self.failed)
        live, self.ui.live = self.ui.live, None
        if live:
            if not problem and self.ui.motion:
                # A short beat on the finished panel before the summary replaces it.
                self.finishing = True
                self.message, self.level = 'Selesai', 'ok'
                live.refresh()
                time.sleep(FINISH_PAUSE)
            live.stop()
            self.ui.pop_title(f'x-bookmarks · {self.completed}/{self.total} utas · '
                              f'{"dihentikan" if interrupted else "ada masalah" if problem else "selesai"}')
        self.ui.session = None
        self.ui.wait_until = None
        elapsed = duration(time.monotonic() - self.started)
        if not self.ui.interactive:
            title = 'Dihentikan' if interrupted else '! Selesai dengan masalah' if problem else 'Selesai'
            self.ui.say(f'{title} · {self.completed}/{self.total} utas diproses · {elapsed}',
                        RED if problem else BLUE)
            self.ui.say(f'Foto {self.photos} · Video {self.videos} · Dilewati {self.skipped}'
                        f' · Utas gagal {self.failed}' + (f' · Lainnya {self.other}' if self.other else ''))
            self.ui.say(f'Foto: {self.media / "photo"}')
            self.ui.say(f'Video: {self.media / "video"}')
            self.ui.say(f'Log: {self.log}', DIM)
            return
        kind = 'stopped' if interrupted else 'warn' if problem else 'done'
        self.ui.console.print(self.summary(kind, self.ui.console.width < 60))

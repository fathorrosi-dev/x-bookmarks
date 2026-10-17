"""Local regression suite. Run: .venv/bin/python -m unittest discover -s tests -v"""
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import re
import pty
import select
import signal
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import bookmarks as tool
import cli_ui
from media_layout import PHOTO, VIDEO, is_partial, media_files, media_kind, organize

SETTINGS = json.loads((BASE / 'settings.example.json').read_text(encoding='utf-8'))

class Capture:
    def __init__(self): self.events=[]
    def event(self, event): self.events.append(event)

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'
        self.state = self.root / '.state'
        self.media.mkdir(); self.state.mkdir()
        worker = self.root / 'gallery_worker.py'
        worker.write_text('import runpy,sys\nsys.argv.insert(1,' + repr(str(BASE)) + ')\n'
                          'runpy.run_path(' + repr(str(BASE / 'tests/worker_fixture.py')) + ',run_name="__main__")\n')

    def command(self, mode='mix', *extra):
        return ['--config-ignore', '-c', str(BASE/'gallery-dl.json'),
                '-o', 'base-directory=' + json.dumps(str(self.media)),
                '-o', 'sleep=0', '-o', 'sleep-request=0', '-o', 'cookies=null',
                '--download-archive', str(self.state/'downloaded.sqlite3'),
                '--no-postprocessors', '--no-colors', *extra, 'fixture:' + mode]

    def run_worker(self, mode='mix', *extra, stdout=None):
        capture = Capture()
        with patch.object(tool, 'BASE', self.root):
            code = tool.run_gallery(self.command(mode, *extra), self.state/'test.log', stdout, capture)
        return code, capture.events

    def test_routing_native_archive_skip_recovery(self):
        code, events = self.run_worker()
        self.assertEqual(code,0, (self.state/'test.log').read_text())
        files = list(media_files(self.media))
        self.assertEqual(len(files),4)
        for path in files: self.assertEqual(path.parent.name,media_kind(path))
        saved = {p.name:p.stat().st_mtime_ns for p in files}
        code, events = self.run_worker()
        self.assertEqual(code,0)
        self.assertEqual(sum(e['type']=='skip' for e in events),4)
        self.assertEqual(saved,{p.name:p.stat().st_mtime_ns for p in media_files(self.media)})
        missing=files[0]; missing.unlink()
        self.assertEqual(tool.reconcile_archive(self.state/'downloaded.sqlite3',self.media),1)
        code, events = self.run_worker()
        self.assertEqual(code,0)
        self.assertTrue(missing.exists())
        self.assertEqual(sum(e['type']=='success' for e in events),1)

    def test_every_extension_and_organize_archive(self):
        for index,ext in enumerate(sorted(PHOTO|VIDEO),1):
            (self.media/f'2020_a_1000_{1000+index}_1.{ext.upper()}').write_bytes(ext.encode())
        (self.media/'x.jpg.part').write_bytes(b'partial')
        (self.media/'x.unknown').write_bytes(b'unknown')
        db=self.state/'downloaded.sqlite3'
        with closing(sqlite3.connect(db)) as con,con:
            con.execute('CREATE TABLE archive (entry TEXT PRIMARY KEY)')
            con.executemany('INSERT INTO archive VALUES (?)',[(f'twitter_{1000+i}_1',) for i in range(1,len(PHOTO|VIDEO)+1)])
        before=db.read_bytes()
        result=organize(self.media,self.state)
        self.assertEqual(result['moved'],len(PHOTO|VIDEO))
        self.assertEqual(result['partial'],1)
        self.assertEqual(result['unknown'],['x.unknown'])
        self.assertEqual(db.read_bytes(),before)
        self.assertEqual(tool.reconcile_archive(db,self.media),0)
        self.assertEqual(organize(self.media,self.state)['moved'],0)
        for path in media_files(self.media):
            if not is_partial(path) and media_kind(path)!='other':
                self.assertEqual(path.parent.name,media_kind(path))

    def test_collision_preflight_and_identical_backup(self):
        (self.media/'photo').mkdir()
        (self.media/'a.jpg').write_bytes(b'a')
        (self.media/'z.jpg').write_bytes(b'z')
        (self.media/'photo/z.jpg').write_bytes(b'different')
        with self.assertRaisesRegex(ValueError,'Benturan'):
            organize(self.media,self.state)
        self.assertTrue((self.media/'a.jpg').exists())
        (self.media/'photo/z.jpg').write_bytes(b'z')
        result=organize(self.media,self.state)
        self.assertEqual((result['moved'],result['duplicates']),(1,1))
        self.assertEqual((Path(result['backup'])/'z.jpg').read_bytes(),b'z')
        self.assertFalse((self.media/'z.jpg').exists())

    def test_duplicate_hash_fallback_matches_sha256_without_file_digest(self):
        import hashlib
        from media_layout import digest
        path=self.media/'large.mp4';value=b'fixture bytes'*(1048576//6)
        path.write_bytes(value)
        with patch.object(hashlib,'file_digest',None,create=True):
            self.assertEqual(digest(path),hashlib.sha256(value).digest())

    def test_unknown_preserved_and_warned(self):
        code,events=self.run_worker('unknown')
        self.assertEqual(code,0)
        files=list(media_files(self.media))
        self.assertEqual(files[0].parent,self.media)
        self.assertTrue(any(e['type']=='notice' for e in events))
        self.assertEqual(tool.reconcile_archive(self.state/'downloaded.sqlite3',self.media),0)

    def test_crash_zero_exit_never_success(self):
        code,events=self.run_worker('crash')
        self.assertNotEqual(code,0)
        self.assertIn('laporan lengkap',(self.state/'test.log').read_text())

    def test_snapshot_error_and_wait(self):
        snapshot=self.state/'bookmarks.json'
        snapshot.write_text('PREVIOUS')
        temp=self.state/'bookmarks.json.partial'
        with temp.open('w') as stream:
            code,events=self.run_worker('scanbad','--dump-json','-o','output.jsonl=false',stdout=stream)
        self.assertNotEqual(code,0)
        with self.assertRaises(ValueError): list(tool.directory_posts(temp))
        self.assertEqual(snapshot.read_text(),'PREVIOUS')
        with temp.open('w') as stream:
            code,events=self.run_worker('wait','--dump-json','-o','output.jsonl=false',stdout=stream)
        self.assertEqual(code,0)
        self.assertEqual(len(list(tool.directory_posts(temp))),4)
        self.assertTrue(any(e['type']=='wait' for e in events))
        self.assertTrue(any(e['type']=='wait_end' for e in events))
        self.assertFalse(any(e['type'] in ('start','success') for e in events))

    def test_native_http_progress_retry_unknown_size_and_failure(self):
        class Handler(BaseHTTPRequestHandler):
            flaky=0
            def log_message(self,*args): pass
            def do_GET(self):
                if self.path=='/broken':
                    self.send_error(404);return
                if self.path=='/flaky':
                    type(self).flaky+=1
                    if type(self).flaky==1:
                        self.send_error(500);return
                payload=b'x'*8192
                self.send_response(200)
                self.send_header('Content-Type','image/jpeg' if self.path=='/photo' else 'video/mp4')
                if self.path!='/unknown':self.send_header('Content-Length',str(len(payload)))
                else:self.send_header('Connection','close')
                self.end_headers()
                for i in range(0,len(payload),256):
                    self.wfile.write(payload[i:i+256]);self.wfile.flush();time.sleep(.012)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            code,events=self.run_worker('http|http://127.0.0.1:'+str(server.server_port),
                                       '-o','chunk-size=256','-o','retries=1','-o','sleep-retries=0')
        finally:
            server.shutdown();server.server_close();thread.join()
        self.assertNotEqual(code,0)
        self.assertEqual(sum(e['type']=='success' for e in events),4,(self.state/'test.log').read_text())
        self.assertTrue(any(e['type']=='file_error' for e in events))
        progress=[e for e in events if e['type']=='progress']
        self.assertTrue(any(e['total'] for e in progress))
        self.assertTrue(any(e['total'] is None for e in progress))
        self.assertTrue(any(e['type']=='retry' for e in events))
        unknown=next(p for p in media_files(self.media) if '_1003_' in p.name)
        self.assertEqual(unknown.parent.name,'video')
        self.assertEqual(unknown.suffix,'.mp4')
        self.assertLess(len(progress),30)

    def test_ui_counters_reset_unknown_and_plain(self):
        buf=io.StringIO()
        with redirect_stderr(buf):
            ui=cli_ui.UI(plain=True)
            with ui.downloading(2,self.media,self.state/'log') as session:
                session.thread_start('1000')
                ui.event({'type':'start','path':'a.jpg','author':'test'})
                for i in range(1000): ui.event({'type':'progress','downloaded':i,'total':1000,'speed':100})
                ui.event({'type':'success','path':'a.jpg'})
                ui.event({'type':'success','path':'a.jpg'})
                session.thread_end(0)
                session.thread_start('2000')
                self.assertEqual(session.bytes,0)
                ui.event({'type':'start','path':'b.mp4'})
                ui.event({'type':'progress','downloaded':500,'total':1000,'speed':100})
                ui.event({'type':'progress','downloaded':800,'total':None,'speed':100})
                self.assertIsNone(session.filebar.tasks[0].total)
                ui.event({'type':'skip','path':'b.mp4'})
                ui.event({'type':'file_error','path':'c.mp4'})
                session.thread_end(1)
                self.assertEqual((session.photos,session.videos,session.skipped,session.failed),(1,0,1,1))
                ui.console.width=35
                ui.console.print(session)
        output=buf.getvalue()
        self.assertNotIn('\x1b',output)
        self.assertLess(len(output.splitlines()),30)

    def test_date_boundaries_and_author_filter(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return datetime(2026,10,7,tzinfo=timezone.utc)
        rows=[{'tweet_id':i,'conversation_id':1000,'author':{'id':42},'date_bookmarked':when}
              for i,when in enumerate(['2026-08-30 16:59:59','2026-08-30 17:00:00',
                                       '2026-10-06 16:59:59','2026-10-06 17:00:00'],1)]
        with patch.object(tool,'datetime',Clock): items,_=tool.select_posts(rows,SETTINGS)
        self.assertEqual([p['tweet_id'] for p in items],['2','3'])
        self.assertEqual(tool.thread_filter(tool.group_threads(items)[0]),
                         "conversation_id == 1000 and author['id'] in (42,)")
        self.assertEqual(tool.thread_filter(tool.group_threads(items)[0],True),'conversation_id == 1000')

    def test_solid_bar_fill_and_clamping(self):
        cases = [(-1, '░'*20), (0, '░'*20), (.625, '▏'+'░'*19),
                 (50, '█'*10+'░'*10), (99, '█'*19+'▊'),
                 (100, '█'*20), (101, '█'*20)]
        for value, expected in cases:
            with self.subTest(value=value):
                bar = cli_ui.SolidBar(value, 100, cli_ui.PINK).text(20)
                self.assertEqual(bar.plain, expected)
                self.assertEqual(bar.cell_len, 20)
        self.assertEqual(cli_ui.SolidBar(0, 0, cli_ui.PINK).text(20).plain, '░'*20)
        # Styles are explicit, including the fractional cell and empty track.
        bar = cli_ui.SolidBar(50.625, 100, cli_ui.PINK).text(20)
        self.assertEqual([span.style for span in bar.spans],
                         [cli_ui.PINK, f'{cli_ui.PINK} on {cli_ui.EMPTY}', cli_ui.EMPTY])

    def test_unknown_bar_comet_and_pause(self):
        speed = cli_ui.COMET_SPEED
        for width in (1, 2, 3, 4, 5, 20):
            heads = []
            for step in range(2*(width-1)+1):
                bar = cli_ui.SolidBar(0, None, cli_ui.RED, (step+.5)/speed, True).text(width)
                self.assertEqual(bar.cell_len, width)
                self.assertEqual(bar.plain.count('█'), 1)
                heads.append(bar.plain.index('█'))
            self.assertEqual((heads[0], heads[width-1], heads[-1]), (0, width-1, 0))
            # The head never jumps: one cell per step, also when it bounces off an end.
            self.assertTrue(all(abs(a-b) <= 1 for a, b in zip(heads, heads[1:])), heads)
        # The tail trails behind the head, flipping sides when the direction changes.
        right = cli_ui.SolidBar(0, None, cli_ui.RED, 5.5/speed, True).text(20).plain
        left = cli_ui.SolidBar(0, None, cli_ui.RED, 25.5/speed, True).text(20).plain
        self.assertEqual(right[2:6], '░▒▓█')
        self.assertEqual(left[13:17], '█▓▒░')
        # No head without data or when motion is off; ASCII bars have their own symbols.
        self.assertEqual(cli_ui.SolidBar(0, None, cli_ui.RED, 3, False).text(20).plain, '░'*20)
        self.assertEqual(cli_ui.SolidBar(0, None, cli_ui.RED, 3, True, motion=False).text(20).plain, '░'*20)
        self.assertEqual(cli_ui.SolidBar(50, 100, cli_ui.RED, unicode_ok=False).text(10).plain, '#'*5+'.'*5)
        self.assertTrue(cli_ui.SolidBar(0, None, cli_ui.RED, 3.2, True, unicode_ok=False).text(10).plain.isascii())
        clock_time = [0.0]
        with patch.object(cli_ui.time, 'monotonic', side_effect=lambda:clock_time[0]):
            ui = cli_ui.UI(plain=True)
            session = ui.downloading(2, self.media, self.state/'log')
            ui.session = session
            ui.event({'type':'start','path':'a.mp4'})
            clock_time[0] = .5
            self.assertEqual(session.animation(), .5)
            for event in ({'type':'wait','reason':'test'}, {'type':'retry'},
                          {'type':'file_error'}, {'type':'notice','level':'error'}):
                ui.event({'type':'progress','downloaded':100,'total':None})
                clock_time[0] += .25
                ui.event(event)
                frozen = session.animation()
                clock_time[0] += 10
                self.assertEqual(session.animation(), frozen)
            ui.event({'type':'progress','downloaded':100,'total':None})
            ui.event({'type':'success','path':'a.mp4'})
            self.assertIsNone(session.animation.resumed_at)
            self.assertEqual(session.filebar.tasks[0].percentage, 100)
            ui.event({'type':'start','path':'b.mp4'})
            self.assertEqual(session.animation(), 0)
            self.assertEqual(session.filebar.tasks[0].completed, 0)
            ui.event({'type':'skip','path':'b.mp4'})
            self.assertIsNone(session.animation.resumed_at)
            self.assertFalse(session.filebar.tasks[0].fields['indicator'])
            with redirect_stderr(io.StringIO()): session.thread_start('next')
            self.assertEqual(session.animation(), 0)
            self.assertIsNone(session.animation.resumed_at)

    def test_responsive_solid_panel_widths(self):
        from rich.cells import cell_len
        from rich.console import Console
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}):
            ui = cli_ui.UI(plain=True)
            session = ui.downloading(120, self.media, self.state/'log')
            ui.session = session
            ui.event({'type':'start','path':'long_name_'*30+'.mp4','author':'long_author_'*20})
            session.overall.update(session.overall_id, completed=72)
            for total in (24*1024**2, None):
                ui.event({'type':'progress','total':total,'downloaded':18*1024**2,'speed':1024**2})
                for width in (40, 60, 100, 160):
                    with self.subTest(width=width, total=total):
                        out = io.StringIO()
                        Console(file=out,width=width,no_color=True).print(session)
                        lines = out.getvalue().splitlines()
                        # Frame, two bars, blank, file, speed, rule, counts, status, frame.
                        self.assertEqual(len(lines), 10)
                        self.assertEqual({cell_len(line) for line in lines}, {width})
                        self.assertIn('60%  72/120', lines[1])
                        self.assertIn('░', lines[1])
                        self.assertIn('█', lines[2])
                        bars = [sum(ch in '█░▏▎▍▌▋▊▉▒▓' for ch in line) for line in lines[1:3]]
                        self.assertEqual(bars[0], bars[1], 'Utas and File bars have the same length')
                        if total:
                            self.assertIn('75%', lines[2])
                            self.assertIn('18.0 MB/24.0 MB', lines[5] if width<60 else lines[2])
                            self.assertNotIn('18.0 MB/24.0 MB', lines[2] if width<60 else lines[5])
                        elif width<60:
                            self.assertIn('—%', lines[2])
                        self.assertNotIn('\x1b', out.getvalue())

    def terminal_demo(self, no_color=False, interrupt=False, command=None, expected_exit=None, env_extra=None):
        master, slave = pty.openpty()
        env = {**os.environ, 'TERM': 'xterm-256color'}
        env.pop('NO_COLOR', None)
        env.pop('COLORFGBG', None)
        env['X_BOOKMARKS_THEME'] = 'dark'        # the host terminal's theme must not change what these tests see
        if no_color:
            env['NO_COLOR'] = '1'
        env.update(env_extra or {})
        child = subprocess.Popen(command or [sys.executable, str(BASE/'tests/demo.py')],
                                 stdout=slave, stderr=slave, env=env)
        os.close(slave)
        result = b''
        interrupted = False
        try:
            while True:
                ready, _, _ = select.select([master], [], [], 5)
                if not ready:
                    child.kill()
                    self.fail('Terminal demo timed out')
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                result += chunk
                if interrupt and not interrupted and '█'.encode() in result:
                    child.send_signal(signal.SIGINT)
                    interrupted = True
            self.assertEqual(child.wait(timeout=5), expected_exit if expected_exit is not None
                             else 130 if interrupt else 0)
        finally:
            os.close(master)
            if child.poll() is None:
                child.kill()
                child.wait()
        return result.decode('utf-8', 'replace')

    def test_tty_colors_cursor_and_in_place_updates(self):
        output = self.terminal_demo()
        for rgb in ('255;194;212', '232;56;79', '168;216;240'):
            self.assertIn('\x1b[38;2;' + rgb + 'm', output)
        for old in ('244;114;182', '248;113;113', '56;189;248'):
            self.assertNotIn(old, output)
        self.assertIn('\x1b[1A', output)
        self.assertIn('\x1b[?25h', output)
        self.assertIn('1 foto', output)
        self.assertIn('1 video', output)
        self.assertIn('\x1b[38;2;142;227;176m', output)       # mint: finished panel and summary
        self.assertIn('\x1b]0;x-bookmarks', output)           # terminal title carries progress
        self.assertIn('\x1b[22;0t', output)                   # title pushed ...
        self.assertIn('\x1b[23;0t', output)                   # ... and restored
        self.assertTrue(any(frame in output for frame in cli_ui.GLYPHS[True]['spin']))
        self.assertTrue('▒' in output or '▓' in output)       # comet tail while the size is unknown
        self.assertIn('█', output)
        self.assertIn('░', output)
        self.assertIn('\x1b[38;2;51;65;85m', output)

    def test_tty_spinner_table_and_main_error_palette(self):
        # Run the real main error handler in an isolated package with no selection.
        for name in ('bookmarks.py', 'cli_ui.py', 'media_layout.py'):
            (self.root/name).write_bytes((BASE/name).read_bytes())
        (self.root/'settings.json').write_bytes((BASE/'settings.example.json').read_bytes())
        script = ('import sys,time,runpy\n'
                  f'sys.path.insert(0,{str(self.root)!r})\n'
                  'from cli_ui import UI\n'
                  'ui=UI()\n'
                  'with ui.activity("Memeriksa simulasi"):\n    time.sleep(.3)\n'
                  'ui.table(("Informasi", "Nilai"), [("Simulasi", "Lokal")])\n'
                  f'sys.argv=[{str(self.root/"bookmarks.py")!r},"list"]\n'
                  f'runpy.run_path({str(self.root/"bookmarks.py")!r},run_name="__main__")\n')
        output = self.terminal_demo(command=[sys.executable,'-c',script], expected_exit=1)
        self.assertIn('\x1b[38;2;255;194;212m', output)
        self.assertIn('\x1b[1;38;2;168;216;240mInformasi', output)
        self.assertIn('\x1b[38;2;232;56;79m! GAGAL', output)
        self.assertIn('Belum ada pilihan', output)
        for old in ('244;114;182', '248;113;113', '56;189;248'):
            self.assertNotIn(old, output)

    def test_no_color_keeps_live_updates_without_color(self):
        output = self.terminal_demo(no_color=True)
        self.assertNotIn('\x1b[38;', output)
        self.assertIn('\x1b[1A', output)
        self.assertIn('\x1b[?25h', output)
        self.assertIn('█', output)
        self.assertIn('░', output)

    def test_interrupt_stops_worker_and_live_cleanup(self):
        # Spawn a real isolated child, then inject Ctrl+C into its event callback.
        class Interrupt:
            def event(self,event): raise KeyboardInterrupt
        with patch.object(tool,'BASE',self.root),patch.object(tool,'stop_worker',wraps=tool.stop_worker) as stop:
            with self.assertRaises(KeyboardInterrupt):
                tool.run_gallery(self.command('wait'),self.state/'interrupt.log',ui=Interrupt())
            self.assertEqual(stop.call_count,1)
            self.assertIsNotNone(stop.call_args.args[0].poll())
        buf=io.StringIO()
        with redirect_stderr(buf):
            ui=cli_ui.UI(plain=True)
            with self.assertRaises(KeyboardInterrupt):
                with ui.downloading(3,self.media,self.state/'log'): raise KeyboardInterrupt
            self.assertIsNone(ui.session)
            self.assertIsNone(ui.live)
        self.assertIn('Dihentikan',buf.getvalue())

    def test_tty_interrupt_restores_cursor_and_summary(self):
        output = self.terminal_demo(interrupt=True)
        self.assertIn('\x1b[?25l', output)
        self.assertIn('\x1b[?25h', output)
        self.assertIn('Dihentikan', output)
        self.assertNotIn('Traceback', output)

    def test_redirected_and_plain_preview_do_not_draw_bars(self):
        for extra in ([], ['--plain']):
            result = subprocess.run([sys.executable, str(BASE/'tests/demo.py'), *extra],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('\x1b', result.stderr)
            self.assertNotIn('█', result.stderr)
            self.assertNotIn('░', result.stderr)
            self.assertLess(len(result.stderr.splitlines()), 20)
            self.assertIn('Foto 1 · Video 1', result.stderr)

    def test_static_env_removes_motion_and_title_env_removes_title(self):
        output = self.terminal_demo(env_extra={'X_BOOKMARKS_STATIC': '1', 'X_BOOKMARKS_NO_TITLE': '1'})
        for frame in cli_ui.GLYPHS[True]['spin']:
            self.assertNotIn(frame, output)
        self.assertNotIn('▒', output)
        self.assertNotIn('▓', output)
        self.assertNotIn('\x1b]0;', output)
        self.assertNotIn('\x1b[22;0t', output)
        self.assertIn('\x1b[1A', output)
        self.assertIn('Selesai', output)

    def interactive_ui(self, width=80):
        """A UI that takes the terminal code path but writes into a string."""
        from rich.console import Console
        # TERM stays pinned for the whole test: UI.unicode reads it lazily, so a linux/dumb host TERM would flip glyphs.
        patcher = patch.dict(os.environ, {'TERM': 'xterm-256color'})
        patcher.start()
        self.addCleanup(patcher.stop)
        ui = cli_ui.UI(plain=True)
        ui.interactive = True
        ui.console = Console(file=io.StringIO(), width=width, force_terminal=True, color_system=None, highlight=False)
        ui.data_console = ui.console              # one buffer for both streams keeps these layout tests readable
        return ui, ui.console.file

    def test_symbols_are_one_cell_and_text_fallback_stays_aligned(self):
        from rich.cells import cell_len
        from rich.console import Console
        for table in (*cli_ui.GLYPHS[True].values(), *cli_ui.BARS[True].values()):
            for char in table:
                self.assertEqual(cell_len(char), 1, char)
        for table in (*cli_ui.GLYPHS[False].values(), *cli_ui.BARS[False].values()):
            self.assertTrue(table.isascii(), table)

        def render(console):
            ui = cli_ui.UI(plain=True)
            ui.console = console
            ui.range = cli_ui.range_label('2026-08-31', '2026-10-06', 'Asia/Jakarta', ui.g['arrow'])
            session = ui.downloading(120, self.media, self.state/'log')
            ui.session = session
            session.failures = [('1000', 1)]
            session.photos, session.failed = 3, 1
            ui.event({'type': 'start', 'path': 'a.mp4', 'author': 'x'})
            ui.event({'type': 'progress', 'total': None, 'downloaded': 10, 'speed': 10})
            console.print(session)
            console.print(session.summary('warn', False))
        # TERM=linux: the frame, symbols, bars and rule are ASCII. Only the text separator stays.
        out = io.StringIO()
        with patch.dict(os.environ, {'TERM': 'linux'}):
            render(Console(file=out, width=60, highlight=False))
        self.assertLessEqual({ch for ch in out.getvalue() if ord(ch) > 127}, {'·'})
        # A console that cannot encode the frame (ASCII locale) picks the ASCII frame itself.
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='ascii', errors='replace')
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}):
            render(Console(file=stream, width=60, highlight=False))
        stream.flush()
        lines = raw.getvalue().decode('ascii').splitlines()
        self.assertTrue(all(len(line) == 60 for line in lines), lines)
        self.assertTrue(lines[0].startswith('+-') and lines[0].endswith('-+'))

    def test_gradient_range_label_and_short_path(self):
        bar = cli_ui.SolidBar(100, 100, cli_ui.PINK, gradient=(cli_ui.PINK, cli_ui.BLUE)).text(20)
        styles = [span.style for span in bar.spans]
        self.assertEqual((len(styles), styles[0], styles[-1]), (20, cli_ui.PINK, cli_ui.BLUE))
        self.assertEqual(cli_ui.blend('#000000', '#FFFFFF', .5), '#808080')
        self.assertEqual(cli_ui.range_label('2026-08-31', '2026-10-06', 'Asia/Jakarta'), '31 Agu → 06 Okt 2026 · WIB')
        self.assertEqual(cli_ui.range_label('2025-12-30', '2026-01-02', 'Asia/Jakarta', '>'), '30 Des 2025 > 02 Jan 2026 · WIB')
        self.assertEqual(cli_ui.range_label('besok', '2026-10-06', 'UTC'), 'besok–2026-10-06 · UTC')
        self.assertEqual(cli_ui.short_path(Path.home()/'tools/media'), '~/tools/media')
        self.assertEqual(cli_ui.short_path('/opt/media'), '/opt/media')
        self.assertEqual([cli_ui.elapsed_label(v) for v in (.24, 3.21, 75)], ['240 ms', '3.2d', '1m 15d'])

    def test_receipts_always_for_problems_and_for_successes_only_in_small_runs(self):
        def run(total, failing):
            ui, out = self.interactive_ui()
            session = ui.downloading(total, self.media, self.state/'log')
            ui.session = session
            for index in range(total):
                session.thread_start(str(1000+index))
                ui.event({'type': 'start', 'path': f'{index}.jpg'})
                ui.event({'type': 'success', 'path': f'{index}.jpg'})
                session.thread_end(1 if index in failing else 0)
            return out.getvalue()
        small = run(3, {1})
        self.assertEqual(small.count('utas 100'), 3)
        self.assertIn('✓ utas 1000 · 1 foto', small)
        self.assertIn('✗ utas 1001 · 1 foto · kode keluar 1', small)
        large = run(cli_ui.RECEIPT_ALL_UP_TO+1, {2})
        self.assertEqual(large.count('utas '), 1)
        self.assertIn('✗ utas 1002', large)

    def test_stall_stops_motion_and_wait_counts_down(self):
        clock = [100.0]
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}), \
                patch.object(cli_ui.time, 'monotonic', side_effect=lambda: clock[0]), \
                redirect_stderr(io.StringIO()):
            ui = cli_ui.UI(plain=True)
            session = ui.downloading(2, self.media, self.state/'log')
            ui.session = session
            ui.event({'type': 'start', 'path': 'a.mp4'})
            ui.event({'type': 'progress', 'downloaded': 10, 'total': None, 'speed': 5})
            clock[0] += 2
            self.assertFalse(session.stalled())
            self.assertEqual(session.animation(), 2)
            self.assertNotIn('Menunggu data', ui.status_text().plain)
            clock[0] += 3
            self.assertTrue(session.stalled())
            self.assertEqual(session.animation(), cli_ui.STALL_AFTER)
            clock[0] += 60
            self.assertEqual(session.animation(), cli_ui.STALL_AFTER)
            self.assertIn('Menunggu data · 1m 5d', ui.status_text().plain)
            ui.event({'type': 'progress', 'downloaded': 20, 'total': None, 'speed': 5})
            self.assertFalse(session.stalled())
            with patch.object(cli_ui.time, 'time', return_value=1000.0):
                ui.event({'type': 'wait', 'reason': 'rate limit', 'until': 1042.0})
                self.assertFalse(session.stalled())
                text = ui.status_text().plain
                self.assertIn('Menunggu · rate limit', text)
                self.assertIn('lanjut 42d', text)
                self.assertEqual(text.count('█'), 10)
                self.assertNotIn('█', ui.status_text(True).plain)
            with patch.object(cli_ui.time, 'time', return_value=1021.0):
                self.assertEqual(ui.status_text().plain.count('█'), 5)
                self.assertIn('lanjut 21d', ui.status_text().plain)
            ui.event({'type': 'wait_end'})
            self.assertIsNone(ui.wait_until)
            self.assertEqual((session.level, session.message), ('busy', 'Melanjutkan'))

    def test_sparkline_scales_to_peak_and_keeps_width(self):
        ui = cli_ui.UI(plain=True)
        session = ui.downloading(1, self.media, self.state/'log')
        glyph = cli_ui.GLYPHS[True]['spark']
        self.assertEqual(session.sparkline(glyph), ' '*cli_ui.SPARK_WIDTH)
        session.speeds.extend([0, 50, 100, 25])
        line = session.sparkline(glyph)
        self.assertEqual((len(line), line.strip()), (cli_ui.SPARK_WIDTH, '▁▅█▃'))
        session.speeds.extend(range(1, 40))
        self.assertEqual(len(session.speeds), cli_ui.SPARK_WIDTH)

    def test_static_mode_has_no_motion_in_ui_object(self):
        with patch.dict(os.environ, {'X_BOOKMARKS_STATIC': '1', 'TERM': 'xterm-256color'}):
            ui = cli_ui.UI(plain=True)
            self.assertFalse(ui.motion)
            self.assertEqual(ui.spinner(), ui.g['run'])
            self.assertEqual(ui.refresh_rate(), 4)
            self.assertEqual(ui.bar(0, None, cli_ui.RED, lambda: 3.0, True).text(20).plain, '░'*20)
        with patch.dict(os.environ, {'SSH_CONNECTION': '1 2 3 4', 'TERM': 'xterm-256color'}):
            os.environ.pop('X_BOOKMARKS_STATIC', None)
            ui = cli_ui.UI(plain=True)
            self.assertTrue(ui.motion)
            self.assertEqual(ui.refresh_rate(), 4)

    def test_activity_leaves_a_receipt_and_checklist_marks_state(self):
        for fail in (False, True):
            ui, out = self.interactive_ui()
            with self.assertRaises(ValueError) if fail else redirect_stderr(io.StringIO()):
                with ui.activity('Langkah uji'):
                    if fail:
                        raise ValueError('uji')
            self.assertIn(('✗' if fail else '✓') + ' Langkah uji', out.getvalue())
        ui, out = self.interactive_ui(width=70)
        ui.checklist('Status alat', [('ok', 'Paket', 'gallery-dl 1.0'), ('warn', 'Snapshot', 'belum ada'),
                                     ('info', 'Alat', Path.home()/'tools/x')])
        text = out.getvalue()
        for expected in ('✓ Paket', '▲ Snapshot', '▸ Alat', '~/tools/x', '╭', 'Status alat'):
            self.assertIn(expected, text)
        buffer = io.StringIO()
        with redirect_stdout(buffer):                 # results go to stdout
            plain = cli_ui.UI(plain=True)
            plain.checklist('Status alat', [('ok', 'Paket', 'gallery-dl 1.0'), ('warn', 'Snapshot', 'belum ada')])
        self.assertEqual(buffer.getvalue().splitlines(), ['Informasi | Nilai', 'Paket | gallery-dl 1.0', 'Snapshot | belum ada'])

    def test_summary_lists_failed_threads_with_a_cap(self):
        ui, out = self.interactive_ui()
        session = ui.downloading(9, self.media, self.state/'log')
        session.failures = [(str(2000+i), 1) for i in range(7)]
        session.completed, session.failed, session.photos = 9, 7, 4
        ui.console.print(session.summary('warn', False))
        text = out.getvalue()
        self.assertIn('Selesai dengan masalah', text)
        for cid in range(2000, 2005):
            self.assertIn(f'utas {cid}', text)
        self.assertNotIn('utas 2005', text)
        self.assertIn('dan 2 lainnya', text)
        self.assertNotIn('photo', text)           # media folders are left out when there are problems
        ui.console.file.truncate(0)
        session.failures, session.failed = [], 0
        ui.console.print(session.summary('stopped', False))
        self.assertIn('Dihentikan', out.getvalue())
        self.assertIn('photo', out.getvalue())

    def test_missing_or_broken_settings_give_clear_errors(self):
        for name in ('bookmarks.py', 'cli_ui.py', 'media_layout.py'):
            (self.root/name).write_bytes((BASE/name).read_bytes())
        cases = [(None, 'settings.json belum ada. Jalankan: bash setup.sh'),
                 ('{broken', 'settings.json tidak valid'),
                 ('{"start": "2026-01-01"}', 'settings.json belum memuat: end, timezone, browser')]
        for content, expected in cases:
            with self.subTest(content=content):
                path = self.root/'settings.json'
                path.unlink(missing_ok=True)
                if content is not None:
                    path.write_text(content)
                result = subprocess.run([sys.executable, str(self.root/'bookmarks.py'), 'status', '--plain'],
                                        cwd=self.root, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('! GAGAL · ' + expected, result.stderr)
                self.assertNotIn('Errno', result.stderr)

    def test_text_files_are_utf8_whatever_the_locale(self):
        # AUD-005: ASCII locale and no UTF-8 mode; non-ASCII tweets must still round-trip through our own files.
        script = ('import json,sys\nfrom pathlib import Path\n'
                  f'sys.path.insert(0,{str(BASE)!r})\n'
                  'import bookmarks as tool, cli_ui\n'
                  'tool.UI_INSTANCE=cli_ui.UI(plain=True)\n'
                  'root=Path(sys.argv[1])\n'
                  'post={"tweet_id":1,"conversation_id":1,"author":{"id":7,"name":"kopi"},'
                  '"date_bookmarked":"2026-09-01 00:00:00","content":"\\u65e5\\u672c\\u8a9e \\U0001f600 caf\\u00e9"}\n'
                  'tool.write_json(root/"bookmarks.json",[[2,post]])\n'
                  'settings=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))\n'
                  'dest=tool.create_selection(settings,root)\n'
                  'manifest=json.loads((dest/"selection.json").read_text(encoding="utf-8"))\n'
                  'print(manifest["selected_bookmarks"][0]["content"].encode("unicode_escape").decode())\n'
                  'print((dest/"daftar-bookmark.csv").read_text(encoding="utf-8").encode("unicode_escape").decode())\n')
        settings = self.root/'settings.json'
        settings.write_text(json.dumps(SETTINGS), encoding='utf-8')
        env = {**os.environ, 'PYTHONUTF8': '0', 'PYTHONCOERCECLOCALE': '0', 'LC_ALL': 'C', 'TERM': 'xterm-256color'}
        env.pop('PYTHONIOENCODING', None)
        probe = self.root/'probe.py'
        probe.write_text(script, encoding='utf-8')
        result = subprocess.run([sys.executable, str(probe), str(self.state), str(settings)], env=env,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        expected = '\u65e5\u672c\u8a9e \U0001f600 caf\u00e9'.encode('unicode_escape').decode()
        lines = result.stdout.decode('ascii').splitlines()
        self.assertEqual(lines[0], expected)
        self.assertIn(expected, lines[1])

    # ----- AUD-001: the orchestration around the worker, run against local fixture jobs only -----
    def orchestrate(self, modes, seen=None):
        """Real download() with fixture jobs. modes maps conversation_id -> fixture mode; returns (dest, call)."""
        dest = self.state/'selections'/tool.range_key(SETTINGS)
        dest.mkdir(parents=True)
        threads = [{'conversation_id': cid, 'author_ids': ['42'], 'bookmarks': []} for cid in modes]
        (dest/'selection.json').write_text(json.dumps({
            'start': SETTINGS['start'], 'end_inclusive': SETTINGS['end'], 'timezone': SETTINGS['timezone'],
            'threads': threads, 'selected_bookmarks': []}), encoding='utf-8')

        def fixture_args(settings, options):
            if seen is not None:
                seen.append(options)
            return self.command(modes[options[-1].rsplit('/', 1)[1]])

        def call(limit=None, all_authors=False, run_gallery=None, reconcile=False):
            args = types.SimpleNamespace(limit=limit, all_authors=all_authors, reconcile=reconcile)
            with patch.object(tool, 'BASE', self.root), patch.object(tool, 'STATE', self.state), \
                    patch.object(tool, 'MEDIA', self.media), patch.object(tool, 'gallery_args', fixture_args), \
                    patch.object(tool, 'run_gallery', run_gallery or tool.run_gallery), redirect_stderr(io.StringIO()):
                return tool.download(SETTINGS, dest, args, ui=cli_ui.UI(plain=True))
        return dest, call

    def last_run(self, dest, mode='bookmarked-authors'):
        return json.loads((dest/f'last-run-{mode}.json').read_text(encoding='utf-8'))

    def test_download_runs_every_thread_and_reports_the_last_run(self):
        seen = []
        dest, call = self.orchestrate({'1000': 'mix', '2000': 'mix'}, seen)
        call()
        report = self.last_run(dest)
        self.assertEqual((report['attempted_threads'], report['completed_threads'], report['failed_threads']), (2, 2, []))
        self.assertEqual((report['downloaded_photos'], report['downloaded_videos'], report['skipped_files']), (2, 2, 4))
        self.assertFalse(report['interrupted'])
        first = seen[0]                                   # the command line built for the first thread
        self.assertEqual(first[first.index('--post-filter') + 1],
                         tool.thread_filter({'conversation_id': '1000', 'author_ids': ['42']}))
        self.assertEqual(first[first.index('--download-archive') + 1], str(self.state/'downloaded.sqlite3'))
        self.assertIn('conversations=true', first)
        self.assertEqual(first[-1], 'https://x.com/i/web/status/1000')
        call(all_authors=True)                            # other mode: its own report, filter loses the author clause
        self.assertTrue((dest/'last-run-all-authors.json').exists())
        self.assertEqual(seen[-1][seen[-1].index('--post-filter') + 1], 'conversation_id == 2000')

    def test_download_limit_counts_threads_not_files(self):
        dest, call = self.orchestrate({'1000': 'mix', '2000': 'mix', '3000': 'mix'})
        call(limit=1)
        report = self.last_run(dest)
        self.assertEqual((report['attempted_threads'], report['completed_threads']), (1, 1))
        self.assertEqual(len(list(media_files(self.media))), 4)

    def test_download_continues_after_a_failed_thread_and_then_reports_the_error(self):
        dest, call = self.orchestrate({'1111': 'crash', '2222': 'mix'})
        with self.assertRaisesRegex(ValueError, '1 utas mengalami error'):
            call()
        report = self.last_run(dest)
        self.assertEqual(report['completed_threads'], 2)
        self.assertEqual([item['conversation_id'] for item in report['failed_threads']], ['1111'])
        self.assertEqual(len(list(media_files(self.media))), 4)   # the second thread still downloaded

    def test_download_interrupt_propagates_and_is_recorded(self):
        dest, call = self.orchestrate({'1000': 'mix', '2000': 'mix'})
        real, calls = tool.run_gallery, []

        def interrupting(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real(*args, **kwargs)
        with self.assertRaises(KeyboardInterrupt):
            call(run_gallery=interrupting)
        report = self.last_run(dest)
        self.assertTrue(report['interrupted'])
        self.assertEqual((report['attempted_threads'], report['completed_threads']), (2, 1))

    def test_download_refuses_bad_manifest_and_legacy_media_at_the_root(self):
        dest, call = self.orchestrate({'1000': 'mix'})
        (self.media/'old.jpg').write_bytes(b'legacy')
        with self.assertRaisesRegex(ValueError, 'organize'):
            call()
        (self.media/'old.jpg').unlink()
        manifest = json.loads((dest/'selection.json').read_text(encoding='utf-8'))
        manifest['end_inclusive'] = '2000-01-01'
        (dest/'selection.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Rentang manifest berbeda'):
            call()
        (dest/'selection.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Belum ada daftar terpilih'):
            call()

    def test_extract_keeps_the_previous_snapshot_unless_the_scan_is_complete(self):
        snapshot = self.state/'bookmarks.json'
        snapshot.write_text('PREVIOUS', encoding='utf-8')
        mode = {'value': 'scanbad'}

        def fixture_args(settings, options):
            return self.command(mode['value'], '--dump-json', '-o', 'output.jsonl=false')
        with patch.object(tool, 'BASE', self.root), patch.object(tool, 'STATE', self.state), \
                patch.object(tool, 'gallery_args', fixture_args), redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'Scan gagal'):
                tool.extract(SETTINGS, snapshot)
            self.assertEqual(snapshot.read_text(encoding='utf-8'), 'PREVIOUS')
            mode['value'] = 'wait'
            posts = tool.extract(SETTINGS, snapshot)
        self.assertEqual(len(posts), 4)
        self.assertEqual(len(list(tool.directory_posts(snapshot))), 4)
        self.assertFalse(snapshot.with_name('bookmarks.json.partial').exists())

    def test_resolve_browser_picks_the_single_firefox_profile_with_an_x_session(self):
        home = self.root/'home'
        root = home/'.mozilla/firefox'

        def profile(name, cookies):
            folder = root/name
            folder.mkdir(parents=True)
            with closing(sqlite3.connect(folder/'cookies.sqlite')) as con, con:
                con.execute('CREATE TABLE moz_cookies (host TEXT, name TEXT)')
                con.executemany('INSERT INTO moz_cookies VALUES (?, ?)', cookies)
            return folder
        with patch.object(tool, 'STATE', self.state), patch.object(tool.Path, 'home', return_value=home), \
                redirect_stderr(io.StringIO()), patch('builtins.print'):
            self.assertEqual(tool.resolve_browser('chrome'), 'chrome')        # only 'firefox' is resolved
            with self.assertRaisesRegex(ValueError, 'Sesi X tidak ditemukan'):
                tool.resolve_browser('firefox')                                # no profile folder at all
            blank = profile('blank.default', [('.example.com', 'sid')])
            with self.assertRaisesRegex(ValueError, 'Sesi X tidak ditemukan'):
                tool.resolve_browser('firefox')                                # a profile, but no X session
            target = profile('main.default', [('.x.com', 'auth_token'), ('.example.com', 'sid')])
            before = (target/'cookies.sqlite').read_bytes()
            self.assertEqual(tool.resolve_browser('firefox'), f'firefox/.x.com:{target}')
            self.assertEqual((target/'cookies.sqlite').read_bytes(), before)  # the real cookie jar is never touched
            self.assertEqual(list(self.state.iterdir()), [])                   # and no copy of it is left behind
            other = profile('work.default', [('x.com', 'auth_token')])
            with self.assertRaisesRegex(ValueError, 'Beberapa profil') as raised:
                tool.resolve_browser('firefox')
            self.assertIn(str(target), str(raised.exception))
            self.assertIn(str(other), str(raised.exception))
            self.assertNotIn(str(blank), str(raised.exception))

    # ----- AUD-003: read-only commands are not blocked by a running download -----
    def isolated_tool(self):
        for name in ('bookmarks.py', 'cli_ui.py', 'media_layout.py'):
            (self.root/name).write_bytes((BASE/name).read_bytes())
        (self.root/'settings.json').write_bytes((BASE/'settings.example.json').read_bytes())

    def run_cli(self, *args, plain=True):
        env = {**os.environ, 'TERM': 'xterm-256color'}
        return subprocess.run([sys.executable, str(self.root/'bookmarks.py'), *args, *(['--plain'] if plain else [])],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=30)

    def sample_post(self, tweet_id=10, **extra):
        return {'tweet_id': tweet_id, 'conversation_id': tweet_id, 'author': {'id': 7, 'name': 'kopi'},
                'date_bookmarked': '2026-09-01 00:00:00', 'content': 'halo', **extra}

    def test_read_only_commands_ignore_the_run_lock_but_mutating_ones_respect_it(self):
        import fcntl
        self.isolated_tool()
        with (self.state/'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)        # what a long download holds
            status = self.run_cli('status')
            self.assertEqual(status.returncode, 0, status.stderr)
            listing = self.run_cli('list')                          # gets past the lock; no selection exists yet
            self.assertNotIn('Proses lain', listing.stderr)
            self.assertIn('Belum ada pilihan', listing.stderr)
            for action in ('select', 'scan', 'check', 'download', 'organize'):
                result = self.run_cli(action)
                self.assertEqual(result.returncode, 1, (action, result.stderr))
                self.assertIn('Proses lain masih berjalan', result.stderr, action)

    # ----- AUD-002: a mass of "missing" files is not silently turned into a mass re-download -----
    def seed_archive(self, total, with_files):
        db = self.state/'downloaded.sqlite3'
        (self.media/'photo').mkdir(exist_ok=True)
        for leftover in (self.media/'photo').iterdir():          # every scenario starts from an empty collection
            leftover.unlink()
        with closing(sqlite3.connect(db)) as con, con:
            con.execute('CREATE TABLE archive (entry TEXT PRIMARY KEY)')
            con.executemany('INSERT INTO archive VALUES (?)', [(f'twitter_{5000+i}_1',) for i in range(total)])
        for i in range(with_files):
            (self.media/'photo'/f'2020_a_1000_{5000+i}_1.jpg').write_bytes(b'x')
        return db

    def archive_entries(self, db):
        with closing(sqlite3.connect(db)) as con:
            return {row[0] for row in con.execute('SELECT entry FROM archive')}

    def test_reconcile_stops_for_a_mass_of_missing_files_and_can_be_forced_with_a_backup(self):
        db = self.seed_archive(100, 40)                             # 60 missing = 60% of the archive
        with self.assertRaisesRegex(ValueError, r'60 dari 100 entri arsip \(60%\).*--reconcile'):
            tool.reconcile_archive(db, self.media)
        self.assertEqual(len(self.archive_entries(db)), 100)        # nothing was removed
        removed = self.state/'archive-removed'
        self.assertEqual(tool.reconcile_archive(db, self.media, force=True, backup_dir=removed), 60)
        self.assertEqual(len(self.archive_entries(db)), 40)
        backup, = removed.iterdir()
        self.assertEqual(len(backup.read_text(encoding='utf-8').split()), 60)
        self.assertIn('twitter_5099_1', backup.read_text(encoding='utf-8'))    # restorable: the exact entries are listed

    def test_reconcile_stays_quiet_below_either_threshold(self):
        db = self.seed_archive(100, 90)                             # 10 missing: below the absolute floor
        self.assertEqual(tool.reconcile_archive(db, self.media, backup_dir=self.state/'archive-removed'), 10)
        self.assertEqual(len(list((self.state/'archive-removed').iterdir())), 1)   # still backed up before removal
        db.unlink()
        db = self.seed_archive(10, 1)                               # 9 of 10 missing: high share, tiny archive
        self.assertEqual(tool.reconcile_archive(db, self.media), 9)
        db.unlink()
        db = self.seed_archive(1000, 940)                           # 60 missing but only 6% of a big archive
        self.assertEqual(tool.reconcile_archive(db, self.media), 60)

    def test_download_needs_the_reconcile_flag_for_a_mass_of_missing_files(self):
        dest, call = self.orchestrate({'1000': 'mix'})
        db = self.seed_archive(100, 0)
        with self.assertRaisesRegex(ValueError, '--reconcile'):
            call()
        self.assertEqual(len(self.archive_entries(db)), 100)
        self.assertFalse((dest/'last-run-bookmarked-authors.json').exists())    # stopped before any thread ran
        call(reconcile=True)
        self.assertEqual(self.last_run(dest)['completed_threads'], 1)
        self.assertEqual(len(list((self.state/'archive-removed').iterdir())), 1)

    # ----- AUD-004: tweet text cannot turn into a spreadsheet formula -----
    def test_csv_cells_that_look_like_formulas_stay_text(self):
        import csv
        texts = ['=1+1', '@user hai', '-5 derajat', '+62 811', 'biasa saja', 'a=b']
        posts = [{'tweet_id': 10+i, 'conversation_id': 10+i, 'author': {'id': 7, 'name': '=cmd' if i == 0 else 'kopi'},
                  'date_bookmarked': '2026-09-01 00:00:00', 'content': text} for i, text in enumerate(texts)]
        tool.write_json(self.state/'bookmarks.json', [[2, post] for post in posts])
        with patch.object(tool, 'UI_INSTANCE', cli_ui.UI(plain=True)), redirect_stderr(io.StringIO()):
            dest = tool.create_selection(SETTINGS, self.state)
        with (dest/'daftar-bookmark.csv').open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(sorted(row['content'] for row in rows),
                         sorted(["'=1+1", "'@user hai", "'-5 derajat", "'+62 811", 'biasa saja', 'a=b']))
        self.assertIn("'=cmd", {row['author_name'] for row in rows})
        manifest = json.loads((dest/'selection.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(item['content'] for item in manifest['selected_bookmarks']), sorted(texts))   # JSON stays untouched

    # ----- UX-001: results on stdout, chatter on stderr -----
    def test_results_go_to_stdout_and_chatter_to_stderr(self):
        self.isolated_tool()
        (self.root/'settings.json').write_text(json.dumps(SETTINGS), encoding='utf-8')
        dest = self.state/'selections'/tool.range_key(SETTINGS)
        dest.mkdir(parents=True)
        item = {'bookmarked_at': '2026-09-01T07:00:00+07:00', 'author_name': 'kopi', 'url': 'https://x.com/i/web/status/10'}
        (dest/'selection.json').write_text(json.dumps({'selected_bookmarks': [item], 'threads': [{}], 'snapshot_at': 'kemarin'}),
                                           encoding='utf-8')
        listing = self.run_cli('list')
        self.assertEqual(listing.stdout.splitlines(), ['Tanggal bookmark | Penulis | URL',
                                                       '2026-09-01T07:00:00+07:00 | @kopi | https://x.com/i/web/status/10'])
        self.assertIn('X Bookmarks ·', listing.stderr)
        self.assertIn('1 bookmark · 1 utas · snapshot kemarin', listing.stderr)
        self.assertIn('Daftar lengkap', listing.stderr)
        self.assertNotIn('Daftar lengkap', listing.stdout)
        status = self.run_cli('status')
        self.assertEqual(status.stdout.splitlines()[0], 'Informasi | Nilai')
        self.assertIn(f'Versi | {tool.VERSION} ', status.stdout)
        self.assertEqual(len(status.stderr.splitlines()), 1)             # only the heading
        both = subprocess.run([sys.executable, str(self.root/'bookmarks.py'), 'list', '--plain'], cwd=self.root,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30,
                              env={**os.environ, 'TERM': 'xterm-256color'}).stdout
        self.assertIn('@kopi', both)                                     # `> file 2>&1` still captures everything
        self.assertIn('Daftar lengkap', both)

    # ----- UX-005: palette follows the terminal's background -----
    def test_theme_detection_and_light_palette_contrast(self):
        detect = cli_ui.detect_theme
        self.assertEqual(detect({}), 'dark')
        self.assertEqual([detect({'COLORFGBG': value}) for value in ('0;15', '0;default;15', '0;7', '15;0', '15;default;0', 'junk')],
                         ['light', 'light', 'light', 'dark', 'dark', 'dark'])
        self.assertEqual(detect({'X_BOOKMARKS_THEME': 'light', 'COLORFGBG': '15;0'}), 'light')   # explicit choice wins
        self.assertEqual(detect({'X_BOOKMARKS_THEME': ' DARK ', 'COLORFGBG': '0;15'}), 'dark')
        self.assertEqual(detect({'X_BOOKMARKS_THEME': 'neon'}), 'dark')

        def luminance(color):
            channels = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
            r, g, b = [c/12.92 if c <= 0.03928 else ((c + 0.055)/1.055)**2.4 for c in channels]
            return 0.2126*r + 0.7152*g + 0.0722*b

        def contrast(a, b):
            high, low = sorted((luminance(a), luminance(b)), reverse=True)
            return (high + 0.05)/(low + 0.05)
        light, dark = cli_ui.PALETTES['light'], cli_ui.PALETTES['dark']
        for background in ('#FFFFFF', '#FAFAFA'):
            for name, color in light.items():
                if name != 'empty':                                      # the bar track is meant to stay quiet
                    self.assertGreaterEqual(contrast(color, background), 4.5, (name, background))
        for name, color in dark.items():
            if name != 'empty':
                self.assertGreaterEqual(contrast(color, '#0D1117'), 4.5, name)
        self.assertEqual((dark['pink'], dark['red'], dark['blue'], dark['file']), ('#FFC2D4', '#E8384F', '#A8D8F0', '#E8384F'))
        self.assertNotIn(light['file'], (light['red'], light['pink']))   # in the light palette red means failure only

    def test_light_theme_reaches_the_terminal(self):
        output = self.terminal_demo(env_extra={'X_BOOKMARKS_THEME': 'light'})
        self.assertIn('38;2;180;50;94', output)                          # light pink
        self.assertIn('38;2;14;116;144', output)                         # the file bar's own accent
        self.assertNotIn('38;2;255;194;212', output)
        self.assertNotIn('38;2;232;56;79', output)

    # ----- UX-002 / UX-003: one parser per action, --version, an epilog with the usual workflow -----
    def test_version_help_and_per_action_flags(self):
        self.isolated_tool()
        version = self.run_cli('--version', plain=False)
        self.assertEqual((version.returncode, version.stdout.strip()), (0, f'x-bookmarks {tool.VERSION}'))
        overview = self.run_cli('--help', plain=False).stdout
        for step in ('./x-bookmarks status', './x-bookmarks scan', './x-bookmarks download --limit 3', 'ke stdout'):
            self.assertIn(step, overview)
        listing, download = (self.run_cli(action, '--help', plain=False).stdout for action in ('list', 'download'))
        self.assertTrue(all(flag in listing for flag in ('--limit', '--json', '--start')))
        self.assertTrue(all(flag not in listing for flag in ('--all-authors', '--reconcile')))
        self.assertTrue(all(flag in download for flag in ('--limit', '--all-authors', '--reconcile')))
        self.assertNotIn('--json', download)
        for argv in (['scan', '--limit', '1'], ['list', '--all-authors'], ['status', '--reconcile'], ['download', '--json'],
                     ['clean', '--start', '2026-01-01'], ['list', '--limit', '0'], ['clean', '--keep', '-1'],
                     ['update', '--limit', '1'], ['status', '--check'], []):
            self.assertEqual(self.run_cli(*argv, plain=False).returncode, 2, argv)

    def test_range_options_work_before_and_after_the_action(self):
        self.isolated_tool()
        (self.root/'settings.json').write_text(json.dumps(SETTINGS), encoding='utf-8')
        for argv in (['--start', '2026-01-01', '--end', '2026-01-31', 'status'], ['status', '--start', '2026-01-01', '--end', '2026-01-31'],
                     ['--start', '2026-01-01', 'status', '--end', '2026-01-31']):
            result = self.run_cli(*argv)
            self.assertEqual(result.returncode, 0, (argv, result.stderr))
            self.assertIn('2026-01-01–2026-01-31', result.stderr, argv)
        self.assertEqual(self.run_cli('--plain', 'status', plain=False).returncode, 0)

    def test_next_step_hint_repeats_the_range_and_quotes_the_browser_and_bad_ids_are_explained(self):
        self.isolated_tool()
        (self.root/'settings.json').write_text(json.dumps(SETTINGS), encoding='utf-8')
        tool.write_json(self.state/'bookmarks.json', [[2, self.sample_post()]])
        result = self.run_cli('select', '--browser', 'firefox/.x.com:/a b')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Berikutnya: ./x-bookmarks list --limit 10 --start {SETTINGS['start']} --end {SETTINGS['end']} "
                      "--browser 'firefox/.x.com:/a b'", result.stderr)
        self.assertNotIn('Berikutnya', result.stdout)
        tool.write_json(self.state/'bookmarks.json', [[2, self.sample_post(tweet_id='abc')]])      # UX-004
        bad = self.run_cli('select')
        self.assertEqual(bad.returncode, 1)
        self.assertIn('tweet_id tidak valid pada tweet abc', bad.stderr)
        self.assertNotIn('invalid literal', bad.stderr)

    # ----- FEAT-003: --json, schema locked by this test -----
    def test_json_output_is_the_only_thing_on_stdout_and_its_schema_is_stable(self):
        self.isolated_tool()
        (self.root/'settings.json').write_text(json.dumps(SETTINGS), encoding='utf-8')
        status = json.loads(self.run_cli('status', '--json').stdout)
        self.assertEqual(sorted(status), sorted(['schema', 'version', 'python', 'packages', 'packages_ready', 'tool', 'state_dir',
                                                 'photo_dir', 'video_dir', 'browser', 'snapshot', 'media', 'last_run']))
        self.assertEqual((status['schema'], status['version'], status['snapshot'], status['last_run']), (1, tool.VERSION, None, None))
        self.assertEqual(sorted(status['media']), ['at_root', 'partial', 'photos', 'videos'])
        self.assertEqual(sorted(status['packages']), ['gallery-dl', 'rich'])
        dest = self.state/'selections'/tool.range_key(SETTINGS)
        dest.mkdir(parents=True)
        items = [{'bookmarked_at': f'2026-09-0{n}T07:00:00+07:00', 'author_name': 'kopi', 'url': f'https://x.com/i/web/status/{n}',
                  'content': 'caf\u00e9 \U0001f600'} for n in (1, 2, 3)]
        (dest/'selection.json').write_text(json.dumps({'selected_bookmarks': items, 'threads': [{}, {}], 'snapshot_at': 'kemarin'}), encoding='utf-8')
        everything = self.run_cli('list', '--json')
        data = json.loads(everything.stdout)
        self.assertEqual(sorted(data), ['bookmarks', 'range', 'schema', 'snapshot_at', 'total_bookmarks', 'total_threads'])
        self.assertEqual((len(data['bookmarks']), data['total_bookmarks'], data['total_threads']), (3, 3, 2))
        self.assertEqual(data['bookmarks'][0]['content'], 'caf\u00e9 \U0001f600')
        self.assertTrue(everything.stdout.isascii())                       # valid whatever the locale
        self.assertEqual(len(json.loads(self.run_cli('list', '--json', '--limit', '2').stdout)['bookmarks']), 2)
        self.assertNotIn('Tanggal bookmark', everything.stdout)

    def test_check_json_lists_the_samples_and_still_hints_on_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['bookmarks.py', 'check', '--json']), patch.object(tool, 'load_settings', return_value=dict(SETTINGS)), \
                patch.object(tool, 'resolve_browser', side_effect=lambda value: value), patch.object(tool, 'extract', return_value=[self.sample_post(55)]), \
                patch.object(tool, 'STATE', self.state), patch.object(tool, 'MEDIA', self.media), patch.object(tool, 'UI_INSTANCE', None), \
                redirect_stdout(out), redirect_stderr(err):
            self.assertFalse(tool.main())
        data = json.loads(out.getvalue())
        self.assertEqual(sorted(data), ['samples', 'schema'])
        self.assertEqual(data['samples'], [{'bookmarked_at': '2026-09-01T07:00:00+07:00', 'author_name': 'kopi', 'url': 'https://x.com/i/web/status/55'}])
        self.assertIn('Berikutnya: ./x-bookmarks scan', err.getvalue())

    # ----- FEAT-001: status shows the last download of the active range -----
    def test_status_shows_the_last_run_of_the_active_range(self):
        ui = cli_ui.UI(plain=True)

        def status_output(**kwargs):
            buffer = io.StringIO()
            with patch.object(tool, 'STATE', self.state), patch.object(tool, 'MEDIA', self.media), patch.object(tool, 'UI_INSTANCE', ui), \
                    redirect_stdout(buffer):
                tool.status(SETTINGS, **kwargs)
            return buffer.getvalue()
        self.assertIn('Run terakhir | belum ada untuk rentang ini', status_output())
        dest, call = self.orchestrate({'1000': 'mix', '2000': 'mix'})
        call()
        self.assertRegex(status_output(), r'Run terakhir \| \d{4}-\d{2}-\d{2} \d{2}:\d{2} · 2/2 utas · 0 gagal · 2 foto · 2 video · 4 dilewati · mode bookmarked-authors')
        self.assertEqual(json.loads(status_output(as_json=True))['last_run']['planned_threads'], 2)
        newer = dest/'last-run-all-authors.json'                           # the newest report of any mode wins
        newer.write_text(json.dumps({**self.last_run(dest), 'mode': 'all-authors', 'completed_threads': 1, 'planned_threads': 3,
                                     'failed_threads': [{'conversation_id': '1', 'exit_code': 1}]}), encoding='utf-8')
        os.utime(newer, (time.time() + 60,) * 2)
        self.assertIn('1/3 utas · 1 gagal · 2 foto · 2 video · 4 dilewati · mode all-authors', status_output())
        view, shown = self.interactive_ui(width=100)                        # the marker: ▲ when something needs attention
        with patch.object(tool, 'STATE', self.state), patch.object(tool, 'MEDIA', self.media), patch.object(tool, 'UI_INSTANCE', view):
            tool.status(SETTINGS)
        self.assertIn('▲ Run terakhir', shown.getvalue())
        newer.write_text(json.dumps({**self.last_run(dest), 'interrupted': True, 'failed_threads': []}), encoding='utf-8')
        os.utime(newer, (time.time() + 90,) * 2)
        self.assertIn('dihentikan', status_output())
        newer.unlink()
        view, shown = self.interactive_ui(width=100)
        with patch.object(tool, 'STATE', self.state), patch.object(tool, 'MEDIA', self.media), patch.object(tool, 'UI_INSTANCE', view):
            tool.status(SETTINGS)
        self.assertIn('✓ Run terakhir', shown.getvalue())                  # a clean run is marked ok
        newer.write_text('{"finished_at": "oops"}', encoding='utf-8')
        os.utime(newer, (time.time() + 120,) * 2)
        self.assertIn('Run terakhir | berkas last-run tidak terbaca', status_output())
        newer.write_text('not json', encoding='utf-8')                     # unreadable JSON is skipped for the older report
        self.assertIn('2/2 utas', status_output())

    # ----- UX-004: an unexpected exception is one line for the user and a traceback in .state/crash.log -----
    def test_unexpected_errors_are_one_line_with_the_traceback_in_a_private_log(self):
        self.isolated_tool()
        (self.root/'settings.json').write_text(json.dumps({**SETTINGS, 'timezone': 5}), encoding='utf-8')
        result = self.run_cli('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('! GAGAL · Kesalahan tak terduga (TypeError', result.stderr)
        self.assertIn('crash.log', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        log = self.state/'crash.log'
        self.assertIn('Traceback', log.read_text(encoding='utf-8'))
        self.assertIn("argv=['status', '--plain']", log.read_text(encoding='utf-8'))
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    # ----- FEAT-002: clean -----
    def test_clean_is_a_dry_run_until_yes_needs_no_settings_and_respects_the_lock(self):
        import fcntl
        self.isolated_tool()
        (self.root/'release_tools.py').write_bytes((BASE/'release_tools.py').read_bytes())
        (self.root/'settings.json').unlink()                                # maintenance must not depend on a valid setup
        for n in (1, 2, 3):
            (self.state/'program-backups'/f'v3.1.1-{n}').mkdir(parents=True)
        dry = self.run_cli('clean', '--keep', '1')
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(dry.stdout.splitlines()[0], 'Kategori | Akan dihapus | Dipertahankan | Ukuran | Catatan')
        self.assertIn('program-backups | 2 | 1 | 0 B', dry.stdout)
        self.assertIn('Dry-run', dry.stderr)
        self.assertEqual(len(list((self.state/'program-backups').iterdir())), 3)
        done = self.run_cli('clean', '--keep', '1', '--yes')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines()[0].split(' | ')[1], 'Dihapus')
        self.assertEqual(sorted(p.name for p in (self.state/'program-backups').iterdir()), ['v3.1.1-3'])
        with (self.state/'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = self.run_cli('clean', '--yes')
        self.assertEqual(busy.returncode, 1)
        self.assertIn('Proses lain masih berjalan', busy.stderr)

    # ----- AUD-007: the dependencies of the direct pins are pinned too, in requirements.txt -----
    def test_transitive_pins_cover_exactly_the_dependencies_of_the_direct_pins(self):
        import importlib.metadata as md
        canon = lambda name: re.sub(r'[-_.]+', '-', name).lower()
        lines = [line.strip() for line in (BASE/'requirements.txt').read_text(encoding='utf-8').splitlines()
                 if line.strip() and not line.startswith('#')]
        self.assertTrue(all(re.fullmatch(r'[A-Za-z0-9._-]+==[0-9][A-Za-z0-9.]*', line) for line in lines), lines)
        pins = {canon(name): version for name, _, version in (line.partition('==') for line in lines)}
        self.assertEqual([canon(line.partition('==')[0]) for line in lines[:2]], ['gallery-dl', 'rich'])   # hand-chosen pins first
        extra = {name: version for name, version in pins.items() if name not in ('gallery-dl', 'rich')}
        for name, version in pins.items():
            self.assertEqual(md.version(name), version, name)         # a stale or missing pin fails here
        seen, todo = set(), ['gallery-dl', 'rich']
        while todo:
            for line in md.requires(todo.pop()) or []:
                if 'extra ==' in line:
                    continue
                dependency = canon(re.match(r'[A-Za-z0-9._-]+', line)[0])
                try:
                    md.version(dependency)
                except md.PackageNotFoundError:
                    continue                                          # a marker kept it out of this interpreter
                if dependency not in seen:
                    seen.add(dependency)
                    todo.append(dependency)
        self.assertEqual(seen, set(extra))                            # nothing the pins pull in is left floating

    # ----- DX-001: documents that name the version -----
    def test_documents_name_the_release_version(self):
        for name in ('README.md', 'PANDUAN.md', 'INSTALL-STATUS.md'):
            title = (BASE/name).read_text(encoding='utf-8').splitlines()[0]
            self.assertIn(tool.VERSION, title, name)
        self.assertEqual(json.loads((BASE/'release.json').read_text(encoding='utf-8'))['version'], tool.VERSION)

    def test_dependency_pins_have_one_source(self):
        import gallery_worker
        lines = [line.strip() for line in (BASE/'requirements.txt').read_text(encoding='utf-8').splitlines()
                 if line.strip() and not line.startswith('#')]
        pins = {name: version for name, _, version in (line.partition('==') for line in lines[:2])}   # the two hand-chosen pins
        self.assertEqual(set(pins), {'gallery-dl', 'rich'})
        self.assertEqual(gallery_worker.pinned_version('gallery-dl'), pins['gallery-dl'])
        with self.assertRaises(ValueError):
            gallery_worker.pinned_version('tidak-ada')
        for name in ('setup.sh', 'gallery_worker.py'):
            for version in pins.values():
                self.assertNotIn(version, (BASE/name).read_text(), name)
        for name in ('README.md', 'PANDUAN.md'):        # prose must follow requirements.txt
            for version in pins.values():
                self.assertIn(version, (BASE/name).read_text(), name)

    def test_gallery_dl_routing_rules_match_media_layout(self):
        import ast
        import re
        rules = json.loads((BASE/'gallery-dl.json').read_text())['extractor']['twitter']['directory']
        found = {}
        for expression, folder in rules.items():
            match = re.search(r" in (\(.*\))$", expression)
            if match:
                found[tuple(folder)] = set(ast.literal_eval(match[1]))
        self.assertEqual(found, {('photo',): set(PHOTO), ('video',): set(VIDEO)})

if __name__=='__main__':unittest.main(verbosity=2)

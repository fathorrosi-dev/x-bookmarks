"""Local regression suite. Run: .venv/bin/python -m unittest discover -s tests -v"""
import argparse
from contextlib import closing, redirect_stderr
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
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
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import bookmarks as tool
import cli_ui
from media_layout import PHOTO, VIDEO, is_partial, media_files, media_kind, organize
from gallery_worker import Events

SETTINGS = json.loads((BASE / 'settings.example.json').read_text())

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
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}):
            ui = cli_ui.UI(plain=True)
        ui.interactive = True
        ui.console = Console(file=io.StringIO(), width=width, force_terminal=True, color_system=None, highlight=False)
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
        with redirect_stderr(buffer):
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

    def test_dependency_pins_have_one_source(self):
        import gallery_worker
        pins = dict(line.split('==') for line in (BASE/'requirements.txt').read_text().split())
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

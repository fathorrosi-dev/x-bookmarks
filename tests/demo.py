"""UI-only preview: no network, files, cookies, archive, or downloads."""
import argparse
import sys
import time
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from cli_ui import UI
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--plain',action='store_true')
args=parser.parse_args()
ui=UI(plain=args.plain)
ui.heading({'start':'2026-08-31','end':'2026-10-06','timezone':'Asia/Jakarta'})
try:
    with ui.downloading(2,BASE/'media',BASE/'.state/download.log') as session:
        session.thread_start('contoh-1')
        ui.event({'type':'start','path':'2026-08-31_contoh_1000_1001_1.jpg','author':'contoh'})
        for i in range(0,101,5):
            ui.event({'type':'progress','total':1048576,'downloaded':1048576*i//100,'speed':524288})
            time.sleep(.06)
        ui.event({'type':'success','path':'contoh.jpg'})
        session.thread_end(0)
        session.thread_start('contoh-2')
        ui.event({'type':'start','path':'2026-09-01_contoh_1000_1002_1.mp4','author':'contoh'})
        for i in range(4):
            ui.event({'type':'progress','total':None,'downloaded':i*524288,'speed':16777216})
            time.sleep(.1)
        ui.event({'type':'wait','reason':'jeda simulasi','until':time.time()+.5})
        time.sleep(.5)
        ui.event({'type':'wait_end'})
        for i in range(0,101,5):
            ui.event({'type':'progress','total':25165824,'downloaded':25165824*i//100,'speed':16777216})
            time.sleep(.06)
        ui.event({'type':'success','path':'contoh.mp4'})
        session.thread_end(0)
except KeyboardInterrupt:
    sys.exit(130)
ui.say('Preview selesai · semua angka di atas adalah simulasi.')

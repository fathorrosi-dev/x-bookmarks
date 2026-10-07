"""Native jobs with local fixture media; never contacts X or reads cookies."""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, sys.argv.pop(1))
from gallery_dl import extractor
from gallery_dl.extractor.common import Extractor, Message
from gallery_worker import main

class Fixture(Extractor):
    category = 'twitter'
    subcategory = 'fixture'
    pattern = r'fixture:(.+)'
    def items(self):
        mode = self.url.split(':', 1)[1]
        if mode == 'crash':
            import os
            os._exit(0)  # Deliberately no final worker event.
        if mode == 'wait':
            self.wait(seconds=0.01, adjust=0, reason='rate limit fixture')
        urls = [('jpg', 'text:photo bytes'), ('mp4', 'text:video bytes'),
                ('GIF', 'text:animated bytes'), ('webm', 'text:video2 bytes')]
        if mode.startswith('http|'):
            origin = mode.split('|', 1)[1]
            urls = [('jpg', origin + '/photo'), ('mp4', origin + '/video'),
                    ('', origin + '/unknown'), ('mp4', origin + '/broken'),
                    ('mp4', origin + '/flaky')]
        if mode == 'unknown':
            urls = [('bin', 'text:other bytes')]
        for index, (ext, url) in enumerate(urls, 1):
            data = {'tweet_id':1000+index, 'conversation_id':1000, 'date':datetime(2020,1,1),
                    'author':{'id':42,'name':'fixture'}, 'num':1, 'extension':ext,'count':1,
                    'date_bookmarked':'2026-09-01 00:00:00'}
            yield Message.Directory, '', data
            yield Message.Url, url, data.copy()
        if mode == 'scanbad':
            raise ValueError('fixture scan error after partial results')

native_find = extractor.find
def find(url):
    return Fixture.from_url(url) if url.startswith('fixture:') else native_find(url)
extractor.find = find
sys.exit(main())

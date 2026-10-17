"""Release protocol, real installation/rollback and interruption without X login."""
import json,os,re,shutil,signal,subprocess,sys,tempfile,time,unittest,zipfile
from pathlib import Path,PurePosixPath
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE))
import release_tools as maintenance,release_bundle,self_update as update
CURRENT=maintenance.installed_version(BASE);_v=list(map(int,CURRENT.split('.')));NEXT='.'.join(map(str,_v[:2]+[_v[2]+1]))

class Releases(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'new package';self.source.mkdir();self.dest=self.root/'installed tool';self.dest.mkdir()
        for name in maintenance.program_files(BASE):
            target=self.source/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/name,target)
        (self.dest/'bookmarks.py').write_text("VERSION = '3.0.2'\n# original program\n")
        (self.dest/'requirements.txt').write_bytes((BASE/'requirements.txt').read_bytes())
        (self.dest/'settings.json').write_text('{"private":"preserve"}')
        self.make_bundle()
    def make_bundle(self):
        self.archive,self.checksum=release_bundle.build(self.source,self.root/'packages')
        version=maintenance.installed_version(self.source)
        self.info={'tag_name':'v'+version,'draft':False,'prerelease':False,'assets':[{'id':1,'name':self.archive.name,'state':'uploaded','size':self.archive.stat().st_size},{'id':2,'name':self.checksum.name,'state':'uploaded','size':self.checksum.stat().st_size}]}
        self.data={'/releases/latest':json.dumps(self.info).encode(),'/releases/assets/1':self.archive.read_bytes(),'/releases/assets/2':self.checksum.read_bytes()}
        class Client:
            def read(inner,path,limit,asset=False):return self.data[path]
        self.client=Client()
    def perform(self,**kw):
        with patch.object(update,'github_token',return_value='test-token'),patch.object(update,'GitHub',return_value=self.client):
            return update.perform(self.dest,say=lambda msg:None,**kw)
    def environment(self):
        shutil.copytree(BASE/'.venv',self.dest/'.venv',symlinks=True)
        (self.dest/'.venv/.tool-location').write_text(str(self.dest)+'\n')
        (self.dest/'.state/jobs').mkdir(parents=True)
        (self.dest/'.state/jobs/job.json').write_text('{"private":true}')
        (self.dest/'.state/downloaded.sqlite3').write_bytes(b'archive sentinel')
        (self.dest/'media/photo').mkdir(parents=True);(self.dest/'media/photo/saved.jpg').write_bytes(b'media sentinel')
    def test_check_is_read_only_and_without_personal_settings(self):
        (self.dest/'settings.json').unlink();self.assertEqual(self.perform(check=True),0)
        self.assertFalse((self.dest/'.state').exists());self.assertFalse((self.dest/'media').exists())
        (self.dest/'settings.json').write_text('broken');self.assertEqual(self.perform(check=True),0)
    def test_equal_and_older_versions_do_not_install(self):
        for version in (CURRENT,'99.0.0'):
            (self.dest/'bookmarks.py').write_text("VERSION = '"+version+"'")
            with patch.object(update,'install_package') as install:self.assertEqual(self.perform(),0);install.assert_not_called()
        self.assertFalse((self.dest/'.state').exists())
    def test_protocol_installs_verified_package_and_cleans_temporary(self):
        def install(source,root,*args,**kw):shutil.copy2(source/'bookmarks.py',root/'bookmarks.py');return 0
        with patch.object(update,'install_package',side_effect=install):self.assertEqual(self.perform(),0)
        self.assertEqual(maintenance.installed_version(self.dest),CURRENT);self.assertFalse(list((self.dest/'.state').glob('release-*')))
    def test_checksum_corruption_and_wrong_filename_leave_program(self):
        for change in ('data','filename'):
            self.make_bundle()
            if change=='data':self.data['/releases/assets/1']=b'x'*self.info['assets'][0]['size']
            else:self.data['/releases/assets/2']=b'0'*64+b'  wrong.zip'
            with self.assertRaisesRegex(ValueError,'[Cc]hecksum'):self.perform()
            self.assertEqual(maintenance.installed_version(self.dest),'3.0.2');self.assertFalse(list((self.dest/'.state').glob('release-*')))
    def test_draft_prerelease_bad_tag_and_missing_assets(self):
        for change in ({'draft':True},{'prerelease':True},{'tag_name':'v3.1.0-rc1'},{'assets':[]}):
            self.data['/releases/latest']=json.dumps({**self.info,**change}).encode()
            with self.assertRaises(ValueError):update.release_info(self.client)
    def test_http_auth_errors_and_disconnected_network_are_explained(self):
        client=update.GitHub('test-token')
        for error in (401,403,404,500):
            with patch.object(client.opener,'open',side_effect=update.urllib.error.HTTPError('https://api.github.com',error,'test',{},None)):
                with self.assertRaises(ValueError) as result:client.read('/releases/latest',100)
                self.assertNotIn('test-token',str(result.exception))
        with patch.object(client.opener,'open',side_effect=update.urllib.error.URLError('test')):
            with self.assertRaisesRegex(ValueError,'Koneksi'):client.read('/releases/latest',100)
    def test_redirect_does_not_forward_token_to_cdn_or_untrusted_host(self):
        req=update.urllib.request.Request('https://api.github.com/a',headers={'Authorization':'Bearer secret'})
        handler=update.SafeRedirect();new=handler.redirect_request(req,None,302,'',{},'https://release-assets.githubusercontent.com/a')
        self.assertFalse(new.has_header('Authorization'))
        for url in ('http://github.com/a','https://evil.example/a'):
            with self.assertRaises(ValueError):handler.redirect_request(req,None,302,'',{},url)
    def test_token_precedence_and_no_login_means_no_token(self):
        with patch.dict(os.environ,{'GH_TOKEN':'first','GITHUB_TOKEN':'second'}),patch.object(update.subprocess,'run') as run:
            self.assertEqual(update.github_token(),'first');run.assert_not_called()
        with patch.dict(os.environ,{},clear=True),patch.object(update.shutil,'which',return_value=None):
            self.assertIsNone(update.github_token())   # FEAT-004: a public release no longer needs a login
    def test_public_release_is_read_anonymously_and_the_token_is_still_used_when_present(self):
        seen=[]
        class Response:
            def __enter__(inner):return inner
            def __exit__(inner,*exc):return False
            def read(inner,limit):return b'{}'
        for token,expected in ((None,False),('test-token',True)):
            client=update.GitHub(token)
            with patch.object(client.opener,'open',side_effect=lambda request,timeout:(seen.append(request),Response())[1]):client.read('/releases/latest',100)
            self.assertEqual(seen[-1].has_header('Authorization'),expected)
        with patch.object(update,'github_token',return_value=None),patch.object(update,'GitHub',return_value=self.client) as built:
            update.perform(self.dest,check=True,say=lambda msg:None)
        built.assert_called_once_with(None)
    def test_anonymous_errors_point_to_login_and_do_not_blame_a_token(self):
        client=update.GitHub()
        for code,words in ((404,'privat'),(403,'60 permintaan'),(401,'60 permintaan')):
            with patch.object(client.opener,'open',side_effect=update.urllib.error.HTTPError('https://api.github.com',code,'test',{},None)):
                with self.assertRaisesRegex(ValueError,words) as result:client.read('/releases/latest',100)
                self.assertIn('gh auth login',str(result.exception))
    def test_state_cleanup_keeps_recent_and_referenced_state_and_never_touches_media_or_archive(self):
        state=self.dest/'.state'
        for n in (100,200,300,400,500):
            (state/'program-backups'/('v3.1.1-%d'%n)).mkdir(parents=True);(state/'program-backups'/('v3.1.1-%d'%n)/'f').write_bytes(b'x'*n)
        for stamp in ('20260101-235959','20260201-000001','20260301-120000'):(state/('venv-before-move-'+stamp)).mkdir()
        (state/'archive-removed').mkdir()
        for n in (9,10,11):(state/'archive-removed'/('archive-removed-%d.txt'%n)).write_text('twitter_1_1\n')
        (state/'pip-cache').mkdir();(state/'pip-cache/wheel').write_bytes(b'c'*10);(state/'layout-backups/1').mkdir(parents=True)
        (state/'downloaded.sqlite3').write_bytes(b'archive sentinel');(self.dest/'media').mkdir();(self.dest/'media/saved.jpg').write_bytes(b'media sentinel')
        (state/'last-update.json').write_text(json.dumps({'backup':str(state/'program-backups/v3.1.1-100')}))
        before=sorted(str(p) for p in self.dest.rglob('*'))
        rows={row['label']:row for row in maintenance.state_cleanup(self.dest,keep=2)}
        self.assertEqual(sorted(str(p) for p in self.dest.rglob('*')),before)   # a plan alone changes nothing
        self.assertEqual([rows['program-backups'][k] for k in ('removed','kept','bytes')],[2,3,500])   # 500,400 newest + 100 referenced
        self.assertEqual([rows['venv-before-move'][k] for k in ('removed','kept')],[1,2])
        self.assertEqual([rows['archive-removed'][k] for k in ('removed','kept')],[1,2])               # 9 goes, 10 and 11 stay (numeric order)
        self.assertEqual([rows['pip-cache'][k] for k in ('removed','bytes')],[1,10])
        self.assertEqual([rows['layout-backups'][k] for k in ('removed','kept')],[0,1])
        maintenance.state_cleanup(self.dest,keep=2,apply=True)
        names=lambda pattern:sorted(p.name for p in state.glob(pattern))
        self.assertEqual(names('program-backups/*'),['v3.1.1-100','v3.1.1-400','v3.1.1-500'])
        self.assertEqual(names('venv-before-move-*'),['venv-before-move-20260201-000001','venv-before-move-20260301-120000'])
        self.assertEqual(names('archive-removed/*'),['archive-removed-10.txt','archive-removed-11.txt'])
        self.assertFalse((state/'pip-cache').exists());self.assertTrue((state/'layout-backups/1').exists())
        self.assertEqual((state/'downloaded.sqlite3').read_bytes(),b'archive sentinel');self.assertEqual((self.dest/'media/saved.jpg').read_bytes(),b'media sentinel')
        maintenance.state_cleanup(self.dest,keep=0,apply=True)
        self.assertEqual(names('program-backups/*'),['v3.1.1-100'])   # keep 0 still spares the backup rollback needs
    def test_state_cleanup_leaves_backups_alone_while_an_update_is_unfinished_and_skips_symlinks(self):
        state=self.dest/'.state'
        for n in (1,2,3):(state/'program-backups'/('v3.1.1-%d'%n)).mkdir(parents=True)
        (state/'update-transaction.json').write_text(json.dumps({'status':'recovery_required','backup':str(state/'program-backups/v3.1.1-1')}))
        row,*_=maintenance.state_cleanup(self.dest,keep=0,apply=True)
        self.assertEqual((row['removed'],row['kept']),(0,3));self.assertIn('belum pulih',row['note'])
        self.assertEqual(len(list((state/'program-backups').iterdir())),3)
        (state/'update-transaction.json').unlink();outside=self.root/'outside';outside.mkdir();(outside/'keep.txt').write_text('x')
        (state/'program-backups/v3.1.1-999').symlink_to(outside,target_is_directory=True)
        maintenance.state_cleanup(self.dest,keep=0,apply=True)
        self.assertTrue((outside/'keep.txt').exists());self.assertTrue((state/'program-backups/v3.1.1-999').is_symlink())
    def test_invalid_token_is_rejected_without_exposing_credential(self):
        for token in ('secret\ncredential','secret credential','secret\x00credential'):
            with self.assertRaises(ValueError) as result:update.GitHub(token)
            self.assertNotIn('secret',str(result.exception));self.assertNotIn('credential',str(result.exception))
    def test_setup_refuses_python_outside_the_range_declared_in_release_json(self):
        # AUD-006: the range comes from release.json, for a fresh install and for an existing environment alike.
        tool=self.root/'tool';tool.mkdir();shutil.copy2(BASE/'setup.sh',tool/'setup.sh')
        here=sys.version_info[:2];meta=json.loads((BASE/'release.json').read_text(encoding='utf-8'))
        meta['python_min'],meta['python_max']='%d.%d'%(here[0],here[1]+1),'%d.%d'%(here[0],here[1]+2)
        (tool/'release.json').write_text(json.dumps(meta),encoding='utf-8')
        shim=self.root/'shim';shim.mkdir();(shim/'python3').symlink_to(sys.executable)
        env={**os.environ,'PATH':str(shim)+os.pathsep+os.environ['PATH']}
        fresh=subprocess.run(['bash',str(tool/'setup.sh')],env=env,capture_output=True,text=True,timeout=60)
        self.assertEqual(fresh.returncode,1,fresh.stderr)
        self.assertIn('Python %d.%d tidak didukung'%here,fresh.stderr);self.assertIn('%d.%d sampai %d.%d'%(here[0],here[1]+1,here[0],here[1]+2),fresh.stderr)
        self.assertFalse((tool/'.venv').exists())          # refused before a virtualenv was made
        (tool/'.venv/bin').mkdir(parents=True);(tool/'.venv/bin/python').symlink_to(sys.executable);(tool/'.venv/.tool-location').write_text(str(tool)+'\n')
        existing=subprocess.run(['bash',str(tool/'setup.sh'),'--check'],env=env,capture_output=True,text=True,timeout=60)
        self.assertEqual(existing.returncode,1,existing.stderr);self.assertIn('tidak didukung',existing.stderr)
    def test_release_files_are_accepted_by_the_previous_updater(self):
        # An installed copy runs ITS OWN (previous) updater against a new package, so a new kind of file would make `update`
        # fail on every existing install. This is that updater's rule, frozen from 3.1.1. Changing it needs two releases:
        # first teach valid_program_path the new name, then ship the file; and then this rule moves with it.
        def accepted(name):
            parts=PurePosixPath(name).parts
            if len(parts)==1:return name in ('x-bookmarks','.gitignore','gallery-dl.json','settings.example.json','release.json','requirements.txt') or Path(name).suffix in ('.py','.sh','.md','.svg')
            if parts[0]=='tests':return Path(name).suffix in ('.py','.json') and not any(p.startswith('.') for p in parts[1:])
            if parts[0]=='.bootstrap':return len(parts)==2 and (parts[1]=='SHA256SUMS' or re.fullmatch(r'pip-[\d.]+-py3-none-any\.whl',parts[1]) is not None)
            return len(parts)==3 and parts[:2]==('.github','workflows') and Path(name).suffix in ('.yml','.yaml')
        meta=json.loads((BASE/'release.json').read_text(encoding='utf-8'))
        self.assertEqual([name for name in meta['files'] if not accepted(name)],[])
    def test_unsafe_zip_private_files_symlink_and_duplicate_names(self):
        for i,name in enumerate(('x-bookmarks/../escape','/x-bookmarks/app.py','x-bookmarks/settings.json','x-bookmarks/.state/private','x-bookmarks/tests/../../escape','x-bookmarks/a\\b.py','x-bookmarks/.venv/private')):
            archive=self.root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as out:out.writestr(name,b'bad')
            with self.assertRaises(ValueError):update.safe_unpack(archive,self.root/('unpack'+str(i)),CURRENT)
        for kind in ('symlink','duplicate'):
            with zipfile.ZipFile(self.root/'bad.zip','w') as out:
                info=zipfile.ZipInfo('x-bookmarks/bookmarks.py');info.external_attr=(0o120777 if kind=='symlink' else 0o100644)<<16
                out.writestr(info,b'bad')
                if kind=='duplicate':out.writestr('x-bookmarks/BOOKMARKS.PY',b'bad')
            with self.assertRaises(ValueError):update.safe_unpack(self.root/'bad.zip',self.root/kind,CURRENT)
    def test_version_platform_python_repository_and_missing_file_validation(self):
        meta=json.loads((self.source/'release.json').read_text())
        for change in ({'version':'3.2.0'},{'platforms':['windows-x86_64']},{'python_min':'9.0','python_max':'9.1'},{'repository':'other/repo'},{'files':[n for n in meta['files'] if n!='self_update.py']}):
            (self.source/'release.json').write_text(json.dumps({**meta,**change}))
            with self.assertRaises(ValueError):maintenance.validate_package(self.source)
        (self.source/'release.json').write_text(json.dumps(meta));(self.source/'self_update.py').unlink()
        with self.assertRaises(ValueError):maintenance.validate_package(self.source)
    def test_archive_limits_and_manifest_extra_file_rejected(self):
        with zipfile.ZipFile(self.root/'large.zip','w',zipfile.ZIP_DEFLATED) as out:out.writestr('x-bookmarks/huge.py',b'0'*(20*1024*1024+1))
        with self.assertRaisesRegex(ValueError,'ukuran'):update.safe_unpack(self.root/'large.zip',self.root/'large',CURRENT)
        with zipfile.ZipFile(self.archive,'a') as out:out.writestr('x-bookmarks/unlisted.py',b'extra')
        with self.assertRaisesRegex(ValueError,'daftar file'):update.safe_unpack(self.archive,self.root/'extra',CURRENT)
    def test_lock_busy_and_wait_for_other_process(self):
        with maintenance.file_lock(self.dest/'.state/run.lock'):
            with self.assertRaisesRegex(ValueError,'berjalan'):self.perform()
        script='from pathlib import Path;from release_tools import file_lock;import sys,time\nwith file_lock(Path(sys.argv[1])):\n print("READY",flush=True);time.sleep(.7)'
        with subprocess.Popen([sys.executable,'-c',script,str(self.dest/'.state/run.lock')],cwd=BASE,stdout=subprocess.PIPE,text=True) as holder:
            self.assertEqual(holder.stdout.readline().strip(),'READY');started=time.monotonic()
            def install(source,root,*args,**kw):shutil.copy2(source/'bookmarks.py',root/'bookmarks.py');return 0
            with patch.object(update,'install_package',side_effect=install):self.assertEqual(self.perform(wait=True),0)
            self.assertGreater(time.monotonic()-started,.5);holder.wait()
    def test_full_self_update_to_newer_version_and_real_rollback(self):
        self.environment()
        text=(self.source/'bookmarks.py').read_text().replace("VERSION = '"+CURRENT+"'","VERSION = '"+NEXT+"'");(self.source/'bookmarks.py').write_text(text)
        meta=json.loads((self.source/'release.json').read_text());meta['version']=NEXT;(self.source/'release.json').write_text(json.dumps(meta));self.make_bundle()
        self.assertEqual(self.perform(),0);self.assertEqual(maintenance.installed_version(self.dest),NEXT)
        maintenance.rollback(self.dest);self.assertEqual(maintenance.installed_version(self.dest),'3.0.2');self.assertFalse((self.dest/'self_update.py').exists())
        self.assertEqual((self.dest/'settings.json').read_text(),'{"private":"preserve"}')
        self.assertEqual((self.dest/'.state/downloaded.sqlite3').read_bytes(),b'archive sentinel');self.assertEqual((self.dest/'media/photo/saved.jpg').read_bytes(),b'media sentinel')
        with self.assertRaises(ValueError):maintenance.rollback(self.dest)
    def test_failure_mid_program_copy_removes_added_and_partial_files(self):
        self.environment();copy=maintenance.atomic_copy
        def fail(src,dest):
            if Path(src).parent==self.source and Path(src).name=='media_layout.py':
                Path(str(dest)+'.new').write_text('partial');raise OSError('fixture failure')
            copy(src,dest)
        with patch.object(maintenance,'atomic_copy',side_effect=fail):
            with self.assertRaisesRegex(ValueError,'dipulihkan'):maintenance.install(self.source,self.dest)
        self.assertEqual(maintenance.installed_version(self.dest),'3.0.2');self.assertFalse((self.dest/'self_update.py').exists());self.assertFalse(list(self.dest.rglob('*.new')))
        self.assertEqual(json.loads((self.dest/'.state/update-transaction.json').read_text())['status'],'restored')
    def test_dependency_mutation_failure_restores_exact_environment(self):
        self.environment();(self.dest/'.venv/sentinel').write_text('old environment')
        def fail(root,args):
            (root/'.venv/sentinel').write_text('mutated environment');raise ValueError('pip failure')
        with patch.object(maintenance,'dependencies_ready',return_value=False),patch.object(maintenance,'logged',side_effect=fail):
            with self.assertRaisesRegex(ValueError,'dipulihkan'):maintenance.install(self.source,self.dest)
        self.assertEqual((self.dest/'.venv/sentinel').read_text(),'old environment');self.assertEqual(maintenance.installed_version(self.dest),'3.0.2')
    def test_recovery_required_is_not_success_and_can_be_recovered(self):
        self.environment()
        with patch.object(maintenance,'logged',side_effect=ValueError('install failure')),patch.object(maintenance,'restore',side_effect=OSError('restore failure')):
            with self.assertRaisesRegex(ValueError,'Pemulihan belum lengkap'):maintenance.install(self.source,self.dest)
        self.assertEqual(json.loads((self.dest/'.state/update-transaction.json').read_text())['status'],'recovery_required')
        with self.assertRaisesRegex(ValueError,'belum pulih'):maintenance.install(self.source,self.dest)
        maintenance.recover(self.dest);self.assertEqual(maintenance.installed_version(self.dest),'3.0.2')
    def test_installer_reacquires_lock_before_any_mutation(self):
        self.environment()
        with maintenance.file_lock(self.dest/'.state/run.lock'):
            with self.assertRaisesRegex(ValueError,'tidak berhasil'):update.install_package(self.source,self.dest,say=lambda msg:None)
        self.assertEqual(maintenance.installed_version(self.dest),'3.0.2');self.assertFalse((self.dest/'.state/program-backups').exists())
    def test_inherited_lock_must_match_target_and_stays_held_by_parent(self):
        path=self.dest/'.state/run.lock'
        with maintenance.file_lock(path) as parent:
            with maintenance.file_lock(path,fd=parent.fileno()):pass
            with self.assertRaises(ValueError):
                with maintenance.file_lock(path):pass
            wrong=self.dest/'.state/wrong.lock';wrong.write_text('wrong')
            with wrong.open('a+b') as stream:
                with self.assertRaisesRegex(ValueError,'tidak cocok'):
                    with maintenance.file_lock(path,fd=stream.fileno()):pass
    def test_manual_update_defers_ctrl_c_and_returns_130(self):
        self.environment()
        script=(self.source/'release_tools.py').read_text()
        replacement="""original_install=install
def install(source,root):
    (root/'.state/started').write_text('started')
    time.sleep(.7)
    original_install(source,root)
if __name__=='__main__':sys.exit(main())"""
        tail="if __name__ == '__main__':\n    sys.exit(main())";self.assertIn(tail,script)  # formatted entry point; a silent no-op would hide this test
        (self.source/'release_tools.py').write_text(script.replace(tail,replacement))
        with subprocess.Popen(['bash',str(self.source/'update.sh'),str(self.dest)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as p:
            deadline=time.monotonic()+10
            while not(self.dest/'.state/started').exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue((self.dest/'.state/started').exists());p.send_signal(signal.SIGINT);out,err=p.communicate(timeout=30)
            self.assertEqual(p.returncode,130,out+err)
        self.assertEqual(maintenance.installed_version(self.dest),CURRENT)
    def test_ctrl_c_before_install_wait_and_during_protected_install(self):
        self.environment()
        # Use a local installer stage marker so SIGINT is delivered during installation.
        code="import time,sys\nfrom pathlib import Path\np=Path(sys.argv[2]);(p/'.state/started').write_text('yes');time.sleep(1);(p/'.state/finished').write_text('yes')\n"
        (self.source/'release_tools.py').write_text(code)
        script='from pathlib import Path;from self_update import install_package;import sys;sys.exit(install_package(Path(sys.argv[1]),Path(sys.argv[2])))'
        with subprocess.Popen([sys.executable,'-c',script,str(self.source),str(self.dest)],cwd=BASE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as p:
            deadline=time.monotonic()+10
            while not(self.dest/'.state/started').exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue((self.dest/'.state/started').exists());p.send_signal(signal.SIGINT);out,err=p.communicate(timeout=10)
            self.assertEqual(p.returncode,130,out+err);self.assertTrue((self.dest/'.state/finished').exists())
        script='from release_tools import file_lock;from pathlib import Path;import sys\nwith file_lock(Path(sys.argv[1]),True):print("unexpected")'
        with maintenance.file_lock(self.dest/'.state/run.lock'),subprocess.Popen([sys.executable,'-c',script,str(self.dest/'.state/run.lock')],cwd=BASE,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as p:
            time.sleep(.15);p.send_signal(signal.SIGINT);p.communicate(timeout=5);self.assertNotEqual(p.returncode,0)
    def test_cli_update_bypasses_broken_settings_and_rejects_unrelated_flags(self):
        (self.source/'settings.json').write_text('broken settings')
        script='import sys,types,runpy;stub=types.ModuleType("self_update");stub.perform=lambda *args:print("CHECK_ONLY") or 0;sys.modules["self_update"]=stub;sys.argv=["bookmarks.py","update","--check","--plain"];runpy.run_path("bookmarks.py",run_name="__main__")'
        r=subprocess.run([sys.executable,'-c',script],cwd=self.source,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);self.assertIn('CHECK_ONLY',r.stdout);self.assertFalse((self.source/'.state').exists())
        for args in (('status','--check'),('scan','--wait'),('update','--start','2026-08-31'),('update','--limit','0')):
            r=subprocess.run([sys.executable,'bookmarks.py',*args],cwd=self.source,capture_output=True);self.assertEqual(r.returncode,2,r.stderr)
    def test_stdlib_fallback_imports_no_rich_or_gallery(self):
        script='import sys;import self_update;assert "rich" not in sys.modules;assert "gallery_dl" not in sys.modules;print("stdlib ready")'
        r=subprocess.run([sys.executable,'-S','-c',script],cwd=self.source,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
    def test_program_only_bundle_and_executable_launcher(self):
        (self.source/'settings.json').write_text('private');(self.source/'.state').mkdir();(self.source/'.state/cookies').write_text('secret')
        self.make_bundle()
        with zipfile.ZipFile(self.archive) as src:
            self.assertNotIn('x-bookmarks/settings.json',src.namelist());self.assertFalse(any('/.state/' in n for n in src.namelist()))
            self.assertTrue(src.getinfo('x-bookmarks/x-bookmarks').external_attr>>16&0o111)
    def test_legacy_v2_rollback_keeps_subfolder_helper(self):
        self.environment();(self.dest/'bookmarks.py').write_text("VERSION = '2.0'\nfiles = MEDIA.iterdir()\n")
        maintenance.install(self.source,self.dest);maintenance.rollback(self.dest)
        self.assertIn('media_files(MEDIA)',(self.dest/'bookmarks.py').read_text());self.assertTrue((self.dest/'media_layout.py').exists())
    def test_setup_seed_preserves_settings_and_migration_rebuilds_env(self):
        # Real setup using bundled pip; migration checks launcher, new location and data.
        clean=self.root/'clean';shutil.copytree(self.source,clean)
        r=subprocess.run(['bash',str(clean/'setup.sh')],capture_output=True,text=True,timeout=180);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads((clean/'settings.json').read_text())['browser'],'firefox')
        settings=(clean/'settings.json').read_bytes()
        r=subprocess.run(['bash',str(clean/'setup.sh'),'--check'],capture_output=True,text=True,timeout=60);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual((clean/'settings.json').read_bytes(),settings)
        maintenance.install(self.source,clean)
        moved=self.root/'moved tool'
        r=subprocess.run(['bash',str(clean/'migrate.sh'),str(moved)],capture_output=True,text=True,timeout=180);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        for name in ('last-update.json','update-transaction.json'):
            self.assertTrue(Path(json.loads((moved/'.state'/name).read_text())['backup']).is_relative_to(moved))
        self.assertEqual((moved/'settings.json').read_bytes(),settings);self.assertEqual((moved/'.venv/.tool-location').read_text().strip(),str(moved))

if __name__=='__main__':unittest.main()

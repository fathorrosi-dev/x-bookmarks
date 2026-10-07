"""Linux release transactions, also usable without Rich or gallery-dl."""
import argparse,contextlib,fcntl,json,os,platform,re,shutil,signal,subprocess,sys,time
from pathlib import Path,PurePosixPath

BASE=Path(__file__).resolve().parent
REPOSITORY='fathorrosi-dev/x-bookmarks'
LEGACY_FILES=['bookmarks.py','gallery_worker.py','cli_ui.py','media_layout.py','gallery-dl.json','requirements.txt','setup.sh','migrate.sh','x-bookmarks','update.sh','rollback.sh','PANDUAN.md','MIGRASI.md','tests/test_tools.py','tests/worker_fixture.py','tests/demo.py']

def platform_key():return platform.system().lower()+'-'+platform.machine().lower()

def installed_version(root):
    match=re.search(r"VERSION\s*=\s*['\"]([^'\"]+)",(Path(root)/'bookmarks.py').read_text(encoding='utf-8'))
    if not match:raise ValueError('Versi program tidak terbaca')
    return match[1]

def write_json(path,data):
    path=Path(path);temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)

def valid_program_path(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name:return False
    parts=PurePosixPath(name).parts
    if name.startswith('/') or any(p in ('','.','..') for p in name.split('/')):return False
    if any(p in ('.state','.venv','media','archive','.git','__pycache__') for p in parts):return False
    if len(parts)==1:
        return name in ('x-bookmarks','.gitignore','gallery-dl.json','settings.example.json','release.json','requirements.txt') or Path(name).suffix in ('.py','.sh','.md','.svg')
    if parts[0]=='tests':return Path(name).suffix in ('.py','.json') and not any(p.startswith('.') for p in parts[1:])
    if parts[0]=='.bootstrap':return len(parts)==2 and (parts[1]=='SHA256SUMS' or re.fullmatch(r'pip-[\d.]+-py3-none-any\.whl',parts[1]) is not None)
    return len(parts)==3 and parts[:2]==('.github','workflows') and Path(name).suffix in ('.yml','.yaml')

def validate_package(root,version=None):
    root=Path(root);meta=json.loads((root/'release.json').read_text(encoding='utf-8'))
    actual=installed_version(root)
    if not re.fullmatch(r'\d+\.\d+\.\d+',actual) or meta.get('version')!=actual or (version and actual!=version):raise ValueError('Versi paket tidak cocok dengan tag/program')
    if meta.get('repository')!=REPOSITORY:raise ValueError('Identitas repo paket tidak cocok')
    if platform_key() not in meta.get('platforms',[]):raise ValueError('Rilis hanya mendukung Linux x64')
    try:low=tuple(map(int,meta['python_min'].split('.')));high=tuple(map(int,meta['python_max'].split('.')))
    except (KeyError,AttributeError,ValueError):raise ValueError('Metadata Python tidak valid') from None
    if not low<=sys.version_info[:2]<=high:raise ValueError('Gunakan Python standar 3.10–3.14')
    names=meta.get('files')
    if not isinstance(names,list) or not names or not all(valid_program_path(n) for n in names):raise ValueError('Daftar file program tidak valid')
    if len({n.casefold() for n in names})!=len(names):raise ValueError('File program duplikat')
    required={'bookmarks.py','cli_ui.py','gallery_worker.py','media_layout.py','gallery-dl.json','requirements.txt','release.json','release_tools.py','self_update.py','settings.example.json','x-bookmarks','setup.sh','update.sh','rollback.sh'}
    if not required.issubset(names):raise ValueError('Paket rilis tidak lengkap')
    if any(not(root/n).is_file() or (root/n).is_symlink() for n in names):raise ValueError('File rilis hilang atau berupa tautan')
    return meta

@contextlib.contextmanager
def file_lock(path,wait=False,fd=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if fd is not None:
        identity=os.fstat(fd);expected=path.stat()
        if (identity.st_dev,identity.st_ino)!=(expected.st_dev,expected.st_ino):raise ValueError('Kunci installer tidak cocok dengan alat tujuan')
        # Same open-file description as the parent's lock; do not unlock it here.
        with os.fdopen(os.dup(fd),'a+b') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield stream
        return
    with path.open('a+b') as stream:
        while True:
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if not wait:raise ValueError('Alat masih berjalan; tunggu selesai atau gunakan --wait.') from None
                time.sleep(.1)
        try:yield stream
        finally:fcntl.flock(stream,fcntl.LOCK_UN)

class ProcessTree:
    def __init__(self,process):self.process=process
    def stop(self):
        p=self.process
        if p.poll() is None:
            try:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        try:os.killpg(p.pid,signal.SIGKILL)
        except (ProcessLookupError,PermissionError):pass
        p.wait(timeout=3)
    def close(self):pass

def worker_start():
    if sys.stdin.readline().strip()!='START':raise ValueError('Installer tidak mendapat izin mulai')

def program_files(root):
    root=Path(root);path=root/'release.json'
    return json.loads(path.read_text(encoding='utf-8'))['files'] if path.exists() else [n for n in LEGACY_FILES if(root/n).is_file()]

def logged(root,args):
    with (Path(root)/'.state/update.log').open('a',encoding='utf-8') as log:
        result=subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,preexec_fn=lambda:signal.signal(signal.SIGINT,signal.SIG_IGN),env={**os.environ,'PIP_CACHE_DIR':str(Path(root)/'.state/pip-cache'),'PYTHONUTF8':'1'})
    if result.returncode:raise ValueError('Perintah pemeliharaan gagal; periksa .state/update.log')

def dependency_versions(root):
    py=Path(root)/'.venv/bin/python'
    r=subprocess.run([str(py),'-m','pip','list','--format=json'],capture_output=True,text=True,encoding='utf-8')
    if r.returncode:raise ValueError('Daftar dependensi tidak dapat dibaca')
    return json.loads(r.stdout)

def dependencies_ready(root,requirements):
    lines=[x.strip() for x in Path(requirements).read_text().splitlines() if x.strip() and not x.startswith('#')]
    script='import sys,json;from importlib.metadata import version;rs=json.loads(sys.argv[1]);assert all(version(x.split("==")[0])==x.split("==")[1] for x in rs)'
    return subprocess.run([str(Path(root)/'.venv/bin/python'),'-c',script,json.dumps(lines)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0

def atomic_copy(source,target):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+'.new');shutil.copy2(source,temp);temp.replace(target)

def compatible_v2(backup):
    path=backup/'bookmarks.py';text=path.read_text(encoding='utf-8')
    if "VERSION = '2.0'" in text:
        text=text.replace("VERSION = '2.0'","VERSION = '2.0-rollback'\nfrom media_layout import media_files")
        text=text.replace('media.iterdir()','media_files(media)').replace('MEDIA.iterdir()','media_files(MEDIA)')
        (backup/'bookmarks-compatible.py').write_text(text,encoding='utf-8');return True
    return False

def restore(root,backup):
    root=Path(root);backup=Path(backup).resolve()
    if not backup.is_relative_to((root/'.state/program-backups').resolve()):raise ValueError('Lokasi cadangan tidak valid')
    meta_path=backup/'manifest.json'
    meta=json.loads(meta_path.read_text()) if meta_path.exists() else {'files':(backup/'files.txt').read_text().splitlines(),'added':[],'removed':[],'env_saved':False}
    if not all(valid_program_path(n) for field in ('added','removed','keep') for n in meta.get(field,[])):raise ValueError('Daftar pemulihan tidak aman')
    if not all(valid_program_path(n) and (backup/n).is_file() for n in meta['files']):raise ValueError('Cadangan program tidak lengkap')
    if meta.get('env_saved') and not (backup/'environment/bin/python').exists():raise ValueError('Cadangan dependensi tidak lengkap')
    for name in meta['files']:
        source=backup/'bookmarks-compatible.py' if name=='bookmarks.py' and (backup/'bookmarks-compatible.py').exists() else backup/name
        atomic_copy(source,root/name)
    for name in meta.get('added',[]):
        if name not in meta.get('keep',[]):(root/name).unlink(missing_ok=True)
    for name in set(meta['files']+meta.get('added',[])+meta.get('removed',[])):
        (root/(name+'.new')).unlink(missing_ok=True)
    if meta.get('env_saved'):
        stage=root/'.state'/('environment-restore-'+str(time.time_ns()))
        shutil.copytree(backup/'environment',stage,symlinks=True)
        previous=root/'.state'/('environment-failed-'+str(time.time_ns()))
        (root/'.venv').rename(previous)
        try:stage.rename(root/'.venv')
        except BaseException:previous.rename(root/'.venv');raise
        shutil.rmtree(previous)

def install(source,root):
    source=Path(source).resolve();root=Path(root).resolve();meta=validate_package(source)
    if source==root or not(root/'bookmarks.py').exists() or not(root/'.venv/bin/python').exists():raise ValueError('Pilih alat terpasang dan paket baru yang terpisah')
    txn=root/'.state/update-transaction.json'
    if txn.exists() and json.loads(txn.read_text()).get('status') in ('installing','restoring','recovery_required'):
        raise ValueError('Pembaruan sebelumnya belum pulih. Jalankan python3 release_tools.py recover dari paket rilis dengan folder tujuan.')
    previous=program_files(root);names=meta['files'];all_names=sorted(set(previous+names))
    if not all(valid_program_path(n) for n in all_names):raise ValueError('Daftar program terpasang tidak aman')
    backup=root/'.state/program-backups'/('v'+installed_version(root)+'-'+str(time.time_ns()));backup.mkdir(parents=True)
    existing=[n for n in all_names if(root/n).is_file()];added=[n for n in names if n not in existing]
    env_change=not dependencies_ready(root,source/'requirements.txt') or (root/'requirements.txt').read_bytes()!=(source/'requirements.txt').read_bytes()
    for name in existing:atomic_copy(root/name,backup/name)
    write_json(backup/'dependencies.json',dependency_versions(root))
    keep=['media_layout.py'] if compatible_v2(backup) else []
    record={'files':existing,'added':added,'removed':sorted(set(previous)-set(names)),'env_saved':env_change,'keep':keep,'previous_version':installed_version(root)}
    # Prepare a complete environment snapshot before pip can change any package.
    if env_change:shutil.copytree(root/'.venv',backup/'environment',symlinks=True)
    write_json(backup/'manifest.json',record)
    (backup/'files.txt').write_text('\n'.join(existing)+'\n')
    write_json(txn,{'status':'installing','backup':str(backup)})
    try:
        if env_change:logged(root,[root/'.venv/bin/python','-m','pip','install','-r',source/'requirements.txt'])
        logged(root,[root/'.venv/bin/python','-m','pip','check'])
        if not dependencies_ready(root,source/'requirements.txt'):raise ValueError('Dependensi tidak cocok dengan rilis')
        for name in names:atomic_copy(source/name,root/name)
        for name in record['removed']:
            if name not in keep:(root/name).unlink(missing_ok=True)
        if installed_version(root)!=meta['version']:raise ValueError('Versi setelah pemasangan tidak cocok')
        write_json(root/'.state/last-update.json',{'backup':str(backup),'source_version':meta['version'],'installed_at':time.time()})
        write_json(txn,{'status':'complete','backup':str(backup)})
    except BaseException as original:
        try:
            write_json(txn,{'status':'restoring','backup':str(backup)});restore(root,backup)
            write_json(txn,{'status':'restored','backup':str(backup)})
        except BaseException:
            write_json(txn,{'status':'recovery_required','backup':str(backup)})
            raise ValueError('Pemulihan belum lengkap. Gunakan python3 release_tools.py recover '+str(root)+' dari paket rilis. Backup: '+str(backup)) from original
        raise ValueError('Pemasangan gagal; program dan dependensi sebelumnya dipulihkan. Backup: '+str(backup)) from original
    print('Versi '+meta['version']+' terpasang. Cadangan: '+str(backup),flush=True)

def rollback(root):
    root=Path(root);path=root/'.state/last-update.json';record=json.loads(path.read_text())
    if record.get('rolled_back_at'):raise ValueError('Cadangan terakhir sudah dipulihkan')
    restore(root,record['backup']);record['rolled_back_at']=time.time();write_json(path,record)
    write_json(root/'.state/update-transaction.json',{'status':'restored','backup':record['backup']})
    print('Program sebelumnya dipulihkan; settings, snapshot, arsip, dan media dipertahankan.')

def recover(root):
    root=Path(root);path=root/'.state/update-transaction.json';record=json.loads(path.read_text())
    if record['status'] not in ('installing','restoring','recovery_required'):raise ValueError('Tidak ada transaksi yang perlu dipulihkan')
    restore(root,record['backup']);record['status']='restored';write_json(path,record)
    print('Pemulihan selesai. Jalankan bash setup.sh --check.')

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('action',choices=['install','rollback','recover']);p.add_argument('dest',nargs='?');p.add_argument('--wait',action='store_true');p.add_argument('--lock-fd',type=int,help=argparse.SUPPRESS);args=p.parse_args()
    root=Path(args.dest).expanduser().resolve() if args.dest else BASE
    interrupted=[]
    try:
        if args.action=='install' and not args.dest:raise ValueError('Folder tujuan diperlukan')
        if args.wait:print('Menunggu kunci alat bebas',flush=True)
        with file_lock(root/'.state/run.lock',args.wait,args.lock_fd):
            if args.action=='install':
                previous=signal.signal(signal.SIGINT,lambda *unused:interrupted.append(True))
                try:install(BASE,root)
                finally:signal.signal(signal.SIGINT,previous)
                if interrupted:print('Pemasangan diselesaikan sebelum penghentian.');return 130
            elif args.action=='rollback':rollback(root)
            else:recover(root)
        return 0
    except KeyboardInterrupt:print('Dihentikan',file=sys.stderr);return 130
    except Exception as exc:print('! GAGAL · '+str(exc),file=sys.stderr);return 130 if interrupted else 1

if __name__=='__main__':sys.exit(main())

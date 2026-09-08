import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import time

archive, run_arg, commit, tree = sys.argv[1:]
run = Path(run_arg).resolve()
if run.exists():
    raise SystemExit('Refuse to overwrite existing run directory')
if 'cockpit-soak' in str(run).lower():
    raise SystemExit('Refuse soak path')
run.mkdir(parents=True)
source = run / 'source'
source.mkdir()
manifest = {}
with tarfile.open(archive, 'r:*') as bundle:
    for member in bundle.getmembers():
        parts = Path(member.name).parts
        if Path(member.name).anchor or member.name.startswith('/') or '..' in parts or member.issym() or member.islnk():
            raise SystemExit('Unsafe archive member: ' + member.name)
        target = (source / member.name).resolve()
        if not target.is_relative_to(source):
            raise SystemExit('Archive member escapes source: ' + member.name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            data = bundle.extractfile(member).read()
            target.write_bytes(data)
            manifest[member.name] = hashlib.sha256(data).hexdigest()
        else:
            raise SystemExit('Unsupported archive member: ' + member.name)
(run / 'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
metadata = dict(commit=commit, tree=tree, archive_sha256=hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
                source_file_count=len(manifest), python=sys.version, executable=sys.executable,
                platform=platform.platform(), started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()), checks=[])
env = {k:v for k,v in os.environ.items() if not k.startswith('COCKPIT_') and k not in ('PYTHONPATH','PYTEST_ADDOPTS')}
tmp = run / 'tmp'
tmp.mkdir()
env.update(TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp), COCKPIT_ENV_FILE=str(run/'absent.env'), PYTHONUTF8='1')
if os.name != 'nt':
    env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
commands = [('version',[sys.executable,'-VV']),('dependencies',[sys.executable,'-m','pip','freeze']),
            ('pip-check',[sys.executable,'-m','pip','check']),('ruff',[sys.executable,'-m','ruff','check','.']),
            ('pytest',[sys.executable,'-m','pytest','-q','-ra','--junitxml='+str(run/'pytest.xml')])]
for name,args in commands:
    started=time.monotonic()
    with (run/(name+'.log')).open('w',encoding='utf-8') as log:
        result=subprocess.run(args,cwd=source/'app',env=env,stdout=log,stderr=subprocess.STDOUT)
    metadata['checks'].append(dict(name=name,argv=args,returncode=result.returncode,duration_seconds=round(time.monotonic()-started,2)))
    (run/'attestation.json').write_text(json.dumps(metadata,sort_keys=True,indent=2),encoding='utf-8')
    print(name, result.returncode, metadata['checks'][-1]['duration_seconds'],flush=True)
changed=[name for name,digest in manifest.items() if not (source/name).exists() or hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest]
metadata['tracked_source_changed']=changed
metadata['completed_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
metadata['artifacts_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(run.iterdir()) if p.is_file() and p.name!='attestation.json'}
(run/'attestation.json').write_text(json.dumps(metadata,sort_keys=True,indent=2),encoding='utf-8')
print(json.dumps(metadata,sort_keys=True),flush=True)
raise SystemExit(int(bool(changed or any(c['returncode'] for c in metadata['checks']))))

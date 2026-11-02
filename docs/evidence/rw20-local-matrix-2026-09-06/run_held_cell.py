import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import time

archive, base, output = map(Path, sys.argv[1:])
base, output = base.resolve(), output.resolve()
if output.exists():
    raise SystemExit('Refuse existing held-excerpt run')
output.mkdir(parents=True)
source = base / 'source'
original = json.loads((base/'source-manifest.json').read_text(encoding='utf-8'))
base_record = json.loads((base/'attestation.json').read_text(encoding='utf-8'))
if base_record['commit']!='69a1c9c53c0beeb0f91310ee017b24ba21a592b7' or base_record['tracked_source_changed']:
    raise SystemExit('Unexpected or changed base source')
def changes(manifest):
    return [name for name,digest in manifest.items() if not (source/name).exists() or hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest]
if changes(original):
    raise SystemExit('Original snapshot differs before supplement')
overlay = {}
with tarfile.open(archive) as bundle:
    for member in bundle.getmembers():
        target = (source / member.name).resolve()
        if not target.is_relative_to(source) or member.issym() or member.islnk():
            raise SystemExit('Unsafe archive')
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            if not member.name.startswith('docs/evidence/rw21-held-excerpts/') or target.exists():
                raise SystemExit('Refuse overwrite or out-of-scope overlay')
            target.parent.mkdir(parents=True, exist_ok=True)
            data = bundle.extractfile(member).read()
            target.write_bytes(data)
            overlay[member.name] = hashlib.sha256(data).hexdigest()
        else:
            raise SystemExit('Unsupported archive member')
record = dict(commit='8fc9c936c6a6c2c25d344a8425ffc5a48c01e879', base_commit=base_record['commit'],
              app_tree='5125e7edd2489c027364bbbfbd4556d928109817',
              archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
              original_source_files=len(original), supplemental_source=overlay,
              python=sys.version, executable=sys.executable, platform=platform.platform(), checks=[])
env = {k:v for k,v in os.environ.items() if not k.startswith('COCKPIT_') and k not in ('PYTHONPATH','PYTEST_ADDOPTS')}
if os.name != 'nt':
    env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
commands = [('unittest',[sys.executable,'docs/evidence/rw21-held-excerpts/test_existing_excerpts.py','-v']),
            ('ruff',[sys.executable,'-m','ruff','check','docs/evidence/rw21-held-excerpts'])]
for name,args in commands:
    started=time.monotonic()
    with (output/(name+'.log')).open('w',encoding='utf-8') as log:
        result=subprocess.run(args,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    record['checks'].append(dict(name=name,argv=args,returncode=result.returncode,duration_seconds=round(time.monotonic()-started,2)))
    print(name,result.returncode,flush=True)
record['original_source_changed']=changes(original)
record['supplemental_source_changed']=changes(overlay)
record['artifacts_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.log')}
(output/'attestation.json').write_text(json.dumps(record,indent=2,sort_keys=True),encoding='utf-8')
print(json.dumps(record,sort_keys=True),flush=True)
raise SystemExit(int(bool(record['original_source_changed'] or record['supplemental_source_changed'] or any(c['returncode'] for c in record['checks']))))

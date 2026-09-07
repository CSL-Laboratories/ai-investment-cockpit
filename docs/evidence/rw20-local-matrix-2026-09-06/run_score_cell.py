import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import time

archive, output = map(Path, sys.argv[1:])
output = output.resolve()
if output.exists():
    raise SystemExit('Refuse existing score run')
output.mkdir(parents=True)
source = output / 'source'
source.mkdir()
manifest = {}
with tarfile.open(archive) as bundle:
    for member in bundle.getmembers():
        target = (source / member.name).resolve()
        if not target.is_relative_to(source) or member.issym() or member.islnk():
            raise SystemExit('Unsafe archive')
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            data = bundle.extractfile(member).read()
            target.write_bytes(data)
            manifest[member.name] = hashlib.sha256(data).hexdigest()
        else:
            raise SystemExit('Unsupported archive member')
record = dict(commit='0149b86a8e0ce6d971e6c6f69bd31d08bdb5ad8c',
              app_tree='5125e7edd2489c027364bbbfbd4556d928109817',
              archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
              source=manifest, python=sys.version, executable=sys.executable,
              platform=platform.platform(), checks=[])
env = {k:v for k,v in os.environ.items() if not k.startswith('COCKPIT_') and k not in ('PYTHONPATH','PYTEST_ADDOPTS')}
if os.name != 'nt':
    env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
commands = [('unittest',[sys.executable,'-m','unittest','discover','-s','tools/tests','-p','test_research_value_score.py']),
            ('ruff',[sys.executable,'-m','ruff','check','tools/research_value_score.py','tools/tests/test_research_value_score.py']),
            ('unmeasured',[sys.executable,'tools/research_value_score.py'])]
for name,args in commands:
    started=time.monotonic()
    with (output/(name+'.log')).open('w',encoding='utf-8') as log:
        result=subprocess.run(args,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    record['checks'].append(dict(name=name,argv=args,returncode=result.returncode,duration_seconds=round(time.monotonic()-started,2)))
    print(name,result.returncode,flush=True)
try:
    empty=json.loads((output/'unmeasured.log').read_text(encoding='utf-8'))
    record['empty_input_refuses_measurement'] = (empty['authority']=='research_only_no_gate_change' and all(empty[x]['status']=='unmeasured' for x in ('extraction','time','strategy')) and empty['input_sha256']=={})
except (ValueError,KeyError,TypeError):
    record['empty_input_refuses_measurement']=False
record['source_changed']=[name for name,digest in manifest.items() if not (source/name).exists() or hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest]
record['artifacts_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.log')}
(output/'attestation.json').write_text(json.dumps(record,indent=2,sort_keys=True),encoding='utf-8')
print(json.dumps(record,sort_keys=True),flush=True)
raise SystemExit(int(bool(record['source_changed'] or not record['empty_input_refuses_measurement'] or any(c['returncode'] for c in record['checks']))))

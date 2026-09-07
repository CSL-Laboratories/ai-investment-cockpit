import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = Path(sys.argv[1]).resolve()
main_cells = ('w311','w312','l311','l312')
score_cells = ('s311w','s312w','s311l','s312l')
held_cells = ('h311w','h312w','h311l','h312l')
summary = dict(full_app_commit='69a1c9c53c0beeb0f91310ee017b24ba21a592b7', app_tree='5125e7edd2489c027364bbbfbd4556d928109817',
               research_tool_commit='0149b86a8e0ce6d971e6c6f69bd31d08bdb5ad8c', held_excerpt_commit='8fc9c936c6a6c2c25d344a8425ffc5a48c01e879',
               claim='local compatibility only; no selection, order, witness or deployment authority', cells=[], artifacts_sha256={})
reference_manifest = json.loads((root/'w311'/'source-manifest.json').read_text(encoding='utf-8'))
for name,score_name,held_name in zip(main_cells,score_cells,held_cells):
    directory = root/name
    record=json.loads((directory/'attestation.json').read_text(encoding='utf-8'))
    score=json.loads((root/score_name/'attestation.json').read_text(encoding='utf-8'))
    held=json.loads((root/held_name/'attestation.json').read_text(encoding='utf-8'))
    suite=ET.parse(directory/'pytest.xml').getroot().find('testsuite')
    counts={key:int(suite.attrib[key]) for key in ('tests','failures','errors','skipped')}
    counts['passed']=counts['tests']-counts['failures']-counts['errors']-counts['skipped']
    skips=[dict(test=case.attrib.get('classname','')+'.'+case.attrib['name'],reason=case.find('skipped').attrib.get('message','')) for case in suite.findall('testcase') if case.find('skipped') is not None]
    valid=(all(c['returncode']==0 for c in record['checks']+score['checks']+held['checks']) and not record['tracked_source_changed'] and not score['source_changed'] and not held['original_source_changed'] and not held['supplemental_source_changed'] and score['empty_input_refuses_measurement'] and counts['failures']==counts['errors']==0)
    expected_minor = '3.11.' if name.endswith('311') else '3.12.'
    expected_platform = 'Windows-' if name.startswith('w') else 'Linux-'
    valid = valid and (record['commit']==summary['full_app_commit']
                       and record['tree']=='13979fca3e96fc64b31a17044b3c56c93b937fd8'
                       and record['archive_sha256']=='42a1ce130db2bdc5d22d178ea56ce6b1dd14fddd8939eab838b5fed7352f4a33'
                       and record['source_file_count']==499 and counts['passed']>0
                       and record['python'].startswith(expected_minor)
                       and record['platform'].startswith(expected_platform)
                       and (name.startswith('w') or 'microsoft-standard-WSL2' in record['platform'])
                       and json.loads((directory/'source-manifest.json').read_text(encoding='utf-8'))==reference_manifest
                       and {c['name'] for c in record['checks']}=={'version','dependencies','pip-check','ruff','pytest'}
                       and score['commit']==summary['research_tool_commit']
                       and held['commit']==summary['held_excerpt_commit']
                       and held['base_commit']==summary['full_app_commit']
                       and held['original_source_files']==499
                       and len(held['supplemental_source'])==5
                       and score['app_tree']==held['app_tree']==summary['app_tree']
                       and record['executable']==score['executable']==held['executable']
                       and {c['name'] for c in score['checks']}=={'unittest','ruff','unmeasured'}
                       and {c['name'] for c in held['checks']}=={'unittest','ruff'}
                       and 'Ran 10 tests' in (root/score_name/'unittest.log').read_text(encoding='utf-8')
                       and 'Ran 7 tests' in (root/held_name/'unittest.log').read_text(encoding='utf-8'))
    for package, package_dir in ((record,directory),(score,root/score_name),(held,root/held_name)):
        valid = valid and all(hashlib.sha256((package_dir/filename).read_bytes()).hexdigest()==digest for filename,digest in package['artifacts_sha256'].items())
    summary['cells'].append(dict(name=name,python=record['python'],platform=record['platform'],pytest=counts,pytest_seconds=float(suite.attrib['time']),skips=skips,all_recorded_checks_pass=valid,
                                 app_artifact=str(Path(name)/'attestation.json'),score_artifact=str(Path(score_name)/'attestation.json'),held_artifact=str(Path(held_name)/'attestation.json')))
summary['complete_four_cell_matrix_pass']=all(c['all_recorded_checks_pass'] for c in summary['cells'])
for path in sorted(root.rglob('*')):
    if path.is_file() and path.name!='matrix-summary.json':
        summary['artifacts_sha256'][path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
(root/'matrix-summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True),encoding='utf-8')
for cell in summary['cells']:
    print(cell['name'],cell['pytest'],cell['pytest_seconds'],cell['all_recorded_checks_pass'])
print('summary_sha256',hashlib.sha256((root/'matrix-summary.json').read_bytes()).hexdigest())
raise SystemExit(0 if summary['complete_four_cell_matrix_pass'] else 1)

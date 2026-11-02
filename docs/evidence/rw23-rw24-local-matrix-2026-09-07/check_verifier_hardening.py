import importlib.util
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
folder = repo / 'docs/evidence/rw23-rw24-local-matrix-2026-09-07'
spec = importlib.util.spec_from_file_location('verifier', folder / 'verify_evidence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
results = {}
with tempfile.TemporaryDirectory(prefix='rw24-verifier-negative-') as temporary:
    root = Path(temporary)
    for cell in module.CELLS:
        shutil.copytree(repo / 'docs/evidence/rw22-local-matrix-2026-09-07' / cell, root / cell)
        path = root / cell / 'attestation.json'
        att = json.loads(path.read_text())
        att.update(commit=module.COMMIT, tree=module.TREE, archive_sha256=module.ARCHIVE)
        att['executable'] = module.EXECUTABLES[cell]
        for check in att['checks']:
            check['argv'] = [arg.replace('r22', 'r24').replace('rw22', 'rw24') for arg in check['argv']]
            check['argv'][0] = att['executable']
        path.write_text(json.dumps(att))
    result = module.verify_root(root)
    assert not result['verified']
    assert all('source manifest differs' in error for error in result['errors']), result
    results['old_matrix_relabelled_as_current'] = result
    for cell in module.CELLS:
        path = root / cell / 'attestation.json'
        att = json.loads(path.read_text())
        for check in att['checks']:
            if check['name'] in ('pytest', 'ruff'):
                check['argv'] = [att['executable'], '-c', 'print("not a test run")']
        path.write_text(json.dumps(att))
    result = module.verify_root(root)
    assert not result['verified']
    assert all('wrong gate argv' in error for error in result['errors']), result
    results['substituted_commands'] = result

# Unit-level coverage controls are synthetic, not claimed as executed application tests.
cases = json.loads((folder / 'expected-regressions.json').read_text())['cases']
xml = ET.Element('testsuite')
for classname, name in cases:
    ET.SubElement(xml, 'testcase', classname=classname, name=name)
assert module.regression_coverage(ET.tostring(xml)) == 225
for variant in ('skip', 'missing', 'duplicate'):
    changed = ET.fromstring(ET.tostring(xml))
    if variant == 'skip':
        ET.SubElement(changed[0], 'skipped')
    elif variant == 'missing':
        changed.remove(changed[0])
    else:
        changed.append(ET.fromstring(ET.tostring(changed[0])))
    try:
        module.regression_coverage(ET.tostring(changed))
    except ValueError as exc:
        results['coverage_' + variant] = {'refused': True, 'reason': str(exc)}
    else:
        raise AssertionError(variant + ' was accepted')

output = folder / 'verifier-negative-controls.json'
output.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
print('Relabelled old source, substituted commands, skipped/missing/duplicate required cases all refused.')

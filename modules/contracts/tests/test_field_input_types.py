import copy
import importlib.util
import json
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preflight', ROOT / 'preflight.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def fixture():
    return [json.loads((ROOT / 'examples' / (name + '.json')).read_text())
            for name in ['requirements', 'snapshot', 'binding']]


class TypeTests(unittest.TestCase):
    def run_case(self, source, key, value):
        data = fixture()
        field = next(iter(next(iter(data[source]['entities'].values()))['fields'].values()))
        field[key] = value
        before = copy.deepcopy(data)
        with self.assertRaises(v.ContractError):
            v.preflight(*data, account_id=data[1]['account_id'], business_id=data[1]['business_id'])
        self.assertEqual(data, before)


def add_test(source, key, value, label):
    def test(self):
        self.run_case(source, key, value)
    setattr(TypeTests, 'test_' + str(source) + '_' + key + '_' + label, test)


for source in [0, 1]:
    for value, label in [([], 'list'), ({}, 'dict'), (None, 'null'), (True, 'bool'), (5, 'integer')]:
        add_test(source, 'type', value, label)

for value, label in [([], 'list'), ({}, 'dict'), (None, 'null'), (True, 'bool')]:
    def test(self, value=value):
        data = fixture()
        field = next(iter(next(iter(data[0]['entities'].values()))['fields'].values()))
        field.update(type='link', target=value)
        with self.assertRaises(v.ContractError):
            v.preflight(*data, account_id=data[1]['account_id'], business_id=data[1]['business_id'])
    setattr(TypeTests, 'test_link_target_' + label, test)



for source in [0, 1]:
    for value, label in [([], 'list'), ({}, 'dict'), (None, 'null'), (True, 'bool'), (5, 'integer')]:
        def test(self, source=source, value=value):
            data = fixture()
            field = next(iter(next(iter(data[source]['entities'].values()))['fields'].values()))
            field['type'] = value
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                args = [sys.executable, str(ROOT / 'preflight.py'), '--account-id', data[1]['account_id'],
                        '--business-id', data[1]['business_id']]
                for name, payload in zip(['requirements', 'snapshot', 'binding'], data):
                    path = directory / (name + '.json')
                    path.write_text(json.dumps(payload))
                    args.extend(['--' + name, str(path)])
                report = directory / 'report.json'
                args.extend(['--report', str(report)])
                result = subprocess.run(args, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(report.exists())
                self.assertNotIn('Traceback', result.stderr)
        setattr(TypeTests, 'test_cli_' + str(source) + '_' + label, test)

if __name__ == '__main__':
    unittest.main()

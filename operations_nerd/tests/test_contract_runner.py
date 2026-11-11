"""Offline regression coverage of runner discovery and working directories."""
import importlib.util
from pathlib import Path
import tempfile
import contextlib
import io
import unittest
from unittest.mock import patch, Mock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('runner', ROOT/'scripts/run_tests.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.app=self.root/'operations_nerd';self.core=self.app/'tests'
        self.contract=self.root/'modules/contracts/tests'
        self.core.mkdir(parents=True);self.contract.mkdir(parents=True)
        self.p1=patch.object(r,'ROOT',self.root);self.p2=patch.object(r,'APP_DIR',self.app)
        self.p1.start();self.p2.start()
    def tearDown(self):self.p2.stop();self.p1.stop();self.tmp.cleanup()
    def file(self,where,name):
        p=where/name;p.write_text('pass\n');return p
    def test_contract_discovered(self):
        p=self.file(self.contract,'test_preflight.py');self.assertIn(p,r.select([]))
    def test_core_retained(self):
        p=self.file(self.core,'test_core.py');self.assertIn(p,r.select([]))
    def test_live_smoke_excluded(self):
        self.file(self.core,'test_llm_smoke.py');self.assertEqual(r.select([]),[])
    def test_contract_live_smoke_excluded(self):
        self.file(self.contract,'test_llm_smoke.py');self.assertEqual(r.select([]),[])
    def test_filter_matches_contract(self):
        p=self.file(self.contract,'test_preflight.py');self.file(self.core,'test_other.py')
        self.assertEqual(r.select(['preflight']),[p])
    def test_filter_matches_core(self):
        p=self.file(self.core,'test_core.py');self.file(self.contract,'test_preflight.py')
        self.assertEqual(r.select(['core']),[p])
    def test_multiple_filters_union(self):
        a=self.file(self.core,'test_core.py');b=self.file(self.contract,'test_preflight.py')
        self.assertEqual(r.select(['core','preflight']),[a,b])
    def test_non_test_files_ignored(self):
        self.file(self.contract,'helper.py');self.assertEqual(r.select([]),[])
    def test_missing_contract_directory_keeps_core(self):
        self.contract.rmdir();p=self.file(self.core,'test_core.py');self.assertEqual(r.select([]),[p])
    def test_core_working_directory_unchanged(self):
        p=self.file(self.core,'test_core.py')
        with patch.object(r.subprocess,'run',return_value=Mock(returncode=0,stdout='',stderr='')) as run:
            self.assertEqual(r.run_one(p)[0],0);self.assertEqual(run.call_args.kwargs['cwd'],self.app)
    def test_contract_uses_repository_working_directory(self):
        p=self.file(self.contract,'test_preflight.py')
        with patch.object(r.subprocess,'run',return_value=Mock(returncode=0,stdout='',stderr='')) as run:
            self.assertEqual(r.run_one(p)[0],0);self.assertEqual(run.call_args.kwargs['cwd'],self.root)
    def test_contract_failure_propagates(self):
        self.file(self.contract,'test_preflight.py')
        with patch.object(r,'run_one',return_value=(1,'fixture failure',0.)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.main([]),1)

if __name__=='__main__':unittest.main()

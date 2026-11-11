import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preflight',ROOT/'preflight.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
VERSION='binding-preflight-1'


def fixture():
    req={'draft_version':VERSION,'module':'fictional-stock','entities':{
        'item':{'fields':{'name':{'type':'text','target':None}}},
        'order':{'fields':{'quantity':{'type':'integer','target':None},'item':{'type':'link','target':'item'}}}}}
    snapshot={'draft_version':VERSION,'account_id':'account-A','business_id':'business-A','revision':'r7','entities':{
        'products':{'account_id':'account-A','business_id':'business-A','archived':False,'fields':{'label':{'type':'text','target':None,'archived':False}}},
        'requests':{'account_id':'account-A','business_id':'business-A','archived':False,'fields':{
            'units':{'type':'integer','target':None,'archived':False},'product_id':{'type':'link','target':'products','archived':False}}}}}
    binding={'draft_version':VERSION,'module':'fictional-stock','account_id':'account-A','business_id':'business-A','revision':'r7','entities':{
        'item':{'entity':'products','fields':{'name':'label'}},'order':{'entity':'requests','fields':{'quantity':'units','item':'product_id'}}}}
    return req,snapshot,binding


class Tests(unittest.TestCase):
    def check(self,change,code):
        data=fixture();change(*data)
        result=v.preflight(*data,account_id='account-A',business_id='business-A')
        self.assertFalse(result['compatible'])
        self.assertIn(code,[i['code'] for i in result['issues']])
    def test_valid(self):
        data=fixture();before=copy.deepcopy(data)
        r=v.preflight(*data,account_id='account-A',business_id='business-A')
        self.assertTrue(r['compatible']);self.assertEqual(data,before)
        self.assertEqual(r,v.preflight(*data,account_id='account-A',business_id='business-A'))
    def test_snapshot_account(self): self.check(lambda r,s,b:s.update(account_id='other'),'snapshot_scope_mismatch')
    def test_snapshot_business(self): self.check(lambda r,s,b:s.update(business_id='other'),'snapshot_scope_mismatch')
    def test_binding_account(self): self.check(lambda r,s,b:b.update(account_id='other'),'binding_scope_mismatch')
    def test_binding_business(self): self.check(lambda r,s,b:b.update(business_id='other'),'binding_scope_mismatch')
    def test_entity_account(self): self.check(lambda r,s,b:s['entities']['products'].update(account_id='other'),'entity_scope_mismatch')
    def test_entity_business(self): self.check(lambda r,s,b:s['entities']['products'].update(business_id='other'),'entity_scope_mismatch')
    def test_module(self): self.check(lambda r,s,b:b.update(module='different'),'module_mismatch')
    def test_revision(self): self.check(lambda r,s,b:b.update(revision='r6'),'stale_binding')
    def test_missing_role(self): self.check(lambda r,s,b:b['entities'].pop('item'),'missing_role')
    def test_extra_role(self): self.check(lambda r,s,b:b['entities'].update(extra=copy.deepcopy(b['entities']['item'])),'unexpected_role')
    def test_missing_entity(self): self.check(lambda r,s,b:b['entities']['item'].update(entity='missing'),'missing_entity')
    def test_archived_entity(self): self.check(lambda r,s,b:s['entities']['products'].update(archived=True),'archived_entity')
    def test_missing_field_role(self): self.check(lambda r,s,b:b['entities']['order']['fields'].pop('quantity'),'missing_field_role')
    def test_extra_field_role(self): self.check(lambda r,s,b:b['entities']['item']['fields'].update(other='label'),'unexpected_field_role')
    def test_missing_field(self): self.check(lambda r,s,b:b['entities']['order']['fields'].update(quantity='missing'),'missing_field')
    def test_archived_field(self): self.check(lambda r,s,b:s['entities']['products']['fields']['label'].update(archived=True),'archived_field')
    def test_type(self): self.check(lambda r,s,b:s['entities']['requests']['fields']['units'].update(type='number'),'field_type_mismatch')
    def test_link(self): self.check(lambda r,s,b:s['entities']['requests']['fields']['product_id'].update(target='foreign'),'link_target_mismatch')
    def test_alias_entities(self): self.check(lambda r,s,b:b['entities']['order'].update(entity='products'),'aliased_entities')
    def test_alias_fields(self): self.check(lambda r,s,b:b['entities']['order']['fields'].update(quantity='product_id'),'aliased_fields')
    def test_no_name_inference(self): self.check(lambda r,s,b:b['entities']['item'].update(entity='item'),'missing_entity')
    def test_hash_change(self):
        data=fixture();a=v.preflight(*data,account_id='account-A',business_id='business-A')
        data[1]['revision']='r8';b=v.preflight(*data,account_id='account-A',business_id='business-A')
        self.assertNotEqual(a['source_sha256']['snapshot'],b['source_sha256']['snapshot'])
    def test_hash_order(self):
        data=fixture();a=v.preflight(*data,account_id='account-A',business_id='business-A')
        data[1]['entities']=dict(reversed(list(data[1]['entities'].items())))
        self.assertEqual(a,v.preflight(*data,account_id='account-A',business_id='business-A'))
    def test_report_omits_scope_values(self):
        text=json.dumps(v.preflight(*fixture(),account_id='account-A',business_id='business-A'))
        self.assertNotIn('account-A',text);self.assertNotIn('business-A',text)
    def test_multiple_errors_collected(self):
        data=fixture();data[2]['revision']='old';data[2]['module']='wrong'
        self.assertEqual(len(v.preflight(*data,account_id='account-A',business_id='business-A')['issues']),2)
    def test_nonmapping(self):
        with self.assertRaises(v.ContractError):v.preflight([],{}, {},account_id='a',business_id='b')
    def test_unknown_version(self):
        data=fixture();data[0]['draft_version']='future'
        with self.assertRaises(v.ContractError):v.preflight(*data,account_id='a',business_id='b')
    def test_unknown_key(self):
        data=fixture();data[2]['execute']=True
        with self.assertRaises(v.ContractError):v.preflight(*data,account_id='a',business_id='b')
    def test_unknown_type(self):
        data=fixture();data[0]['entities']['item']['fields']['name']['type']='money'
        with self.assertRaises(v.ContractError):v.preflight(*data,account_id='a',business_id='b')
    def test_unknown_required_target(self):
        data=fixture();data[0]['entities']['order']['fields']['item']['target']='missing'
        with self.assertRaises(v.ContractError):v.preflight(*data,account_id='a',business_id='b')
    def test_nonlink_target(self):
        data=fixture();data[0]['entities']['item']['fields']['name']['target']='item'
        with self.assertRaises(v.ContractError):v.preflight(*data,account_id='a',business_id='b')
    def test_blank_scope(self):
        with self.assertRaises(v.ContractError):v.preflight(*fixture(),account_id='',business_id='b')
    def test_whitespace_scope(self):
        with self.assertRaises(v.ContractError):v.preflight(*fixture(),account_id=' a',business_id='b')
    def test_no_bool_revision(self):
        data=fixture();data[1]['revision']=True
        with self.assertRaises(v.ContractError):v.preflight(*data,account_id='a',business_id='b')
    def test_no_string_archived(self):
        data=fixture();data[1]['entities']['products']['archived']='false'
        with self.assertRaises(v.ContractError):v.preflight(*data,account_id='a',business_id='b')
    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            for name,data in zip(('requirements','snapshot','binding'),fixture()):(d/(name+'.json')).write_text(json.dumps(data))
            args=[sys.executable,str(ROOT/'preflight.py'),'--account-id','account-A','--business-id','business-A']
            for name in ('requirements','snapshot','binding','report'):args+=['--'+name,str(d/(name+'.json'))]
            self.assertEqual(subprocess.run(args,capture_output=True).returncode,0)
            good=(d/'report.json').read_bytes()
            data=json.loads((d/'binding.json').read_text());data['revision']='old';(d/'binding.json').write_text(json.dumps(data))
            self.assertEqual(subprocess.run(args,capture_output=True).returncode,1)
            previous=(d/'report.json').read_bytes();(d/'binding.json').write_text('{"x":1,"x":2}')
            self.assertEqual(subprocess.run(args,capture_output=True).returncode,2)
            self.assertEqual((d/'report.json').read_bytes(),previous)
            args[-1]=str(d/'snapshot.json');self.assertEqual(subprocess.run(args,capture_output=True).returncode,2)
    def test_json_nonfinite(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.json';p.write_text('{"a":NaN}')
            with self.assertRaises(v.ContractError):v.read_json(p)
    def test_no_core_imports(self):
        source=(ROOT/'preflight.py').read_text()
        self.assertNotIn('import operations_nerd',source)
        self.assertNotIn('sqlite3',source)


if __name__=='__main__':unittest.main()

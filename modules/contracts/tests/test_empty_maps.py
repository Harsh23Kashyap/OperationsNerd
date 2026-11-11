"""Empty maps are invalid structure, not a compatible empty module."""
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from test_preflight import fixture,v

class EmptyMapTests(unittest.TestCase):
    pass

def case(index,role=None):
    def test(self):
        data=fixture()
        if role is None:data[index]['entities']={}
        elif index==0:data[index]['entities'][role]['fields']={}
        elif index==1:data[index]['entities'][role]['fields']={}
        else:data[index]['entities'][role]['fields']={}
        with self.assertRaisesRegex(v.ContractError,'nonempty mapping'):
            v.preflight(*data,account_id='account-A',business_id='business-A')
    return test
for label,index,role in [('requirements_entities',0,None),('snapshot_entities',1,None),('binding_entities',2,None),('requirements_fields',0,'item'),('snapshot_fields',1,'products'),('binding_fields',2,'item')]:
    setattr(EmptyMapTests,'test_empty_'+label,case(index,role))

if __name__=='__main__':unittest.main()

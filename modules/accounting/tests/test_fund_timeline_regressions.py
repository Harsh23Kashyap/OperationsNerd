"""Offline SQLite + real FastAPI-router regressions. No providers or business data.
Run this file with ACCOUNTING_SOURCE_ROOT to compare the same tests to an older tree.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get('ACCOUNTING_SOURCE_ROOT', Path(__file__).resolve().parents[3]))
sys.path[:0] = [str(ROOT / 'modules/accounting'), str(ROOT / 'modules/accounting/tests')]
import accounting as a
from test_fund_spendable_regressions import setup
from fastapi import FastAPI
from fastapi.testclient import TestClient
import api

class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.c,self.f,self.bank,self.cap,self.u,self.r,self.clear,self.exp = setup()
        self.inc = a.account_by_code(self.c,'4000')['account_id']
        self.n = 0
    def tearDown(self):
        self.c.close()
    def post(self, date, debit, credit, amount, key=None, fund=None, source='manual'):
        self.n += 1
        fid = self.f if fund is None else fund
        return a.post_journal_entry(self.c,key or 'p'+str(self.n),date,'Timeline regression',[
            {'account_id':debit,'debit_cents':amount,'fund_id':fid},
            {'account_id':credit,'credit_cents':amount,'fund_id':fid}],source=source)
    def spend(self,date='2026-03-01',amount=8000,**kw):
        return self.post(date,self.exp,self.bank,amount,**kw)
    def balance(self,date='2026-12-31'):
        return a.fund(self.c,self.f,as_of=date)['available_cents']
    def reject_atomic(self, fn):
        before = [tuple(r) for r in self.c.execute('SELECT * FROM acct_journal_entries')]
        lines = self.c.execute('SELECT COUNT(*) FROM acct_journal_lines').fetchone()[0]
        with self.assertRaises(a.InsufficientFundBalance): fn()
        self.assertEqual(before,[tuple(r) for r in self.c.execute('SELECT * FROM acct_journal_entries')])
        self.assertEqual(lines,self.c.execute('SELECT COUNT(*) FROM acct_journal_lines').fetchone()[0])
    def test_backdated_expense_covers_later_spend(self):
        self.spend(); self.reject_atomic(lambda:self.spend('2026-02-01'))
    def test_backdated_capital_covers_later_spend(self):
        self.spend(); self.reject_atomic(lambda:self.post('2026-02-01',self.cap,self.bank,8000))
    def test_backdated_payroll_covers_later_spend(self):
        self.spend(); self.reject_atomic(lambda:self.spend('2026-02-01',source='payroll'))
    def test_backdated_release_covers_later_spend(self):
        self.spend(); self.reject_atomic(lambda:a.release_from_restriction(self.c,'rel',self.f,8000,'2026-02-01','Release',self.r,self.u,self.clear))
    def test_income_refund_cannot_overdraw(self):
        self.spend(); self.reject_atomic(lambda:self.post('2026-04-01',self.inc,self.bank,8000))
    def test_equity_withdrawal_cannot_overdraw(self):
        self.spend(); self.reject_atomic(lambda:self.post('2026-04-01',self.r,self.bank,8000))
    def test_income_reversal_cannot_remove_consumed_capacity(self):
        opening=self.post('2026-01-02',self.bank,self.inc,8000)
        self.spend(amount=16000)
        self.reject_atomic(lambda:a.reverse_journal_entry(self.c,opening['entry_id'],'undo'))
    def test_later_receipt_does_not_hide_intermediate_deficit(self):
        self.spend()
        a.record_grant_receipt(self.c,'later',self.f,'2026-04-01',10000,self.bank,self.inc,'Later')
        self.reject_atomic(lambda:self.spend('2026-02-01'))
    def test_exact_later_capacity_allowed(self):
        self.spend(); self.spend('2026-02-01',2000); self.assertEqual(self.balance(),0)
    def test_same_day_net_change_not_arbitrary_entry_order(self):
        self.spend()
        self.post('2026-03-01',self.bank,self.inc,8000)
        self.spend('2026-02-01',8000); self.assertEqual(self.balance(),2000)
    def test_unrestricted_backdating_unchanged(self):
        self.spend(fund=a.default_fund_id(self.c)); self.spend('2026-02-01',fund=a.default_fund_id(self.c))
        self.assertEqual(self.balance(),10000)
    def test_other_fund_not_used_as_capacity(self):
        g=a.add_fund(self.c,'other','OTHER','Other','temporarily_restricted')['fund_id']
        self.post('2026-01-02',self.bank,self.inc,50000,fund=g)
        self.spend(); self.reject_atomic(lambda:self.spend('2026-02-01'))
    def test_offsetting_income_and_expense_net_once(self):
        self.post('2026-02-01',self.exp,self.inc,50000); self.assertEqual(self.balance(),10000)
    def test_failure_leaves_key_reusable(self):
        self.spend(); self.reject_atomic(lambda:self.spend('2026-02-01',key='retry'))
        a.record_grant_receipt(self.c,'more',self.f,'2026-01-02',6000,self.bank,self.inc,'More')
        result=self.spend('2026-02-01',key='retry')
        replay=self.spend('2026-02-01',key='retry')
        self.assertEqual(result,replay);self.assertEqual(self.balance(),0)
    def test_cash_transfer_no_capacity_change(self):
        self.spend(); other=a.account_by_code(self.c,'1010')['account_id']
        self.post('2026-02-01',other,self.bank,50000);self.assertEqual(self.balance(),2000)
    def test_backdated_bill_rejection_preserves_draft(self):
        self.spend(); supplier=a.add_supplier(self.c,'sup','Timeline supplier')['id']
        doc=a.create_bill(self.c,'bill',supplier,'B-1','2026-02-01',[{'description':'Work','unit_price_cents':8000,'account_id':self.exp}],fund_id=self.f)
        self.reject_atomic(lambda:a.receive_bill(self.c,doc['bill_id'],'recv'))
        self.assertEqual(a.bill(self.c,doc['bill_id'])['status'],'draft')
    def http(self):
        path=self.c.execute('PRAGMA database_list').fetchone()['file']
        os.environ['ACCOUNTING_DATABASE_PATH']=path
        app=FastAPI(); app.include_router(api.router)
        return TestClient(app)
    def payload(self,date,debit,credit,amount):
        return {'entry_date':date,'memo':'HTTP regression','lines':[
            {'account_id':debit,'debit_cents':amount,'fund_id':self.f},
            {'account_id':credit,'credit_cents':amount,'fund_id':self.f}]}
    def test_http_backdated_expense_409_and_rollback(self):
        self.spend()
        with self.http() as client:
            response=client.post('/accounting/journal',json=self.payload('2026-02-01',self.exp,self.bank,8000),headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn('2026-03-01',response.text);self.assertEqual(self.balance(),2000)
    def test_http_income_refund_409(self):
        self.spend()
        with self.http() as client:
            response=client.post('/accounting/journal',json=self.payload('2026-04-01',self.inc,self.bank,8000),headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text)
    def test_http_exact_capacity_201_and_idempotent(self):
        self.spend()
        with self.http() as client:
            payload=self.payload('2026-02-01',self.exp,self.bank,2000)
            x=client.post('/accounting/journal',json=payload,headers={'Idempotency-Key':'http'})
            y=client.post('/accounting/journal',json=payload,headers={'Idempotency-Key':'http'})
        self.assertEqual(x.status_code,201,x.text);self.assertEqual(x.json(),y.json());self.assertEqual(self.balance(),0)
    def test_http_release_409(self):
        self.spend()
        with self.http() as client:
            response=client.post('/accounting/funds/release-from-restriction',json={
                'from_fund_id':self.f,'amount_cents':8000,'entry_date':'2026-02-01','memo':'Release',
                'donor_restricted_account_id':self.r,'unrestricted_net_assets_account_id':self.u,
                'clearing_account_id':self.clear},headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text)

    def test_http_backdated_capital_409(self):
        self.spend()
        with self.http() as client:
            response=client.post('/accounting/journal',json=self.payload('2026-02-01',self.cap,self.bank,8000),headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text)
    def test_http_equity_withdrawal_409(self):
        self.spend()
        with self.http() as client:
            response=client.post('/accounting/journal',json=self.payload('2026-04-01',self.r,self.bank,8000),headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text)
    def test_http_payroll_backdate_409(self):
        self.spend()
        payload=self.payload('2026-02-01',self.exp,self.bank,8000);payload['source']='payroll'
        with self.http() as client:
            response=client.post('/accounting/journal',json=payload,headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text)
    def test_http_income_reversal_409(self):
        opening=self.post('2026-01-02',self.bank,self.inc,8000);self.spend(amount=16000)
        with self.http() as client:
            response=client.post('/accounting/journal/'+str(opening['entry_id'])+'/reverse',json={},headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text)
    def test_http_later_receipt_does_not_hide_gap(self):
        self.spend()
        a.record_grant_receipt(self.c,'later',self.f,'2026-04-01',10000,self.bank,self.inc,'Later')
        with self.http() as client:
            response=client.post('/accounting/journal',json=self.payload('2026-02-01',self.exp,self.bank,8000),headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,409,response.text);self.assertIn('2026-03-01',response.text)
    def test_http_same_entry_offset_201(self):
        with self.http() as client:
            response=client.post('/accounting/journal',json=self.payload('2026-02-01',self.exp,self.inc,50000),headers={'Idempotency-Key':'http'})
        self.assertEqual(response.status_code,201,response.text);self.assertEqual(self.balance(),10000)

if __name__=='__main__':unittest.main(verbosity=2)

"""Every saved posting prefix is checked, with explicit bank mapping. Offline only."""
import sys
import unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import accounting as a
from test_fund_spendable_regressions import setup

class PostingTests(unittest.TestCase):
    def setUp(self):
        self.c,self.f,self.b,self.k,self.u,self.r,self.l,self.e=setup()
        self.v=a.account_by_code(self.c,'4000')['account_id']
        self.n=0
    def tearDown(self):self.c.close()
    def map(self):
        if not a.fund_controls(self.c,self.f)['cash_configured']:
            a.configure_fund_cash_accounts(self.c,'map',self.f,[self.b],'fixture-admin','Reviewed bank')
    def post(self,date,debit,credit,amount,key=None):
        self.n+=1
        return a.post_journal_entry(self.c,key or 'p'+str(self.n),date,'Prefix test',[
            dict(account_id=debit,debit_cents=amount,fund_id=self.f),
            dict(account_id=credit,credit_cents=amount,fund_id=self.f)])
    def suspend(self,which):a.set_fund_constraint(self.c,'off:'+which,self.f,which,False,'fixture-admin','Explicit temporary exception')
    def reject(self,fn):
        entries=[tuple(r) for r in self.c.execute('SELECT * FROM acct_journal_entries')]
        with self.assertRaises((a.InsufficientFundBalance,a.InvalidState)):fn()
        self.assertEqual(entries,[tuple(r) for r in self.c.execute('SELECT * FROM acct_journal_entries')])
    def test_later_same_date_receipt_does_not_hide_capacity_prefix(self):
        self.map();self.post('2026-03-01',self.e,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        self.reject(lambda:self.post('2026-02-01',self.e,self.b,8000))
    def test_later_same_date_receipt_does_not_hide_cash_prefix(self):
        self.map();other=a.add_account(self.c,'other','1700','Noncash asset','asset')['account_id']
        self.post('2026-03-01',other,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        self.reject(lambda:self.post('2026-02-01',other,self.b,8000))
    def test_receipt_before_spend_is_valid_saved_order(self):
        self.map();self.post('2026-03-01',self.b,self.v,8000);self.post('2026-03-01',self.e,self.b,8000)
        self.post('2026-02-01',self.e,self.b,8000)
        self.assertEqual(a.fund(self.c,self.f,as_of='2026-12-31')['available_cents'],2000)
    def test_new_same_date_entry_appends_after_saved_receipt(self):
        self.map();self.post('2026-03-01',self.b,self.v,8000);self.post('2026-03-01',self.e,self.b,18000)
        self.assertEqual(a._fund_cash_balance(self.c,self.f,'2026-12-31'),0)
    def test_backdated_rejection_key_can_retry_after_earlier_receipt(self):
        self.map();self.post('2026-03-01',self.e,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        self.reject(lambda:self.post('2026-02-01',self.e,self.b,8000,'retry'))
        self.post('2026-01-02',self.b,self.v,6000)
        first=self.post('2026-02-01',self.e,self.b,8000,'retry')
        self.assertEqual(first,self.post('2026-02-01',self.e,self.b,8000,'retry'))
    def test_capacity_suppression_does_not_hide_cash_prefix(self):
        self.map();self.suspend('capacity');self.post('2026-03-01',self.e,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        self.reject(lambda:self.post('2026-02-01',self.e,self.b,8000))
    def test_cash_suppression_does_not_hide_capacity_prefix(self):
        self.map();self.suspend('cash');self.post('2026-03-01',self.e,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        self.reject(lambda:self.post('2026-02-01',self.e,self.b,8000))
    def test_explicit_both_suppressions_allow_and_audit_prefix_exception(self):
        self.map();self.suspend('cash');self.suspend('capacity')
        self.post('2026-03-01',self.e,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        entry=self.post('2026-02-01',self.e,self.b,8000)
        rows=a.fund_constraint_audit(self.c,self.f)
        self.assertEqual(len([r for r in rows if r['journal_entry_id']==entry['entry_id']]),2)
    def test_unmapped_expense_fails_closed(self):
        # Separate unmapped fund, not a purpose/allocation change.
        f=a.add_fund(self.c,'f2','NEW','Unmapped','temporarily_restricted')['fund_id']
        a.record_grant_receipt(self.c,'grant2',f,'2026-01-01',10000,self.b,self.v,'New grant')
        self.f=f;self.reject(lambda:self.post('2026-02-01',self.e,self.b,1))
    def test_unmapped_asset_outflow_fails_closed(self):
        f=a.add_fund(self.c,'f2','NEW','Unmapped','temporarily_restricted')['fund_id']
        a.record_grant_receipt(self.c,'grant2',f,'2026-01-01',10000,self.b,self.v,'New grant')
        self.f=f;self.reject(lambda:self.post('2026-02-01',self.k,self.b,1))
    def test_unmapped_bill_commitment_fails_closed(self):
        f=a.add_fund(self.c,'f2','NEW','Unmapped','temporarily_restricted')['fund_id']
        a.record_grant_receipt(self.c,'grant2',f,'2026-01-01',10000,self.b,self.v,'New grant')
        s=a.add_supplier(self.c,'supplier','Fixture')['id']
        bill=a.create_bill(self.c,'bill',s,'B','2026-02-01',[dict(description='Work',unit_price_cents=1,account_id=self.e)],fund_id=f)
        self.reject(lambda:a.receive_bill(self.c,bill['bill_id'],'receive'))
    def test_unmapped_cash_suspension_is_explicit_exception(self):
        f=a.add_fund(self.c,'f2','NEW','Unmapped','temporarily_restricted')['fund_id']
        a.record_grant_receipt(self.c,'grant2',f,'2026-01-01',10000,self.b,self.v,'New grant')
        self.f=f;self.suspend('cash');entry=self.post('2026-02-01',self.e,self.b,1)
        self.assertTrue(any(r['journal_entry_id']==entry['entry_id'] for r in a.fund_constraint_audit(self.c,f)))
    def test_mapping_validates_historical_prefix_not_date_end(self):
        f=a.add_fund(self.c,'f2','NEW','Unmapped','temporarily_restricted')['fund_id'];self.f=f
        self.suspend('cash');self.suspend('capacity')
        self.post('2026-01-01',self.e,self.b,100)
        self.post('2026-01-01',self.b,self.v,100)
        # Cash remains suppressed, so review can be recorded, never silently enforced.
        a.configure_fund_cash_accounts(self.c,'map-history',f,[self.b],'fixture-admin','Reviewed exception')
        self.assertFalse(a.fund_controls(self.c,f)['cash_enforced'])
    def test_future_reinstatement_checks_each_prefix(self):
        from unittest.mock import patch
        self.map();self.suspend('cash');self.suspend('capacity')
        self.post('2027-01-01',self.e,self.b,12000);self.post('2027-01-01',self.b,self.v,5000)
        original=a._as_of
        with patch.object(a,'_as_of',lambda value:original('2026-11-10' if value is None else value)):
            self.reject(lambda:a.set_fund_constraint(self.c,'on',self.f,'cash',True,'fixture-admin','Recheck'))

    def test_backdated_bill_commitment_checks_each_later_prefix(self):
        self.map();self.post('2026-03-01',self.e,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        supplier=a.add_supplier(self.c,'supplier','Fixture')['id']
        bill=a.create_bill(self.c,'bill',supplier,'B','2026-02-01',[dict(description='Work',unit_price_cents=8000,account_id=self.e)],fund_id=self.f)
        self.reject(lambda:a.receive_bill(self.c,bill['bill_id'],'receive'))
    def test_backdated_release_checks_each_later_prefix(self):
        self.map();self.post('2026-03-01',self.e,self.b,8000);self.post('2026-03-01',self.b,self.v,8000)
        self.reject(lambda:a.release_from_restriction(self.c,'release',self.f,8000,'2026-02-01','Purpose met',self.r,self.u,self.l))
    def test_reversal_cannot_remove_money_needed_by_later_prefix(self):
        self.map();entry=self.post('2026-01-02',self.b,self.v,8000)
        self.post('2026-03-01',self.e,self.b,16000);self.post('2026-03-01',self.b,self.v,8000)
        self.reject(lambda:a.reverse_journal_entry(self.c,entry['entry_id'],'reverse'))
    def test_unmapped_receipt_and_zero_delta_setup_allowed(self):
        f=a.add_fund(self.c,'f2','NEW','Unmapped','temporarily_restricted')['fund_id'];self.f=f
        self.post('2026-01-01',self.b,self.v,10000)
        self.assertEqual(a.fund(self.c,f,as_of='2026-12-31')['available_cents'],10000)
        self.map();self.post('2026-02-01',self.e,self.b,10000)
        self.assertEqual(a._fund_cash_balance(self.c,f,'2026-12-31'),0)

if __name__=='__main__':unittest.main()

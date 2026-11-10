"""Saved posting-order contract and review regressions, disposable SQLite/real HTTP only."""
import os
from pathlib import Path
import sys
import unittest
ROOT = Path(os.environ.get('ACCOUNTING_SOURCE_ROOT', Path(__file__).resolve().parents[3]))
sys.path[:0] = [str(ROOT/'modules/accounting'), str(ROOT/'modules/accounting/tests')]
import accounting as a
import test_fund_timeline_regressions as timeline
from test_fund_spendable_regressions import setup
class ReviewFollowthrough(timeline.TimelineTests):
 # Inherited 26 cases are rerun as well as these 12 review cases.
 def test_multistep_jan_mar_may_feb_cites_march(self):
  self.spend('2026-03-01',8000)
  a.record_grant_receipt(self.c,'may',self.f,'2026-05-01',10000,self.bank,self.inc,'May receipt')
  with self.assertRaisesRegex(a.InsufficientFundBalance,'2000 cents available on 2026-03-01'):
   self.spend('2026-02-01',8000)
  self.assertEqual(self.balance('2026-03-01'),2000);self.assertEqual(self.balance('2026-05-01'),12000)
 def test_permanent_backdated_expense_insufficient(self):
  self.c.close();self.c,self.f,self.bank,self.cap,self.u,self.r,self.clear,self.exp=setup('permanently_restricted')
  self.inc=a.account_by_code(self.c,'4000')['account_id'];self.spend();self.reject_atomic(lambda:self.spend('2026-02-01',8000))
 def test_permanent_backdated_capital_insufficient(self):
  self.c.close();self.c,self.f,self.bank,self.cap,self.u,self.r,self.clear,self.exp=setup('permanently_restricted')
  self.inc=a.account_by_code(self.c,'4000')['account_id'];self.spend();self.reject_atomic(lambda:self.post('2026-02-01',self.cap,self.bank,8000))
 def test_permanent_backdated_release_always_refused(self):
  self.c.close();self.c,self.f,self.bank,self.cap,self.u,self.r,self.clear,self.exp=setup('permanently_restricted')
  with self.assertRaisesRegex(a.InvalidState,'permanent funds must remain restricted'):
   a.release_from_restriction(self.c,'rel',self.f,1,'2026-02-01','Release',self.r,self.u,self.clear)
  self.assertEqual(self.balance(),10000)
 def test_same_day_second_expense_refused(self):
  self.spend('2026-03-01',6000);self.reject_atomic(lambda:self.spend('2026-03-01',4001));self.assertEqual(self.balance(),4000)
 def test_same_day_two_expenses_exact(self):
  self.spend('2026-03-01',6000);self.spend('2026-03-01',4000);self.assertEqual(self.balance(),0)
 def test_saved_order_receipt_after_spend_rejects_backdate(self):
  self.spend('2026-03-01',8000);self.post('2026-03-01',self.bank,self.inc,8000)
  self.reject_atomic(lambda:self.spend('2026-02-01',8000))
  self.assertEqual(self.balance('2026-02-01'),10000);self.assertEqual(self.balance('2026-03-01'),10000)
 def test_saved_order_receipt_before_spend_allows_backdate(self):
  self.post('2026-03-01',self.bank,self.inc,8000);self.spend('2026-03-01',8000);self.spend('2026-02-01',8000)
  self.assertEqual(self.balance('2026-03-01'),2000)
 def test_release_posting_date_error_uses_common_format(self):
  self.spend();
  with self.assertRaisesRegex(a.InsufficientFundBalance,'fund FUND has 2000 cents available on 2026-04-01; this entry reduces capacity by 8000 cents'):
   a.release_from_restriction(self.c,'rel',self.f,8000,'2026-04-01','Release',self.r,self.u,self.clear)
  self.assertEqual(self.balance(),2000)
 def test_release_later_date_error_uses_common_format(self):
  self.spend()
  with self.assertRaisesRegex(a.InsufficientFundBalance,'fund FUND has 2000 cents available on 2026-03-01; this entry reduces capacity by 8000 cents'):
   a.release_from_restriction(self.c,'rel',self.f,8000,'2026-02-01','Release',self.r,self.u,self.clear)
 def test_release_rejected_key_can_retry(self):
  self.spend()
  with self.assertRaises(a.InsufficientFundBalance):a.release_from_restriction(self.c,'rel',self.f,8000,'2026-04-01','Release',self.r,self.u,self.clear)
  self.post('2026-01-02',self.bank,self.inc,6000)
  p=a.release_from_restriction(self.c,'rel',self.f,8000,'2026-04-01','Release',self.r,self.u,self.clear)
  self.assertEqual(a.release_from_restriction(self.c,'rel',self.f,8000,'2026-04-01','Release',self.r,self.u,self.clear),p)
  self.assertEqual(self.balance(),0)
 def test_http_same_day_second_expense_rollback(self):
  self.spend('2026-03-01',6000)
  with self.http() as client:
   result=client.post('/accounting/journal',headers={'Idempotency-Key':'http-extra'},json=self.payload('2026-03-01',self.exp,self.bank,4001))
   self.assertEqual(result.status_code,409)
  self.assertEqual(self.balance(),4000)
if __name__=='__main__': unittest.main()

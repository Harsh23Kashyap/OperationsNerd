"""Disposable SQLite controls, no network, credentials or real business data."""
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('ACCOUNTING_SOURCE_ROOT', Path(__file__).resolve().parents[3]))
sys.path[:0] = [str(ROOT/'modules/accounting'), str(ROOT/'modules/accounting/tests')]
import accounting as a
from test_fund_spendable_regressions import setup


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.c,self.f,self.bank,self.cap,self.u,self.r,self.clear,self.exp = setup()
        self.income = a.account_by_code(self.c,'4000')['account_id']
        self.other = a.add_account(self.c,'otherasset','1700','Other asset','asset')['account_id']
        self.n = 0
        original_as_of = a._as_of
        clock = patch.object(a, '_as_of', lambda value: original_as_of('2026-11-10' if value is None else value))
        clock.start()
        self.addCleanup(clock.stop)
    def tearDown(self):
        self.c.close()
    def post(self,debit,credit,n,date='2026-02-01',key=None):
        self.n += 1
        return a.post_journal_entry(self.c,key or 'p'+str(self.n),date,'Controls fixture',[
            dict(account_id=debit,debit_cents=n,fund_id=self.f),
            dict(account_id=credit,credit_cents=n,fund_id=self.f)])
    def mapping(self,ids=None,key='cash'):
        return a.configure_fund_cash_accounts(self.c,key,self.f,ids or [self.bank],'admin-fixture','Reviewed bank mapping')
    def change(self,constraint,enabled,key=None,actor='admin-fixture',reason='Expected grant not received'):
        self.n += 1
        return a.set_fund_constraint(self.c,key or 'ctrl'+str(self.n),self.f,constraint,enabled,actor,reason)
    def audit(self):
        return a.fund_constraint_audit(self.c,self.f)
    def reject_atomic(self,fn,exc=a.AccountingError):
        before = (len(a.journal_entries(self.c)),len(self.audit()),self.c.execute('SELECT count(*) FROM acct_commands').fetchone()[0])
        with self.assertRaises(exc): fn()
        self.assertEqual(before,(len(a.journal_entries(self.c)),len(self.audit()),self.c.execute('SELECT count(*) FROM acct_commands').fetchone()[0]))
    def test_default_capacity_still_blocks_overspend(self):
        self.reject_atomic(lambda:self.post(self.exp,self.bank,10001))
    def test_unconfigured_cash_is_reported_not_inferred(self):
        state=a.fund_controls(self.c,self.f)
        self.assertFalse(state['cash_configured'])
        self.assertTrue(state['capacity_enabled'])
        self.assertEqual(state['cash_account_ids'],[])
    def test_cash_and_capacity_are_distinct(self):
        self.mapping()
        self.post(self.other,self.income,10000)
        self.assertEqual(a.fund(self.c,self.f,as_of='2026-12-31')['available_cents'],20000)
        self.reject_atomic(lambda:self.post(self.exp,self.bank,15000))
    def test_cash_reduction_without_capacity_change_is_checked(self):
        self.mapping()
        self.reject_atomic(lambda:self.post(self.other,self.bank,10001))
    def test_exact_cash_zero_allowed(self):
        self.mapping();self.post(self.other,self.bank,10000)
        self.assertEqual(a._fund_cash_balance(self.c,self.f,'2026-12-31'),0)
    def test_cash_mapping_multiple_accounts_transfer_nets_once(self):
        bank2=a.account_by_code(self.c,'1010')['account_id']
        self.mapping([self.bank,bank2]);self.post(bank2,self.bank,10000)
        self.assertEqual(a._fund_cash_balance(self.c,self.f,'2026-12-31'),10000)
    def test_backdated_cash_checks_later_commitment(self):
        self.mapping();self.post(self.other,self.bank,8000,'2026-03-01')
        self.post(self.bank,self.income,10000,'2026-04-01')
        self.reject_atomic(lambda:self.post(self.other,self.bank,8000))
    def test_same_date_cash_is_netted_not_entry_order(self):
        self.mapping();self.post(self.other,self.bank,8000,'2026-03-01')
        self.post(self.bank,self.income,8000,'2026-03-01')
        self.post(self.other,self.bank,8000)
        self.assertEqual(a._fund_cash_balance(self.c,self.f,'2026-12-31'),2000)
    def test_capacity_suspension_does_not_disable_cash(self):
        self.mapping();self.change('capacity',False)
        self.reject_atomic(lambda:self.post(self.exp,self.bank,10001))
    def test_cash_suspension_does_not_disable_capacity(self):
        self.mapping();self.change('cash',False)
        self.reject_atomic(lambda:self.post(self.exp,self.bank,10001))
    def test_both_suspensions_record_usage_links(self):
        self.mapping();self.change('cash',False);self.change('capacity',False)
        entry=self.post(self.exp,self.bank,12000)
        uses=[r for r in self.audit() if r['event']=='entry_while_suspended']
        self.assertEqual({r['constraint_name'] for r in uses},{'cash','capacity'})
        self.assertTrue(all(r['actor']=='admin-fixture' and r['journal_entry_id']==entry['entry_id'] for r in uses))
    def test_cannot_reinstate_cash_while_still_negative(self):
        self.mapping();self.change('cash',False);self.post(self.other,self.bank,12000)
        self.reject_atomic(lambda:self.change('cash',True))
    def test_cannot_reinstate_capacity_while_still_negative(self):
        self.change('capacity',False);self.post(self.exp,self.bank,12000)
        self.reject_atomic(lambda:self.change('capacity',True))
    def test_reinstate_after_repair_keeps_exception_history(self):
        self.mapping();self.change('cash',False);self.change('capacity',False)
        self.post(self.exp,self.bank,12000)
        self.post(self.bank,self.income,5000,'2026-03-01')
        self.change('cash',True);self.change('capacity',True)
        state=a.fund_controls(self.c,self.f)
        self.assertTrue(state['cash_enabled'] and state['capacity_enabled'])
        self.assertTrue(any(r['event']=='entry_while_suspended' for r in self.audit()))
        self.reject_atomic(lambda:self.post(self.exp,self.bank,4000,'2026-04-01'))
    def test_replay_has_no_duplicate_audit(self):
        self.mapping();first=self.change('cash',False,key='repeat');n=len(self.audit())
        second=self.change('cash',False,key='repeat')
        self.assertEqual(first,second);self.assertEqual(len(self.audit()),n)
    def test_replay_with_changed_reason_conflicts(self):
        self.change('capacity',False,key='repeat')
        self.reject_atomic(lambda:self.change('capacity',False,key='repeat',reason='Different'),a.IdempotencyConflict)
    def test_actor_and_reason_required(self):
        for actor,reason in [('', 'Reason'),('admin',''),('  ','Reason')]:
            self.reject_atomic(lambda:self.change('capacity',False,actor=actor,reason=reason),ValueError)
    def test_audit_is_append_only(self):
        self.change('capacity',False)
        for sql in ['DELETE FROM acct_fund_constraint_audit','UPDATE acct_fund_constraint_audit SET actor=\'other\'']:
            with self.assertRaises(sqlite3.IntegrityError):self.c.execute(sql)
    def test_cash_mapping_is_immutable(self):
        self.mapping()
        with self.assertRaises(sqlite3.IntegrityError):self.c.execute('DELETE FROM acct_fund_cash_accounts')
        self.reject_atomic(lambda:self.mapping([self.other],key='map2'))
    def test_non_cash_classes_are_rejected(self):
        ar=a.account_by_code(self.c,'1100')['account_id']
        for aid in [ar,self.cap,self.exp]:
            self.reject_atomic(lambda:self.mapping([aid],key='bad'+str(aid)))
    def test_historical_negative_cash_requires_explicit_review(self):
        self.post(self.other,self.bank,12000)
        self.reject_atomic(lambda:self.mapping())
        self.assertFalse(a.fund_controls(self.c,self.f)['cash_configured'])
        self.change('cash',False);self.mapping()
        self.assertTrue(a.fund_controls(self.c,self.f)['cash_configured'])
    def test_reinstate_checks_future_deficits_too(self):
        self.mapping();self.change('cash',False)
        self.post(self.other,self.bank,12000,'2027-01-01')
        self.post(self.bank,self.income,5000,'2027-02-01')
        self.reject_atomic(lambda:self.change('cash',True))
    def test_constraint_cannot_disable_permanent_donor_rule(self):
        with self.assertRaises(ValueError):self.change('permanent_restriction',False)
    def test_noop_state_change_is_not_new_authority(self):
        self.reject_atomic(lambda:self.change('cash',True))
    def test_cash_mapping_cannot_be_designated_capital(self):
        self.mapping([self.other]);self.reject_atomic(lambda:a.designate_capital_account(self.c,'badcap',self.other))
    def test_idempotent_mapping_returns_original_result(self):
        first=self.mapping();self.assertEqual(self.mapping(),first)
        self.assertEqual(len([r for r in self.audit() if r['event']=='configure_cash']),1)

    def test_suspended_capacity_applies_to_release_precheck(self):
        self.change('capacity',False)
        entry=a.release_from_restriction(self.c,'release',self.f,12000,'2026-02-01','Purpose met',self.r,self.u,self.clear)
        self.assertEqual(a.fund(self.c,self.f,as_of='2026-12-31')['available_cents'],-2000)
        self.assertTrue(any(r['journal_entry_id']==entry['entry_id'] for r in self.audit()))
    def test_new_schema_keeps_existing_funds_unconfigured(self):
        self.assertEqual(a.schema_version(self.c),6)
        a.verify_schema(self.c)
        self.assertEqual(a.fund(self.c,self.f,as_of='2026-12-31')['available_cents'],10000)
        self.assertEqual(self.audit(),[])
    def test_database_cannot_map_capital_as_cash(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('INSERT INTO acct_fund_cash_accounts VALUES (?,?)',(self.f,self.cap))
    def test_database_cannot_classify_cash_as_capital(self):
        self.mapping([self.other])
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('INSERT INTO acct_fund_capital_accounts VALUES (?)',(self.other,))

    def test_v5_upgrade_preserves_book_and_backups(self):
        import tempfile
        from test_ledger import chart
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'upgrade.db')
            c=a.connect(path)
            try:
                a._migrate.migrate(c,'accounting',a.MIGRATIONS,target=5)
                chart(c)
                f=a.add_fund(c,'f','OLD','Old fund','temporarily_restricted')['fund_id']
                bank=a.account_by_code(c,'1000')['account_id']
                revenue=a.account_by_code(c,'4000')['account_id']
                a.record_grant_receipt(c,'grant',f,'2026-01-01',10000,bank,revenue,'Old grant')
                out=a.init_db(c)
                self.assertEqual(out['applied'],[6]);self.assertTrue(Path(out['backup']).is_file())
                self.assertEqual(a.fund(c,f,as_of='2026-12-31')['available_cents'],10000)
                self.assertFalse(a.fund_controls(c,f)['cash_enforced'])
                self.assertEqual(a.fund_constraint_audit(c,f),[])
                a.verify_schema(c)
            finally:c.close()
    def test_other_fund_cannot_supply_mapped_cash(self):
        self.mapping()
        f2=a.add_fund(self.c,'f2','F2','Other fund','temporarily_restricted')['fund_id']
        a.record_grant_receipt(self.c,'g2',f2,'2026-01-01',50000,self.bank,self.income,'Other grant')
        self.reject_atomic(lambda:self.post(self.other,self.bank,10001))

if __name__=='__main__':unittest.main()

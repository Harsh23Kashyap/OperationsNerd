"""Installer-only session management on disposable books."""
import secrets
from pathlib import Path
import sys
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import control_auth as auth
from test_fund_spendable_regressions import setup

class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.conn,*_=setup(mapped=False)
        self.addCleanup(self.conn.close)
        self.password=secrets.token_urlsafe(24)
        self.admin=auth.provision(self.conn,'admin',self.password)
        self.viewer=auth.provision(self.conn,'viewer',self.password,'viewer')
    def login(self,user='admin',now=100):return auth.login(self.conn,user,self.password,now)['token']
    def test_status_never_contains_hashes_or_tokens(self):
        token=self.login()
        row=auth.identity_status(self.conn,'admin',now=101)
        self.assertEqual(set(row),{'id','username','role','active','failed_attempts','locked_until','active_sessions'})
        self.assertNotIn(token,str(row));self.assertNotIn(self.password,str(row))
    def test_status_counts_only_unexpired_sessions(self):
        self.login(now=100);self.login(now=300)
        self.assertEqual(auth.identity_status(self.conn,'admin',now=3700)['active_sessions'],1)
    def test_status_normalizes_username(self):
        self.assertEqual(auth.identity_status(self.conn,' ADMIN ',now=100)['id'],self.admin)
    def test_status_unknown_does_not_create_user(self):
        with self.assertRaisesRegex(ValueError,'Unknown local control identity'):
            auth.identity_status(self.conn,'unknown')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM acct_control_users').fetchone()[0],2)
    def test_revoke_returns_number_deleted(self):
        self.login();self.login()
        self.assertEqual(auth.revoke_sessions(self.conn,'admin'),2)
    def test_revoked_tokens_no_longer_authenticate(self):
        tokens=[self.login(),self.login()]
        auth.revoke_sessions(self.conn,'admin')
        for token in tokens:
            with self.assertRaises(auth.Unauthorized):auth.principal(self.conn,token,101)
    def test_revoke_preserves_other_user_session(self):
        token=self.login('viewer');self.login()
        auth.revoke_sessions(self.conn,'admin')
        self.assertEqual(auth.principal(self.conn,token,101)['id'],self.viewer)
    def test_revoke_preserves_password_and_allows_new_login(self):
        before=self.conn.execute('SELECT password_hash FROM acct_control_users WHERE id=?',(self.admin,)).fetchone()[0]
        self.login();auth.revoke_sessions(self.conn,'admin')
        self.assertEqual(before,self.conn.execute('SELECT password_hash FROM acct_control_users WHERE id=?',(self.admin,)).fetchone()[0])
        self.assertTrue(self.login())
    def test_revoke_is_repeatable(self):
        self.login();self.assertEqual(auth.revoke_sessions(self.conn,'admin'),1)
        self.assertEqual(auth.revoke_sessions(self.conn,'admin'),0)
    def test_unknown_revoke_leaves_existing_session(self):
        token=self.login()
        with self.assertRaises(ValueError):auth.revoke_sessions(self.conn,'missing')
        self.assertEqual(auth.principal(self.conn,token,101)['id'],self.admin)
    def test_disable_preserves_password_and_revokes_sessions(self):
        token=self.login()
        before=self.conn.execute('SELECT password_hash FROM acct_control_users WHERE id=?',(self.admin,)).fetchone()[0]
        self.assertEqual(auth.disable_identity(self.conn,'admin'),1)
        self.assertEqual(before,self.conn.execute('SELECT password_hash FROM acct_control_users WHERE id=?',(self.admin,)).fetchone()[0])
        with self.assertRaises(auth.Unauthorized):auth.principal(self.conn,token,101)
        with self.assertRaises(auth.Unauthorized):self.login()
    def test_disable_preserves_other_identity(self):
        token=self.login('viewer');auth.disable_identity(self.conn,'admin')
        self.assertEqual(auth.principal(self.conn,token,101)['id'],self.viewer)
    def test_disable_is_repeatable(self):
        self.login();self.assertEqual(auth.disable_identity(self.conn,'admin'),1)
        self.assertEqual(auth.disable_identity(self.conn,'admin'),0)
        self.assertFalse(auth.identity_status(self.conn,'admin',now=101)['active'])
    def test_disable_unknown_leaves_identity_active(self):
        with self.assertRaises(ValueError):auth.disable_identity(self.conn,'missing')
        self.assertTrue(auth.identity_status(self.conn,'admin',now=101)['active'])
    def test_invalid_username_does_not_change_any_identity(self):
        for name in ('',None,' ' * 2,'a'*161):
            with self.assertRaises(ValueError):auth.disable_identity(self.conn,name)
        self.assertTrue(auth.identity_status(self.conn,'admin',now=101)['active'])
    def test_reprovision_can_restore_disabled_identity(self):
        auth.disable_identity(self.conn,'admin')
        auth.provision(self.conn,'admin',self.password)
        self.assertTrue(self.login())
    def test_revoke_does_not_reset_lockout(self):
        self.conn.execute('UPDATE acct_control_users SET failed_attempts=5,locked_until=999 WHERE id=?',(self.admin,))
        auth.revoke_sessions(self.conn,'admin')
        row=auth.identity_status(self.conn,'admin',now=101)
        self.assertEqual((row['failed_attempts'],row['locked_until']),(5,999))
    def test_database_rollback_error_is_not_masked(self):
        self.login()
        self.conn.execute("CREATE TRIGGER reject_session_delete BEFORE DELETE ON acct_control_sessions BEGIN SELECT RAISE(ROLLBACK,'original rollback fixture'); END")
        with self.assertRaisesRegex(Exception,'original rollback fixture'):
            auth.disable_identity(self.conn,'admin')
        self.assertTrue(auth.identity_status(self.conn,'admin',now=101)['active'])
    def test_disabling_viewer_does_not_change_admin_role(self):
        auth.disable_identity(self.conn,'viewer')
        row=auth.identity_status(self.conn,'admin',now=101)
        self.assertEqual(row['role'],'admin');self.assertTrue(row['active'])
    def test_disable_rolls_back_if_session_deletion_fails(self):
        self.login()
        self.conn.execute("CREATE TRIGGER reject_session_delete BEFORE DELETE ON acct_control_sessions BEGIN SELECT RAISE(ABORT,'offline failure'); END")
        with self.assertRaises(Exception):auth.disable_identity(self.conn,'admin')
        self.assertTrue(auth.identity_status(self.conn,'admin',now=101)['active'])
        self.assertFalse(self.conn.in_transaction)

if __name__=='__main__':unittest.main()

"""Exercise the installer CLI against disposable current-schema book files."""
import contextlib
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import accounting
import control_auth
import manage_admin

class CLITests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=os.path.join(temp.name,'books.db')
        self.conn=accounting.connect(self.path);self.addCleanup(self.conn.close)
        accounting.init_db(self.conn)
        self.password=secrets.token_urlsafe(24)
        control_auth.provision(self.conn,'admin',self.password)
    def run_cli(self,action,username='admin'):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            manage_admin.main(['--database',self.path,'--username',username,action])
        return json.loads(output.getvalue())
    def test_status_output_is_sanitized_json(self):
        result=self.run_cli('status')
        self.assertEqual(result['username'],'admin')
        self.assertNotIn('password',str(result));self.assertNotIn('token',str(result))
    def test_status_does_not_modify_book(self):
        before='\n'.join(self.conn.iterdump())
        self.run_cli('status')
        self.assertEqual('\n'.join(self.conn.iterdump()),before)
    def test_revoke_cli_affects_existing_sessions(self):
        token=control_auth.login(self.conn,'admin',self.password)['token']
        self.assertEqual(self.run_cli('revoke-sessions'),{'action':'revoke-sessions','sessions_revoked':1})
        with self.assertRaises(control_auth.Unauthorized):control_auth.principal(self.conn,token)
    def test_disable_cli_denies_future_login(self):
        self.run_cli('disable')
        with self.assertRaises(control_auth.Unauthorized):control_auth.login(self.conn,'admin',self.password)
    def test_missing_file_not_created(self):
        missing=self.path+'.missing'
        with self.assertRaises(sqlite3.DatabaseError):
            manage_admin.main(['--database',missing,'--username','admin','status'])
        self.assertFalse(os.path.exists(missing))
    def test_unknown_action_never_opens_database(self):
        with patch.object(manage_admin.accounting,'connect',side_effect=AssertionError('must not open')):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                self.run_cli('enable')
    def test_no_password_prompt_for_any_action(self):
        with patch('getpass.getpass',side_effect=AssertionError('no secret needed')):
            for action in ('status','revoke-sessions','disable'):self.run_cli(action)
    def test_unknown_user_has_no_side_effect(self):
        before='\n'.join(self.conn.iterdump())
        with self.assertRaisesRegex(ValueError,'Unknown local control identity'):
            self.run_cli('disable','missing')
        self.assertEqual('\n'.join(self.conn.iterdump()),before)
    def test_schema_failure_closes_connection(self):
        with patch.object(manage_admin.accounting,'verify_schema',side_effect=accounting.SchemaTampered('fixture')):
            with self.assertRaises(accounting.SchemaTampered):self.run_cli('disable')
        self.assertTrue(control_auth.identity_status(self.conn,'admin')['active'])
        self.conn.execute('BEGIN EXCLUSIVE');self.conn.execute('ROLLBACK')
    def test_old_schema_requires_separate_upgrade(self):
        with patch.object(manage_admin.accounting,'schema_version',return_value=6):
            with self.assertRaisesRegex(ValueError,'Upgrade the local book separately'):
                self.run_cli('disable')
        self.assertTrue(control_auth.identity_status(self.conn,'admin')['active'])
    def test_cli_does_not_migrate(self):
        with patch.object(manage_admin.accounting,'init_db',side_effect=AssertionError('no migrations')):
            self.run_cli('status');self.run_cli('revoke-sessions')

if __name__=='__main__':unittest.main()

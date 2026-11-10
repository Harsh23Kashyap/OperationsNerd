"""Installer identities and local HTTP controls on disposable books."""
import hashlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import api
from test_fund_spendable_regressions import setup
from fastapi import FastAPI
from fastapi.testclient import TestClient

PASSWORD = 'fixture-only-long-password'


class ControlAuthTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.fund, self.bank, *_ = setup(mapped=False)
        path = self.conn.execute('PRAGMA database_list').fetchone()[2]
        self.conn.close()
        self.conn = api.accounting.connect(path, check_same_thread=False)
        self.auth = api.control_auth
        self.uid = self.auth.provision(self.conn, 'Installer.Admin', PASSWORD)
        self.auth.provision(self.conn, 'viewer', PASSWORD, 'viewer')
        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[api.get_conn] = lambda: self.conn
        self.client = TestClient(app)
        self.env = patch.dict(os.environ, {'APP_ENV': 'development'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.conn.close)

    def token(self, username='installer.admin'):
        return self.auth.login(self.conn, username, PASSWORD)['token']

    def headers(self, token=None):
        return {'Authorization': 'Bearer ' + (token or self.token()), 'Idempotency-Key': 'control-test'}

    def test_password_boundaries(self):
        for password in ('x' * 11, 'x' * 257):
            with self.assertRaises(ValueError):
                self.auth.provision(self.conn, 'boundary', password)
        for password in ('x' * 12, 'x' * 256):
            self.auth.provision(self.conn, 'boundary', password)
            self.assertTrue(self.auth.login(self.conn, 'boundary', password)['token'])

    def test_invalid_role_and_active_flag_rejected(self):
        for role, active in (('owner', True), ('admin', 1)):
            with self.assertRaises(ValueError):
                self.auth.provision(self.conn, 'invalid', PASSWORD, role, active)

    def test_disabled_existing_session_is_rejected(self):
        token = self.token()
        self.conn.execute('UPDATE acct_control_users SET active=0 WHERE id=?', (self.uid,))
        with self.assertRaises(self.auth.Unauthorized):
            self.auth.principal(self.conn, token)

    def test_token_is_bound_to_database(self):
        token = self.token()
        other = api.accounting.connect(':memory:')
        try:
            api.accounting.init_db(other)
            self.auth.provision(other, 'installer.admin', PASSWORD)
            with self.assertRaises(self.auth.Unauthorized):
                self.auth.principal(other, token)
        finally:
            other.close()

    def test_second_login_session_survives_first_logout(self):
        first, second = self.token(), self.token()
        self.auth.logout(self.conn, first)
        self.assertEqual(self.auth.principal(self.conn, second)['id'], self.uid)

    def test_login_database_failure_is_503_without_password(self):
        with patch.object(self.auth, 'login', side_effect=api.sqlite3.OperationalError('fixture locked')):
            response = self.client.post('/accounting/controls/login', json={'username':'installer.admin','password':PASSWORD})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(PASSWORD, response.text)

    def test_production_guard_runs_before_database_dependency(self):
        self.client.app.dependency_overrides[api.get_conn] = lambda: (_ for _ in ()).throw(AssertionError('must not open DB'))
        with patch.dict(os.environ, {'APP_ENV': 'production'}):
            for method, path in (('GET',f'/accounting/funds/{self.fund}/controls'),('POST','/accounting/controls/login')):
                self.assertEqual(self.client.request(method,path,json={'username':'installer.admin','password':PASSWORD}).status_code,404)

    def test_password_is_salted_not_plaintext(self):
        row = self.conn.execute('SELECT password_hash FROM acct_control_users WHERE id=?', (self.uid,)).fetchone()
        self.assertNotIn(PASSWORD, row[0])
        old = row[0]
        self.auth.provision(self.conn, 'installer.admin', PASSWORD)
        self.assertNotEqual(old, self.conn.execute('SELECT password_hash FROM acct_control_users WHERE id=?', (self.uid,)).fetchone()[0])

    def test_token_hash_only_is_stored(self):
        token = self.token()
        value = self.conn.execute('SELECT token_hash FROM acct_control_sessions').fetchone()[0]
        self.assertNotEqual(token, value)
        self.assertEqual(value, hashlib.sha256(token.encode()).hexdigest())

    def test_normalized_installer_identity(self):
        self.assertEqual(self.auth.principal(self.conn, self.token(' INSTALLER.ADMIN '))['id'], self.uid)

    def test_unknown_user_has_generic_error(self):
        with self.assertRaisesRegex(self.auth.Unauthorized, 'Invalid credentials'):
            self.auth.login(self.conn, 'unknown', PASSWORD)

    def test_wrong_password_has_generic_error(self):
        with self.assertRaises(self.auth.Unauthorized):
            self.auth.login(self.conn, 'installer.admin', 'incorrect-fixture-password')

    def test_lockout_survives_new_login_calls(self):
        for _ in range(5):
            with self.assertRaises(self.auth.Unauthorized):
                self.auth.login(self.conn, 'installer.admin', 'incorrect-fixture-password', now=100)
        with self.assertRaises(self.auth.Unauthorized):
            self.auth.login(self.conn, 'installer.admin', PASSWORD, now=101)
        self.assertTrue(self.auth.login(self.conn, 'installer.admin', PASSWORD, now=401)['token'])

    def test_expiry_boundary_rejected(self):
        result = self.auth.login(self.conn, 'installer.admin', PASSWORD, now=100)
        self.assertTrue(self.auth.principal(self.conn, result['token'], now=result['expires_at'] - 1))
        with self.assertRaises(self.auth.Unauthorized):
            self.auth.principal(self.conn, result['token'], now=result['expires_at'])

    def test_logout_revokes_token(self):
        token = self.token()
        self.auth.logout(self.conn, token)
        with self.assertRaises(self.auth.Unauthorized):
            self.auth.principal(self.conn, token)

    def test_reprovision_revokes_all_sessions(self):
        first, second = self.token(), self.token()
        self.auth.provision(self.conn, 'installer.admin', PASSWORD, 'viewer')
        for token in (first, second):
            with self.assertRaises(self.auth.Unauthorized):
                self.auth.principal(self.conn, token)

    def test_disabled_identity_cannot_login(self):
        self.auth.provision(self.conn, 'installer.admin', PASSWORD, active=False)
        with self.assertRaises(self.auth.Unauthorized):
            self.token()

    def test_missing_auth_denied(self):
        self.assertEqual(self.client.get(f'/accounting/funds/{self.fund}/controls').status_code, 401)

    def test_rejected_login_never_echoes_password(self):
        for body in ({'username': 'admin', 'password': 'secret-short'},
                     {'username': 'admin', 'password': PASSWORD, 'extra': 'private-input'},
                     {'username': 99, 'password': PASSWORD}):
            with self.subTest(body=list(body)):
                r = self.client.post('/accounting/controls/login', json=body)
                self.assertEqual(r.status_code, 401)
                self.assertNotIn(body['password'], r.text)
                self.assertNotIn('private-input', r.text)

    def test_malformed_login_never_returns_request_body(self):
        r = self.client.post('/accounting/controls/login', content='{"password":"fixture-secret"', headers={'Content-Type': 'application/json'})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn('fixture-secret', r.text)

    def test_viewer_can_review_controls_and_audit(self):
        headers = self.headers(self.token('viewer'))
        for suffix in ('', '/audit'):
            self.assertEqual(self.client.get(f'/accounting/funds/{self.fund}/controls'+suffix, headers=headers).status_code, 200)

    def test_viewer_cannot_control_fund(self):
        r = self.client.post(f'/accounting/funds/{self.fund}/controls/cash', json={'enabled': False, 'reason': 'fixture'}, headers=self.headers(self.token('viewer')))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(api.accounting.fund_constraint_audit(self.conn, self.fund), [])

    def test_authenticated_mapping_and_identity_audit(self):
        r = self.client.post(f'/accounting/funds/{self.fund}/controls/cash-accounts', json={'account_ids': [self.bank], 'reason': 'Reviewed fixture mapping'}, headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        audit = api.accounting.fund_constraint_audit(self.conn, self.fund)
        self.assertEqual(audit[-1]['actor'], f'control-user:{self.uid}:installer.admin')

    def test_actor_spoof_rejected(self):
        r = self.client.post(f'/accounting/funds/{self.fund}/controls/cash', json={'enabled': False, 'reason': 'fixture', 'admin_actor': 'someone-else'}, headers=self.headers())
        self.assertEqual(r.status_code, 422)

    def test_idempotent_suspension(self):
        headers = self.headers()
        url = f'/accounting/funds/{self.fund}/controls/cash'
        for _ in range(2):
            r = self.client.post(url, json={'enabled': False, 'reason': 'Reviewed exception'}, headers=headers)
            self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(api.accounting.fund_constraint_audit(self.conn, self.fund)), 1)

    def test_missing_idempotency_key_rejected(self):
        r = self.client.post(f'/accounting/funds/{self.fund}/controls/cash', json={'enabled': False, 'reason': 'fixture'}, headers={'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(r.status_code, 422)

    def test_production_control_routes_closed(self):
        with patch.dict(os.environ, {'APP_ENV': 'production'}):
            self.assertEqual(self.client.get(f'/accounting/funds/{self.fund}/controls', headers=self.headers()).status_code, 404)
            self.assertEqual(self.client.post('/accounting/controls/login', json={'username': 'installer.admin', 'password': PASSWORD}).status_code, 404)

    def test_stolen_or_invented_token_rejected(self):
        self.assertEqual(self.client.get(f'/accounting/funds/{self.fund}/controls', headers=self.headers('invented-token')).status_code, 401)

    def test_reinstatement_still_requires_cash_mapping(self):
        headers = self.headers()
        url = f'/accounting/funds/{self.fund}/controls/cash'
        self.assertEqual(self.client.post(url, json={'enabled': False, 'reason': 'Reviewed exception'}, headers=headers).status_code, 200)
        headers['Idempotency-Key'] = 'reinstate-unmapped'
        self.assertEqual(self.client.post(url, json={'enabled': True, 'reason': 'Cannot bypass mapping'}, headers=headers).status_code, 409)

    def test_schema_detects_auth_table_tampering(self):
        self.conn.execute('DROP TABLE acct_control_sessions')
        with self.assertRaises(api.accounting.SchemaTampered):
            api.accounting.verify_schema(self.conn)

    def test_no_http_provisioning_endpoint(self):
        self.assertEqual(self.client.post('/accounting/controls/users', json={}).status_code, 404)

    def test_cookie_alone_cannot_authorize_control_change(self):
        self.client.cookies.set('session', self.token())
        self.assertEqual(self.client.get(f'/accounting/funds/{self.fund}/controls').status_code, 401)

    def test_login_response_not_cached_and_logout_works(self):
        r = self.client.post('/accounting/controls/login', json={'username': 'installer.admin', 'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers['cache-control'], 'no-store')
        headers = self.headers(r.json()['token'])
        self.assertEqual(self.client.post('/accounting/controls/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f'/accounting/funds/{self.fund}/controls', headers=headers).status_code, 401)


if __name__ == '__main__':
    unittest.main()

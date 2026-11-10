"""Installer-managed local control identities. Not core CRM/business auth."""
import hashlib
import hmac
import secrets
import time

SESSION_SECONDS = 3600
LOCK_SECONDS = 300
MAX_FAILURES = 5


class Unauthorized(ValueError):
    pass


def _username(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError('Enter a username of 1-160 characters')
    return value.strip().lower()


def _password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 256:
        raise ValueError('Password must contain 12-256 characters')
    return value.encode('utf-8')


def _derive(password, salt):
    return hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1).hex()


def provision(conn, username, password, role='admin', active=True):
    """Installer-only function. Re-provisioning revokes all existing sessions."""
    username = _username(username)
    password = _password(password)
    if role not in ('admin', 'viewer') or type(active) is not bool:
        raise ValueError('Invalid role or active flag')
    salt = secrets.token_bytes(16)
    encoded = salt.hex() + ':' + _derive(password, salt)
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('''INSERT INTO acct_control_users(username,password_hash,role,active)
            VALUES (?,?,?,?) ON CONFLICT(username) DO UPDATE SET
            password_hash=excluded.password_hash,role=excluded.role,active=excluded.active,
            failed_attempts=0,locked_until=0''', (username, encoded, role, int(active)))
        uid = conn.execute('SELECT id FROM acct_control_users WHERE username=?', (username,)).fetchone()[0]
        conn.execute('DELETE FROM acct_control_sessions WHERE user_id=?', (uid,))
        conn.execute('COMMIT')
        return uid
    except BaseException:
        conn.execute('ROLLBACK')
        raise


def login(conn, username, password, now=None):
    now = int(time.time() if now is None else now)
    try:
        username = _username(username)
        password = _password(password)
    except ValueError:
        raise Unauthorized('Invalid credentials') from None
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute('SELECT * FROM acct_control_users WHERE username=?', (username,)).fetchone()
        # Do the same password work for unknown, disabled and locked identities.
        encoded = row['password_hash'] if row else '00' * 16 + ':' + '00' * 64
        try:
            salt, expected = encoded.split(':', 1)
            valid = hmac.compare_digest(_derive(password, bytes.fromhex(salt)), expected)
        except (ValueError, TypeError):
            valid = False
        if not row or not row['active'] or row['locked_until'] > now or not valid:
            if row and row['locked_until'] <= now:
                attempts = row['failed_attempts'] + 1 if not row['locked_until'] else 1
                conn.execute('UPDATE acct_control_users SET failed_attempts=?,locked_until=? WHERE id=?',
                             (attempts, now + LOCK_SECONDS if attempts >= MAX_FAILURES else 0, row['id']))
            conn.execute('COMMIT')
            raise Unauthorized('Invalid credentials')
        conn.execute('UPDATE acct_control_users SET failed_attempts=0,locked_until=0 WHERE id=?', (row['id'],))
        conn.execute('DELETE FROM acct_control_sessions WHERE expires_at<=?', (now,))
        token = secrets.token_urlsafe(32)
        conn.execute('INSERT INTO acct_control_sessions VALUES (?,?,?)',
                     (hashlib.sha256(token.encode()).hexdigest(), row['id'], now + SESSION_SECONDS))
        conn.execute('COMMIT')
        return {'token': token, 'expires_at': now + SESSION_SECONDS}
    except BaseException:
        if conn.in_transaction:
            conn.execute('ROLLBACK')
        raise


def principal(conn, token, now=None):
    now = int(time.time() if now is None else now)
    if not isinstance(token, str) or not token or len(token) > 256:
        raise Unauthorized('Login required')
    row = conn.execute('''SELECT u.id,u.username,u.role,u.active,s.expires_at FROM acct_control_sessions s
        JOIN acct_control_users u ON u.id=s.user_id WHERE s.token_hash=?''',
        (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row or not row['active'] or row['expires_at'] <= now:
        raise Unauthorized('Login required')
    return {'id': row['id'], 'username': row['username'], 'role': row['role']}


def logout(conn, token):
    if isinstance(token, str):
        conn.execute('DELETE FROM acct_control_sessions WHERE token_hash=?',
                     (hashlib.sha256(token.encode()).hexdigest(),))

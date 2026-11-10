-- Installer-managed identities for the local accounting control surface only.
CREATE TABLE acct_control_users (
 id INTEGER PRIMARY KEY,
 username TEXT NOT NULL UNIQUE CHECK(length(username) BETWEEN 1 AND 160),
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','viewer')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts >= 0),
 locked_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE acct_control_sessions (
 token_hash TEXT PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES acct_control_users(id),
 expires_at INTEGER NOT NULL
);
CREATE INDEX acct_control_sessions_user ON acct_control_sessions(user_id);

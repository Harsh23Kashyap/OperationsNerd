# Installer-only identity status and session revocation

The local accounting control login has installer-managed administrators/viewers. Its original provisioning command can disable a user, but asks for a replacement password and overwrites the stored credential. This separate management tool lets a trusted installer inspect one identity, revoke its sessions or disable it without collecting or rotating a password.

From `modules/accounting`:

```sh
python manage_admin.py --database /path/to/current-books.db --username local-admin status
python manage_admin.py --database /path/to/current-books.db --username local-admin revoke-sessions
python manage_admin.py --database /path/to/current-books.db --username local-admin disable
```

`status` emits JSON containing ID, username, role, active flag, failed-attempt count, lockout expiry and unexpired session count. It never selects password/token hashes or prints credentials. Status does not alter logical book records. The existing connection routine can use SQLite's WAL files, so this is not a promise of no filesystem activity.

`revoke-sessions` deletes all saved sessions for that identity, preserving its password, role, active flag and login lockout. A still-enabled identity can log in again. `disable` marks that identity inactive and deletes its sessions in one transaction. Other identities and their sessions are untouched. Repeating either operation is safe; the result reports how many sessions were removed. Unknown identities fail rather than being created. A failed session deletion rolls back the disable. The installer may disable the last administrator for containment; recovery is through the existing `install_admin.py` provisioning/password-reset operation, not a new passwordless enable action.

These operations affect subsequent authentication checks. They do not cancel requests already authorized/in flight, remove a token from a browser's memory, or replace credential rotation after compromise. Revoking sessions alone cannot stop someone who still knows a valid password from logging in again.

The command opens an existing book only, requires the current schema and verifies its schema rules. It does not create books or run migrations. Upgrade separately using the existing reviewed migration/backup procedure. Access to the file is the installer's trust boundary. There is no new HTTP route, automatic action against real users, stored secret, CRM mapping or per-business routing. Ordinary ledger routes remain unauthenticated development-only surfaces. The production gate stays in place.

Prepared on exact unmerged PR2 head `7f1a8badc316f6c56ff098e6436753147bee7dda` in Harsh23Kashyap/OperationsNerd. This is a fork-only stack on `feature/local-accounting-admin`; prior branches remain unchanged. Nothing is merged, deployed or changed upstream.

Offline checks on disposable SQLite books:
- 20 new lifecycle tests fail on the base because the management helpers are absent, and pass with them.
- 11 new CLI tests pass: sanitized/status JSON, unchanged logical records, revocation, disabling, missing-file refusal, action validation, no password prompts, unknown identity, schema-failure cleanup, explicit separate-upgrade requirement and no migrations.
- The 31 new tests plus 33 existing control-auth tests pass (64 total).
- All 17 accounting test scripts pass with external sockets blocked, including original fund/accounting/API/migration checks.

No production deployment, real identity/book mutation, live model/provider call, paid run, or claim that ordinary ledger authentication/business isolation is complete.

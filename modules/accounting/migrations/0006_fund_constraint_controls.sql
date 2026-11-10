-- Local accounting control prototype. Authentication remains the caller's job.
INSERT INTO acct_command_names(name) VALUES ('configure_fund_cash_accounts'), ('set_fund_constraint');
CREATE TABLE acct_fund_cash_accounts (
    fund_id INTEGER NOT NULL REFERENCES acct_funds(id),
    account_id INTEGER NOT NULL REFERENCES acct_accounts(id),
    PRIMARY KEY(fund_id, account_id)
);
CREATE TRIGGER acct_fund_cash_accounts_no_update BEFORE UPDATE ON acct_fund_cash_accounts
BEGIN SELECT RAISE(ABORT, 'cash mappings are immutable; reviewed migration required'); END;
CREATE TRIGGER acct_fund_cash_accounts_no_delete BEFORE DELETE ON acct_fund_cash_accounts
BEGIN SELECT RAISE(ABORT, 'cash mappings are immutable; reviewed migration required'); END;
CREATE TABLE acct_fund_constraint_audit (
    id INTEGER PRIMARY KEY,
    fund_id INTEGER NOT NULL REFERENCES acct_funds(id),
    constraint_name TEXT NOT NULL CHECK(constraint_name IN ('capacity','cash')),
    event TEXT NOT NULL CHECK(event IN ('configure_cash','suspend','reinstate','entry_while_suspended')),
    actor TEXT NOT NULL CHECK(trim(actor) <> ''),
    reason TEXT NOT NULL CHECK(trim(reason) <> ''),
    journal_entry_id INTEGER REFERENCES acct_journal_entries(id),
    details TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX acct_fund_constraint_audit_fund ON acct_fund_constraint_audit(fund_id, constraint_name, id);
CREATE TRIGGER acct_fund_constraint_audit_no_update BEFORE UPDATE ON acct_fund_constraint_audit
BEGIN SELECT RAISE(ABORT, 'fund constraint audit is append-only'); END;
CREATE TRIGGER acct_fund_constraint_audit_no_delete BEFORE DELETE ON acct_fund_constraint_audit
BEGIN SELECT RAISE(ABORT, 'fund constraint audit is append-only'); END;
CREATE TRIGGER acct_fund_cash_accounts_valid BEFORE INSERT ON acct_fund_cash_accounts
WHEN NOT EXISTS(SELECT 1 FROM acct_accounts WHERE id=NEW.account_id AND account_type='asset' AND role IS NULL AND is_active=1)
 OR NOT EXISTS(SELECT 1 FROM acct_funds WHERE id=NEW.fund_id AND restriction_type<>'unrestricted')
 OR EXISTS(SELECT 1 FROM acct_fund_capital_accounts WHERE account_id=NEW.account_id)
BEGIN SELECT RAISE(ABORT, 'cash mapping requires a restricted fund and a non-capital, non-control active asset'); END;
CREATE TRIGGER acct_fund_capital_accounts_not_cash BEFORE INSERT ON acct_fund_capital_accounts
WHEN EXISTS(SELECT 1 FROM acct_fund_cash_accounts WHERE account_id=NEW.account_id)
BEGIN SELECT RAISE(ABORT, 'cash and capital classifications cannot overlap'); END;

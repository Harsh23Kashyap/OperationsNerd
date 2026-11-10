# Restricted-fund controls: local prototype

This draft adds independent capacity and cash checks, plus audited suspension and reinstatement. It is not an authenticated administrator feature and is not ready to deploy.

## What is enforced

The existing capacity check stays on by default. For a restricted fund with an explicit reviewed cash-account mapping, the additional cash check prevents a reduction from making that fund's cash negative at the entry date or any later posted date. A receivable or other non-cash asset does not count as cash just because it is an asset. Same-date changes are netted at date end, as in PR1's existing capacity rule. There is no intraday guarantee or inferred entry order.

`fund_controls(conn, fund_id)` reports `capacity_enabled`, `cash_enabled`, `cash_configured`, `cash_enforced` and the mapped account IDs. Existing funds are not silently mapped. Until mapping is complete, `cash_enforced` is false even though the cash policy is enabled. That gap must be settled before claiming cash protection for a business.

`configure_fund_cash_accounts` requires a non-empty explicit set of active, non-control, non-capital asset accounts, plus an administrator attribution and reason. Mapping is one-time and immutable. A later chart change needs a reviewed migration, not removal of a cash account to hide a deficit. Existing mapped history is checked at each date end, unless the cash check was explicitly suspended first. No production books have been mapped by this draft.

## Suspension and reinstatement

`set_fund_constraint` accepts only `cash` or `capacity`. It changes one restricted fund's financial check, not balancing, journal immutability, permanent donor restrictions or another fund's checks. An actor and reason are required. Commands use the existing atomic transaction/idempotency mechanism.

The audit is append-only, with timestamp, fund, constraint, actor, reason, last journal-entry anchor and state change. Every entry posted while a check is suspended links to the suspension that allowed it. The recorded actor on those usage rows is the person attributed to the suspension, not a claimed identity for the person posting the entry.

Reinstatement requires non-negative balances today and at future posted dates. Earlier audited exceptions remain in the ledger. This is a proposed rule for Dennis to review, not a settled accounting policy. A later backdated reduction is still checked against its date and subsequent posted dates, including historical exception dates.

## Authentication boundary

These commands are local Python-domain functions requiring a trusted caller with a database connection. The `admin_actor` string is attribution, not authentication or proof of an administrator role. There is no HTTP control endpoint, no administrator UI and no new role model. The existing development accounting router is still disabled in production and still lacks business-level auth/routing. Its ordinary endpoints do not expose these control commands.

Before any authenticated adapter or deployment, agree:

1. Which bank/cash accounts each fund maps to, who reviews the mapping, and whether an unmapped fund should be blocked.
2. Which verified business administrators may suspend/reinstate, whether another approval is required, and what identity/reason must be kept.
3. Whether date-end netting is intended or an intraday sequence is required.

Migration 0006 adds cash mappings and the immutable audit through the existing backed-up migration mechanism. Migrations 0001-0005 and PR3's base are unchanged.

## Offline tests

```sh
python -B modules/accounting/tests/test_fund_constraint_controls.py -v
for f in modules/accounting/tests/test_*.py; do python -B "$f" || exit 1; done
```

All 32 new disposable-SQLite tests pass, including migration from version 5 with a backup, independent cash/capacity controls, cash/receivable separation, backdated and future deficits, same-day netting, per-fund isolation, idempotency, rollback and audit immutability. All 13 accounting test scripts pass. No model/provider calls, real business data or live accounting changes are involved. Local results do not establish production auth or deployment readiness.

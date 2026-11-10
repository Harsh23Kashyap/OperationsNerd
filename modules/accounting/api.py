"""
HTTP API for the accounting module: a FastAPI router over accounting.py's
commands, queries and reports.

Every POST and PATCH needs an Idempotency-Key header, which becomes the
command's idempotency key, so a client can retry safely. Errors map to status
codes: ValueError 422, NotFound 404, IdempotencyConflict / InvalidState /
Unbalanced and database rule violations 409; a database that is missing,
unreadable or whose schema was tampered with (accounting.SchemaTampered) 503.

Every amount is a whole number of cents, in and out, named *_cents, so nothing
in the wire format can round.

Every string field and the Idempotency-Key header have a length cap
(accounting.MAX_TEXT, or MAX_LONG_TEXT for notes, terms and memos), and a
journal entry, invoice or bill is capped at accounting.MAX_LINES lines;
accounting.py checks the same caps.

Each request checks the database before using it: a file that was set up before
and has since disappeared is a 503 (it is not quietly recreated empty), one
whose schema is behind this code is migrated, and verify_schema() runs every
time.

The database is the file named by ACCOUNTING_DATABASE_PATH. There is no
per-business database routing or ordinary ledger auth on main yet, so this router is a local
development surface only: main.py mounts it when ACCOUNTING_MODULE_ENABLED=1
and never when APP_ENV=production. Once the per-business database and ledger authorization
land, the router should take the signed-in business's connection instead (see
README).
"""

import importlib.util
import os
import sqlite3
import sys
import threading
from pathlib import Path as _FilePath
from typing import Annotated, Literal, Optional

from fastapi import Request, APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field


def _sibling(name: str, filename: str):
    """Loads a file from this folder by path, without putting the folder on sys.path.

    modules/hr/ has a migrate.py of its own and imports it by plain name, so adding this
    folder to sys.path would quietly hand HR our copy when both routers are mounted.
    Nothing here changes sys.path; accounting.py loads its own migrate.py the same way.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FilePath(__file__).resolve().with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


accounting = _sibling("accounting", "accounting.py")

router = APIRouter(prefix="/accounting", tags=["accounting"])

_ready: set[str] = set()
_ready_lock = threading.Lock()


def database_path() -> str:
    path = os.environ.get("ACCOUNTING_DATABASE_PATH")
    if not path:
        raise HTTPException(503, "ACCOUNTING_DATABASE_PATH is not set")
    return path


def get_conn():
    path = database_path()
    with _ready_lock:
        known = path in _ready
    try:
        # A database this process has already set up is opened without create, so a file that has been deleted
        # (or moved away) is a 503 until it is put back, instead of a new empty database.
        conn = accounting.connect(path, check_same_thread=False, create=not known)
    except sqlite3.DatabaseError as e:
        raise HTTPException(503, f"the accounting database can't be opened: {e}")
    try:
        _prepare(conn, path, known)
    except BaseException:
        conn.close()
        raise
    try:
        yield conn
    finally:
        conn.close()


def _prepare(conn, path, known):
    try:
        if not known or accounting.schema_version(conn) != accounting.latest_version():
            with _ready_lock:
                accounting.init_db(conn)             # migrates if needed, then verify_schema()
                _ready.add(path)
        else:
            accounting.verify_schema(conn)
    except accounting.SchemaTampered as e:
        raise HTTPException(503, f"SchemaTampered: {e}")
    except sqlite3.DatabaseError as e:
        raise HTTPException(503, f"the accounting database can't be used: {e}")


def _key(idempotency_key: str = Header(..., alias="Idempotency-Key", max_length=accounting.MAX_TEXT)) -> str:
    return idempotency_key


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except accounting.NotFound as e:
        raise HTTPException(404, str(e))
    except (accounting.IdempotencyConflict, accounting.InvalidState, accounting.Unbalanced,
            accounting.InsufficientFundBalance) as e:
        raise HTTPException(409, f"{type(e).__name__}: {e}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except OverflowError:
        # A number too big for SQLite's 64-bit integers that accounting.py didn't catch first.
        raise HTTPException(422, "a number in the request is too large")
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, f"rejected by the database: {e}")
    except accounting.SchemaTampered as e:
        raise HTTPException(503, f"SchemaTampered: {e}")
    except sqlite3.DatabaseError as e:
        # The file went away, became unreadable or stayed locked: not the client's fault, and not a crash.
        raise HTTPException(503, f"the accounting database can't be used: {e}")


class _Body(BaseModel):
    # strict: no silent coercion, so {"quantity": "3"} or {"account_id": true} is a 422 instead of
    # becoming 3 or account 1 (accounting.py refuses bools for ids and amounts too).
    model_config = ConfigDict(extra="forbid", strict=True)


Text = Annotated[str, Field(max_length=accounting.MAX_TEXT)]
LongText = Annotated[str, Field(max_length=accounting.MAX_LONG_TEXT)]
Date = Annotated[str, Field(max_length=10)]
AccountType = Literal["asset", "liability", "equity", "income", "expense"]
Status = Literal["draft", "sent", "paid", "void", "overdue"]
FundRestriction = Literal["unrestricted", "temporarily_restricted", "permanently_restricted"]


def _lines(models) -> list[dict]:
    """The line models as plain dicts with the fields that weren't given left out, so accounting.py sees
    exactly what a direct caller would pass (it refuses, for instance, both account_id and account_code)."""
    return [{k: v for k, v in model.model_dump().items() if v is not None} for model in models]


class NewAccount(_Body):
    code: Text
    name: Text
    account_type: AccountType
    role: Optional[Literal["accounts_receivable", "accounts_payable"]] = None
    notes: Optional[LongText] = None


class AccountChanges(_Body):
    changes: dict                                   # its values are checked (and capped) by update_account


class JournalLine(_Body):
    account_id: Optional[int] = None
    account_code: Optional[Text] = None
    debit_cents: Optional[int] = None
    credit_cents: Optional[int] = None
    fund_id: Optional[int] = None
    memo: Optional[LongText] = None


class NewJournalEntry(_Body):
    entry_date: Date
    memo: LongText
    lines: Annotated[list[JournalLine], Field(min_length=2, max_length=accounting.MAX_LINES)]
    source: Literal["manual", "payroll"] = "manual"
    source_ref: Optional[Text] = None


class Reversal(_Body):
    entry_date: Optional[Date] = None
    memo: Optional[LongText] = None


_NO_REVERSAL = Reversal()


class NewParty(_Body):
    name: Text
    email: Optional[Text] = None
    phone: Optional[Text] = None
    billing_address: Optional[Text] = None
    payment_terms_days: int = 0
    terms: Optional[LongText] = None
    notes: Optional[LongText] = None


class PartyChanges(_Body):
    changes: dict                                   # checked by update_customer / update_supplier


class DocumentLine(_Body):
    description: Text
    quantity: int = 1
    unit_price_cents: int
    account_id: Optional[int] = None
    account_code: Optional[Text] = None


class NewFund(_Body):
    code: Text
    name: Text
    restriction_type: FundRestriction
    purpose_description: Optional[LongText] = None
    grantor: Optional[Text] = None
    start_date: Optional[Date] = None
    end_date: Optional[Date] = None


class FundChanges(_Body):
    changes: dict


class GrantReceipt(_Body):
    fund_id: int
    entry_date: Date
    amount_cents: int
    cash_account_id: int
    revenue_account_id: int
    memo: LongText
    grantor: Optional[Text] = None
    source_ref: Optional[Text] = None


class FundRelease(_Body):
    from_fund_id: int
    amount_cents: int
    entry_date: Date
    memo: LongText
    donor_restricted_account_id: int
    unrestricted_net_assets_account_id: int
    clearing_account_id: int


class NewInvoice(_Body):
    customer_id: int
    invoice_number: Text
    issue_date: Date
    lines: Annotated[list[DocumentLine], Field(min_length=1, max_length=accounting.MAX_LINES)]
    due_date: Optional[Date] = None
    terms: Optional[LongText] = None
    notes: Optional[LongText] = None
    fund_id: Optional[int] = None


class NewBill(_Body):
    supplier_id: int
    bill_number: Text
    issue_date: Date
    lines: Annotated[list[DocumentLine], Field(min_length=1, max_length=accounting.MAX_LINES)]
    due_date: Optional[Date] = None
    terms: Optional[LongText] = None
    notes: Optional[LongText] = None
    fund_id: Optional[int] = None


class Posting(_Body):
    """Sending an invoice or receiving a bill: the date its entry carries, the document's own issue date
    by default."""
    entry_date: Optional[Date] = None


_NO_POSTING = Posting()


class NewPayment(_Body):
    amount_cents: int
    payment_date: Date
    account_id: int                                 # where the money arrived, or left from
    reference: Optional[Text] = None


class Void(_Body):
    reason: LongText
    entry_date: Optional[Date] = None


# ---------------------------------------------------------------- chart of accounts

@router.get("/accounts")
def list_accounts(account_type: Optional[AccountType] = None, active_only: bool = False,
                  conn=Depends(get_conn)):
    return _call(accounting.accounts, conn, account_type=account_type, active_only=active_only)


@router.post("/accounts", status_code=201)
def add_account(body: NewAccount, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.add_account, conn, key, body.code, body.name, body.account_type, role=body.role,
                 notes=body.notes)


@router.get("/accounts/{account_id}")
def get_account(account_id: int, conn=Depends(get_conn)):
    return _call(accounting.account, conn, account_id)


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountChanges, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.update_account, conn, account_id, body.changes, key)


@router.get("/accounts/{account_id}/balance")
def account_balance(account_id: int, as_of: Optional[str] = Query(None, max_length=10), conn=Depends(get_conn)):
    return _call(accounting.account_balance, conn, account_id, as_of=as_of)


# ---------------------------------------------------------------- funds

@router.post("/accounts/{account_id}/designate-capital")
def designate_capital_account(account_id: int, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.designate_capital_account, conn, key, account_id)


@router.get("/funds")
def list_funds(active_only: bool = False, as_of: Optional[str] = Query(None, max_length=10),
               conn=Depends(get_conn)):
    return _call(accounting.funds, conn, active_only=active_only, as_of=as_of)


@router.post("/funds", status_code=201)
def add_fund(body: NewFund, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.add_fund, conn, key, body.code, body.name, body.restriction_type,
                 purpose_description=body.purpose_description, grantor=body.grantor,
                 start_date=body.start_date, end_date=body.end_date)


@router.get("/funds/{fund_id}")
def get_fund(fund_id: int, as_of: Optional[str] = Query(None, max_length=10), conn=Depends(get_conn)):
    return _call(accounting.fund, conn, fund_id, as_of=as_of)


@router.patch("/funds/{fund_id}")
def update_fund(fund_id: int, body: FundChanges, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.update_fund, conn, fund_id, body.changes, key)


@router.post("/funds/grant-receipt", status_code=201)
def record_grant_receipt(body: GrantReceipt, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.record_grant_receipt, conn, key, body.fund_id, body.entry_date, body.amount_cents,
                 body.cash_account_id, body.revenue_account_id, body.memo, grantor=body.grantor,
                 source_ref=body.source_ref)


@router.post("/funds/release-from-restriction", status_code=201)
def release_from_restriction(body: FundRelease, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.release_from_restriction, conn, key, body.from_fund_id, body.amount_cents,
                 body.entry_date, body.memo, body.donor_restricted_account_id,
                 body.unrestricted_net_assets_account_id, body.clearing_account_id)


# ---------------------------------------------------------------- the journal

@router.get("/journal")
def list_journal(from_date: Optional[str] = Query(None, max_length=10),
                 to_date: Optional[str] = Query(None, max_length=10),
                 source: Optional[str] = Query(None, max_length=accounting.MAX_TEXT),
                 limit: int = Query(500, ge=1, le=10_000), conn=Depends(get_conn)):
    return _call(accounting.journal_entries, conn, from_date=from_date, to_date=to_date, source=source,
                 limit=limit)


@router.post("/journal", status_code=201)
def post_journal_entry(body: NewJournalEntry, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.post_journal_entry, conn, key, body.entry_date, body.memo, _lines(body.lines),
                 source=body.source, source_ref=body.source_ref)


@router.get("/journal/{entry_id}")
def get_journal_entry(entry_id: int, conn=Depends(get_conn)):
    return _call(accounting.journal_entry, conn, entry_id)


@router.post("/journal/{entry_id}/reverse", status_code=201)
def reverse_journal_entry(entry_id: int, body: Optional[Reversal] = None, key: str = Depends(_key),
                          conn=Depends(get_conn)):
    body = body or _NO_REVERSAL
    return _call(accounting.reverse_journal_entry, conn, entry_id, key, entry_date=body.entry_date,
                 memo=body.memo)


# ---------------------------------------------------------------- customers and suppliers

@router.get("/customers")
def list_customers(active_only: bool = False, conn=Depends(get_conn)):
    return _call(accounting.customers, conn, active_only=active_only)


@router.post("/customers", status_code=201)
def add_customer(body: NewParty, key: str = Depends(_key), conn=Depends(get_conn)):
    fields = body.model_dump()
    return _call(accounting.add_customer, conn, key, fields.pop("name"), **fields)


@router.get("/customers/{customer_id}")
def get_customer(customer_id: int, conn=Depends(get_conn)):
    return _call(accounting.customer, conn, customer_id)


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: int, body: PartyChanges, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.update_customer, conn, customer_id, body.changes, key)


@router.get("/suppliers")
def list_suppliers(active_only: bool = False, conn=Depends(get_conn)):
    return _call(accounting.suppliers, conn, active_only=active_only)


@router.post("/suppliers", status_code=201)
def add_supplier(body: NewParty, key: str = Depends(_key), conn=Depends(get_conn)):
    fields = body.model_dump()
    return _call(accounting.add_supplier, conn, key, fields.pop("name"), **fields)


@router.get("/suppliers/{supplier_id}")
def get_supplier(supplier_id: int, conn=Depends(get_conn)):
    return _call(accounting.supplier, conn, supplier_id)


@router.patch("/suppliers/{supplier_id}")
def update_supplier(supplier_id: int, body: PartyChanges, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.update_supplier, conn, supplier_id, body.changes, key)


# ---------------------------------------------------------------- invoices

@router.get("/invoices")
def list_invoices(customer_id: Optional[int] = None, status: Optional[Status] = None,
                  as_of: Optional[str] = Query(None, max_length=10), open_only: bool = False,
                  conn=Depends(get_conn)):
    return _call(accounting.invoices, conn, customer_id=customer_id, status=status, as_of=as_of,
                 open_only=open_only)


@router.post("/invoices", status_code=201)
def create_invoice(body: NewInvoice, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.create_invoice, conn, key, body.customer_id, body.invoice_number, body.issue_date,
                 _lines(body.lines), due_date=body.due_date, terms=body.terms, notes=body.notes,
                 fund_id=body.fund_id)


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, as_of: Optional[str] = Query(None, max_length=10), conn=Depends(get_conn)):
    return _call(accounting.invoice, conn, invoice_id, as_of=as_of)


@router.post("/invoices/{invoice_id}/send")
def send_invoice(invoice_id: int, body: Optional[Posting] = None, key: str = Depends(_key),
                 conn=Depends(get_conn)):
    body = body or _NO_POSTING
    return _call(accounting.send_invoice, conn, invoice_id, key, entry_date=body.entry_date)


@router.post("/invoices/{invoice_id}/payments", status_code=201)
def record_invoice_payment(invoice_id: int, body: NewPayment, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.record_invoice_payment, conn, invoice_id, body.amount_cents, body.payment_date,
                 body.account_id, key, reference=body.reference)


@router.post("/invoices/{invoice_id}/void")
def void_invoice(invoice_id: int, body: Void, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.void_invoice, conn, invoice_id, body.reason, key, entry_date=body.entry_date)


# ---------------------------------------------------------------- bills

@router.get("/bills")
def list_bills(supplier_id: Optional[int] = None, status: Optional[Status] = None,
               as_of: Optional[str] = Query(None, max_length=10), open_only: bool = False,
               conn=Depends(get_conn)):
    return _call(accounting.bills, conn, supplier_id=supplier_id, status=status, as_of=as_of,
                 open_only=open_only)


@router.post("/bills", status_code=201)
def create_bill(body: NewBill, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.create_bill, conn, key, body.supplier_id, body.bill_number, body.issue_date,
                 _lines(body.lines), due_date=body.due_date, terms=body.terms, notes=body.notes,
                 fund_id=body.fund_id)


@router.get("/bills/{bill_id}")
def get_bill(bill_id: int, as_of: Optional[str] = Query(None, max_length=10), conn=Depends(get_conn)):
    return _call(accounting.bill, conn, bill_id, as_of=as_of)


@router.post("/bills/{bill_id}/receive")
def receive_bill(bill_id: int, body: Optional[Posting] = None, key: str = Depends(_key), conn=Depends(get_conn)):
    body = body or _NO_POSTING
    return _call(accounting.receive_bill, conn, bill_id, key, entry_date=body.entry_date)


@router.post("/bills/{bill_id}/payments", status_code=201)
def record_bill_payment(bill_id: int, body: NewPayment, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.record_bill_payment, conn, bill_id, body.amount_cents, body.payment_date,
                 body.account_id, key, reference=body.reference)


@router.post("/bills/{bill_id}/void")
def void_bill(bill_id: int, body: Void, key: str = Depends(_key), conn=Depends(get_conn)):
    return _call(accounting.void_bill, conn, bill_id, body.reason, key, entry_date=body.entry_date)


# ---------------------------------------------------------------- reports

@router.get("/reports/trial-balance")
def trial_balance(as_of: Optional[str] = Query(None, max_length=10), include_empty: bool = False,
                  fund_id: Optional[int] = None, conn=Depends(get_conn)):
    return _call(accounting.trial_balance, conn, as_of=as_of, include_empty=include_empty, fund_id=fund_id)


@router.get("/reports/profit-and-loss")
def profit_and_loss(from_date: str = Query(..., max_length=10), to_date: str = Query(..., max_length=10),
                    fund_id: Optional[int] = None, conn=Depends(get_conn)):
    return _call(accounting.profit_and_loss, conn, from_date, to_date, fund_id=fund_id)


@router.get("/reports/balance-sheet")
def balance_sheet(as_of: Optional[str] = Query(None, max_length=10), fund_id: Optional[int] = None,
                  conn=Depends(get_conn)):
    return _call(accounting.balance_sheet, conn, as_of=as_of, fund_id=fund_id)


@router.get("/reports/fund-balances")
def fund_balances(as_of: Optional[str] = Query(None, max_length=10), conn=Depends(get_conn)):
    return _call(accounting.fund_balances, conn, as_of=as_of)


@router.get("/reports/statement-of-activities")
def statement_of_activities(from_date: str = Query(..., max_length=10), to_date: str = Query(..., max_length=10),
                            conn=Depends(get_conn)):
    return _call(accounting.statement_of_activities, conn, from_date, to_date)


@router.get("/reports/ar-aging")
def ar_aging(as_of: Optional[str] = Query(None, max_length=10), conn=Depends(get_conn)):
    return _call(accounting.ar_aging, conn, as_of=as_of)


@router.get("/reports/ap-aging")
def ap_aging(as_of: Optional[str] = Query(None, max_length=10), conn=Depends(get_conn)):
    return _call(accounting.ap_aging, conn, as_of=as_of)


@router.get("/schema")
def schema(conn=Depends(get_conn)):
    return {"module": accounting.MODULE, "version": accounting.schema_version(conn)}


# Local-only authenticated fund controls. Ordinary ledger endpoints remain a
# development surface; this does not provide core CRM/business isolation.
control_auth = _sibling('accounting_control_auth', 'control_auth.py')


def _local_controls():
    if os.environ.get('APP_ENV', '').lower() == 'production':
        raise HTTPException(404, 'Not available')


class ControlLogin(_Body):
    username: Annotated[str, Field(min_length=1, max_length=160)]
    password: Annotated[str, Field(min_length=12, max_length=256, repr=False)]


class CashMapping(_Body):
    account_ids: list[int] = Field(min_length=1, max_length=100)
    reason: LongText


class ConstraintChange(_Body):
    enabled: bool
    reason: LongText


def _control_token(authorization: str | None = Header(None, max_length=512)):
    _local_controls()
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Login required')
    return authorization[7:]


def _control_user(token=Depends(_control_token), conn=Depends(get_conn)):
    try:
        user = control_auth.principal(conn, token)
    except control_auth.Unauthorized:
        raise HTTPException(401, 'Login required') from None
    except sqlite3.DatabaseError as e:
        raise HTTPException(503, f'the accounting database cannot be used: {e}') from None
    return user


def _control_admin(user=Depends(_control_user)):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Administrator required')
    return user


async def _control_credentials(request: Request):
    # FastAPI's default validation errors include rejected input values. Never
    # return a supplied password, including on malformed/extra-field requests.
    try:
        return ControlLogin.model_validate(await request.json())
    except (ValueError, TypeError):
        raise HTTPException(401, 'Invalid credentials') from None


@router.post('/controls/login', dependencies=[Depends(_local_controls)])
def control_login(body=Depends(_control_credentials), conn=Depends(get_conn)):
    from fastapi.responses import JSONResponse
    try:
        credentials = control_auth.login(conn, body.username, body.password)
    except control_auth.Unauthorized:
        raise HTTPException(401, 'Invalid credentials') from None
    except sqlite3.DatabaseError as e:
        raise HTTPException(503, f'the accounting database cannot be used: {e}') from None
    return JSONResponse(credentials, headers={'Cache-Control': 'no-store'})


@router.post('/controls/logout', dependencies=[Depends(_local_controls)])
def control_logout(token=Depends(_control_token), conn=Depends(get_conn)):
    _call(control_auth.logout, conn, token)
    return {'logged_out': True}


@router.get('/funds/{fund_id}/controls')
def get_fund_controls(fund_id: int, user=Depends(_control_user), conn=Depends(get_conn)):
    return _call(accounting.fund_controls, conn, fund_id)


@router.get('/funds/{fund_id}/controls/audit')
def get_fund_control_audit(fund_id: int, user=Depends(_control_user), conn=Depends(get_conn)):
    return _call(accounting.fund_constraint_audit, conn, fund_id)


@router.post('/funds/{fund_id}/controls/cash-accounts')
def configure_cash(fund_id: int, body: CashMapping, user=Depends(_control_admin),
                   key=Depends(_key), conn=Depends(get_conn)):
    actor = f"control-user:{user['id']}:{user['username']}"
    return _call(accounting.configure_fund_cash_accounts, conn, key, fund_id,
                 body.account_ids, actor, body.reason)


@router.post('/funds/{fund_id}/controls/{constraint}')
def change_constraint(fund_id: int, constraint: Literal['cash', 'capacity'], body: ConstraintChange,
                      user=Depends(_control_admin), key=Depends(_key), conn=Depends(get_conn)):
    actor = f"control-user:{user['id']}:{user['username']}"
    return _call(accounting.set_fund_constraint, conn, key, fund_id, constraint,
                 body.enabled, actor, body.reason)

# Draft module-binding preflight

A standalone, local-only compatibility checker for a proposed module's logical
data requirements and an explicit business-schema mapping. It is a review aid,
not an agreed OperationsNerd module interface, an adapter, or integration code.
It imports no core code and changes no routes, agent operations or databases.

The architecture discussion leaves the module interface and shared core data
open. This draft makes mapping gaps reviewable without picking real company,
entity or account mappings. It does not move LogisticsNerd into OperationsNerd.

## Run the fictional example

From the repository root with Python 3.10+ (standard library only):

```sh
python modules/contracts/preflight.py \
  --requirements modules/contracts/examples/requirements.json \
  --snapshot modules/contracts/examples/snapshot.json \
  --binding modules/contracts/examples/binding.json \
  --account-id account-A --business-id business-A --report /tmp/binding-report.json
python modules/contracts/tests/test_preflight.py
```

Choose a writable report path on your platform; its directory must exist.
The bundled data is fictional and has no real customer/stock/price information.
Do not run a model, provision a database or install packages for this tool.

## Three explicit inputs

All use `draft_version: binding-preflight-1`. Unknown keys and duplicate JSON
object keys are refused. Identifiers and revisions are nonempty trimmed strings;
converting a real system's IDs is deliberately left to a future adapter.

1. **requirements**: a module name plus logical entity roles and required field
   roles. A field declares one of `text`, `integer`, `number`, `boolean`, `date`,
   `link`, and a `target` logical role for links (otherwise null).
2. **snapshot**: asserted account/business/revision plus named entities, each
   with its asserted scope, boolean archived flag and typed fields. Link targets
   name actual entities. This is a draft input format, not the CRM's live schema
   format. There is no schema export, database read or authenticated source.
3. **binding**: module/account/business/revision and an explicit mapping from
   each logical entity/field role to actual entity/field names in that snapshot.

The caller supplies the expected account and business separately. The checker
requires both inputs to match that scope, and selected entities to match it too.
It checks revision equality, every required entity/field, exact field types,
archived flags and link targets. Extra role mappings and aliases (two roles
mapped to the same entity, or two field roles to one field) are refused in this
draft. Extra schema entities/fields are allowed and ignored if not selected.
Partial nonempty mappings report missing roles/fields as issues, never filled
by similar names. Empty `entities` maps (requirements, snapshot or binding) and
empty per-entity `fields` maps are structurally invalid: they raise
`ContractError` in the Python API and exit 2 in the CLI, rather than producing
missing-role issues or a compatibility report. Empty input leaves any existing
report unchanged. Do not confuse an old report with a successful current run. Multiple semantic
issues are collected in deterministic order. Structurally malformed inputs stop
before any compatibility claim is written.

## Report and exit status

- 0: supplied mapping is compatible with the supplied snapshot and requirements.
- 1: well-formed inputs, but compatibility issues exist.
- 2: invalid inputs, same source/output path, or report I/O failure.

The report contains compact issue codes and logical role names, module/revision,
and canonical sorted-JSON hashes of the three parsed inputs. Whitespace and key
order do not change hashes. They are not byte hashes, signatures or provenance.
Account/business values, actual entity names, source paths and record values
are omitted, but role names/module/revision/hashes can still be private. Review
the audience before sharing a report.

The CLI atomically replaces its report for exit 0/1. Invalid input leaves an old
report unchanged, so always inspect the exit status and hashes. It refuses an
output resolving to a source. Other existing reports are replaced. One writer
per report; atomic rename is not a durability or race-proof storage guarantee.
Files are read into memory without a size limit.

## What this does not prove

Scope strings are caller assertions, not identity checks. A fabricated snapshot
can pass, and a genuine one can go stale immediately. Never use this output as
an authorization token or a reason to skip live schema/owner validation.

There are no record reads/writes, workflow execution, approval, permissions,
transactions, migrations, retries, idempotency, reservations, orders, payments,
stock checks, accounting postings or company-isolation guarantees. Passing type
checks cannot prove business meaning or that a module's workflow is correct.
Real adapters, agreed I/O, business mapping and the revised integration
architecture remain open decisions. Existing production gates remain intact.

## Test integration

`python scripts/run_tests.py` now includes the standalone contract test scripts
under `modules/contracts/tests` as well as the core scripts. Contract scripts
run from the repository root; core scripts retain their application working
directory. Filename filters apply to both groups. The existing GitHub Actions
job invokes this runner, so it includes the contract tests without an extra
job or dependency. The live-credit `test_llm_smoke.py` remains excluded.

Review follow-up validation: 12 new runner-discovery/working-directory/failure
propagation cases (old runner: 5 fail/7 pass) and 6 explicit empty-map API cases
pass. The existing 39 contract cases still pass. The updated full runner passes
25/25 scripts: 23 core scripts including the runner regression, plus 2 contract
scripts. No live-model smoke test is enabled. This is local qualification, not
a claim that the hosted CI job has already completed.

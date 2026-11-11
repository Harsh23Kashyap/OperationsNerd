"""Draft standalone module binding preflight. No core imports or effects."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

TYPES = {'text', 'integer', 'number', 'boolean', 'date', 'link'}


class ContractError(ValueError):
    pass


def _obj(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ContractError(where + ': incorrect object keys')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContractError('expected nonempty trimmed identifier')
    return value


def _map(value):
    if not isinstance(value, dict) or not value:
        raise ContractError('expected nonempty mapping')
    for key in value:
        _text(key)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def preflight(requirements, snapshot, binding, *, account_id, business_id):
    """Check an explicit local binding against a supplied schema snapshot.

    Caller scope and snapshot ownership are assertions, not authentication.
    This never authorizes reads/writes, registers modules or executes workflows.
    """
    _text(account_id); _text(business_id)
    _obj(requirements, ('draft_version', 'module', 'entities'), 'requirements')
    _obj(snapshot, ('draft_version','account_id','business_id','revision','entities'), 'snapshot')
    _obj(binding, ('draft_version','module','account_id','business_id','revision','entities'), 'binding')
    for data in (requirements, snapshot, binding):
        if data['draft_version'] != 'binding-preflight-1':
            raise ContractError('unsupported draft version')
    _text(requirements['module']); _text(binding['module'])
    for data in (snapshot, binding):
        for key in ('account_id','business_id','revision'):
            _text(data[key])
    reqs = _map(requirements['entities']); schemas = _map(snapshot['entities']); mappings = _map(binding['entities'])
    for role, req in reqs.items():
        _obj(req, ('fields',), 'requirement entity')
        for field_role, field in _map(req['fields']).items():
            _obj(field, ('type','target'), 'requirement field')
            if field['type'] not in TYPES:
                raise ContractError('unknown required field type')
            if field['type'] == 'link':
                if field['target'] not in reqs:
                    raise ContractError('link target must name a required role')
            elif field['target'] is not None:
                raise ContractError('only link fields have targets')
    for entity, schema in schemas.items():
        _obj(schema, ('account_id','business_id','archived','fields'), 'schema entity')
        _text(schema['account_id']); _text(schema['business_id'])
        if type(schema['archived']) is not bool:
            raise ContractError('archived must be boolean')
        for field_name, field in _map(schema['fields']).items():
            _obj(field, ('type','target','archived'), 'schema field')
            if field['type'] not in TYPES or type(field['archived']) is not bool:
                raise ContractError('invalid schema field')
            if field['type'] == 'link':
                _text(field['target'])
            elif field['target'] is not None:
                raise ContractError('only link fields have targets')
    for role, mapping in mappings.items():
        _obj(mapping, ('entity','fields'), 'entity binding')
        _text(mapping['entity'])
        for key, value in _map(mapping['fields']).items():
            _text(value)
    issues = []
    def issue(code, role=None, field=None):
        item = {'code': code}
        if role is not None: item['role'] = role
        if field is not None: item['field'] = field
        issues.append(item)
    for name, data in (('snapshot',snapshot),('binding',binding)):
        if data['account_id'] != account_id or data['business_id'] != business_id:
            issue(name + '_scope_mismatch')
    if binding['module'] != requirements['module']: issue('module_mismatch')
    if binding['revision'] != snapshot['revision']: issue('stale_binding')
    for role in sorted(set(mappings) - set(reqs)): issue('unexpected_role',role)
    for role in sorted(reqs):
        mapping = mappings.get(role)
        if mapping is None:
            issue('missing_role',role); continue
        entity = schemas.get(mapping['entity'])
        if entity is None:
            issue('missing_entity',role); continue
        if entity['account_id'] != account_id or entity['business_id'] != business_id:
            issue('entity_scope_mismatch',role)
        if entity['archived']: issue('archived_entity',role)
        fields = mapping['fields']; required = reqs[role]['fields']
        for key in sorted(set(fields) - set(required)): issue('unexpected_field_role',role,key)
        if len(set(fields.values())) != len(fields): issue('aliased_fields',role)
        for key in sorted(required):
            if key not in fields:
                issue('missing_field_role',role,key); continue
            actual = entity['fields'].get(fields[key])
            if actual is None:
                issue('missing_field',role,key); continue
            if actual['archived']: issue('archived_field',role,key)
            if actual['type'] != required[key]['type']: issue('field_type_mismatch',role,key)
            if required[key]['type'] == 'link':
                target = mappings.get(required[key]['target'])
                if target is None or actual['target'] != target['entity']:
                    issue('link_target_mismatch',role,key)
    names = [m['entity'] for m in mappings.values()]
    if len(set(names)) != len(names): issue('aliased_entities')
    hashes = {name: hashlib.sha256(canonical(data)).hexdigest() for name,data in
              (('requirements',requirements),('snapshot',snapshot),('binding',binding))}
    return {'draft_version':'binding-preflight-1', 'compatible':not issues,
            'module':requirements['module'], 'revision':snapshot['revision'],
            'source_sha256':hashes, 'issues':issues,
            'limits':['Local schema consistency only, not authentication or authorization.',
                      'Supplied snapshot can be stale or false. Recheck live schema before effects.',
                      'No record values, workflows, migrations, stock, orders or accounting are checked.',
                      'Draft format for review, not an agreed OperationsNerd module interface.']}


def read_json(path):
    def pairs(items):
        result = {}
        for k,v in items:
            if k in result: raise ContractError('duplicate JSON key')
            result[k] = v
        return result
    def constant(_): raise ContractError('nonfinite JSON')
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('requirements','snapshot','binding','report'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--account-id',required=True)
    parser.add_argument('--business-id',required=True)
    args = parser.parse_args(argv)
    try:
        sources = [args.requirements,args.snapshot,args.binding]
        resolved = {p.resolve() for p in sources}
        if len(resolved)!=3 or args.report.resolve() in resolved:
            raise ContractError('distinct input/output paths required')
        result = preflight(*(read_json(p) for p in sources), account_id=args.account_id,business_id=args.business_id)
        raw = canonical(result) + b'\n'
        with tempfile.NamedTemporaryFile('wb',dir=args.report.parent,delete=False) as out:
            temporary = Path(out.name)
            out.write(raw)
        try: os.replace(temporary,args.report)
        finally: temporary.unlink(missing_ok=True)
    except (OSError,ValueError,TypeError,UnicodeError):
        print('Invalid contract input or report I/O. No adapter was run.')
        return 2
    print('compatible=' + str(result['compatible']) + ' issues=' + str(len(result['issues'])))
    return 0 if result['compatible'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
